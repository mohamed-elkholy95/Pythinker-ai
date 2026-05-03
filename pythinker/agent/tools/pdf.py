"""PDF report generation tool — renders structured Markdown to a styled PDF.

Design notes:
    The styling target is a restrained, professional research-report look —
    monochrome palette, generous typography, thin rules under section
    headings, faint gray pills for inline code, simple bordered tables,
    italic blockquotes with a left bar, justified body text. The reference
    visual is the Obscura research report layout under
    ``blackbox/Research Report on Obscura Open-Source Agentic Browser/``.

    Markdown subset honoured (chosen to match what that report uses):

    - ``#`` cover title (rendered from the ``title`` parameter; a leading
      ``# `` line in the body is also tolerated and folded in)
    - ``## `` numbered section heading (e.g. ``## 1. Executive Summary``) —
      gets a thin horizontal rule below
    - ``### `` numbered subsection heading
    - ``- `` / ``* `` bullets and ``1. `` / ``2. `` numbered list items
    - ``> `` blockquotes (italic, left bar)
    - ``---`` horizontal rule on its own line
    - GitHub-flavored tables (``| a | b |\n| --- | --- |\n| 1 | 2 |``)
    - inline ``**bold**`` / ``*italic*`` / `` `code` `` (code renders as a
      faint gray pill in monospace)
    - inline links ``[text](url)``
    - document metadata block: consecutive lines of the form
      ``**Date:** April 30, 2026`` directly under the cover title render
      as a tight key/value block surrounded by thin rules
"""

# ReportLab is an optional dependency — install via the ``[reports]`` extra
# (``pip install 'pythinker-ai[reports]'``). The tool surfaces an actionable
# install hint when the import fails so the agent can fall back to inline
# text rather than crashing the turn.

from __future__ import annotations

import platform
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Union

from pythinker.agent.tools.base import Tool, tool_parameters
from pythinker.agent.tools.filesystem import _resolve_path
from pythinker.agent.tools.schema import StringSchema, tool_parameters_schema

_INSTALL_HINT = (
    "reportlab is not installed. Install with `pip install reportlab` "
    "(or `pip install 'pythinker-ai[reports]'`) and retry."
)

_BOLD = re.compile(r"\*\*([^*]+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_CODE = re.compile(r"`([^`\n]+?)`")
_LINK = re.compile(r"\[([^\]]+?)\]\(([^)\s]+?)\)")
_NUMBERED = re.compile(r"^(\d+)\.\s+(.*)")
_META_LINE = re.compile(r"^\*\*([^*][^*]*?):\*\*\s*(.+)$")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


# --------------------------------------------------------------------------
# Block tokens
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Title:
    text: str


@dataclass(frozen=True)
class MetaBlock:
    rows: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Section:
    text: str


@dataclass(frozen=True)
class Subsection:
    text: str


@dataclass(frozen=True)
class Body:
    text: str


@dataclass(frozen=True)
class BulletItem:
    text: str


@dataclass(frozen=True)
class NumberedItem:
    number: int
    text: str


@dataclass(frozen=True)
class Quote:
    text: str


@dataclass(frozen=True)
class HRule:
    pass


@dataclass(frozen=True)
class TableBlock:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...] = field(default_factory=tuple)


Block = Union[
    Title, MetaBlock, Section, Subsection, Body, BulletItem,
    NumberedItem, Quote, HRule, TableBlock,
]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _slugify(text: str, *, max_len: int = 60) -> str:
    """Convert a title into a safe filename slug."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", text or "report").strip("_")
    return (slug[:max_len] or "report").lower()


def _inline_md(text: str, *, mono_font: str | None = None) -> str:
    """Convert inline markdown to ReportLab's mini-XML subset.

    Order matters: code spans are stashed first so their contents are not
    re-interpreted as bold / italic / links. Links are rendered as
    ``<link href='...'>...</link>`` with an underline tag for contrast.
    Inline code uses ``mono_font`` (the resolved monospace family,
    typically Liberation Mono on Linux) so the look matches the
    reference Obscura PDF rather than ReportLab's built-in Courier.
    """
    if mono_font is None:
        mono_font = _resolve_font_family().mono
    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    code_spans: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        body = match.group(1)
        code_spans.append(
            f"<font face='{mono_font}' size='9.5' backColor='#F1F2F4'>"
            f"&nbsp;{body}&nbsp;</font>"
        )
        return f"__PYTHINKER_CODE_SPAN_{len(code_spans) - 1}__"

    safe = _CODE.sub(_stash_code, safe)
    safe = _BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", safe)
    safe = _ITALIC.sub(lambda m: f"<i>{m.group(1)}</i>", safe)
    safe = _LINK.sub(
        lambda m: (
            f"<link href='{m.group(2)}' color='#1F2937'>"
            f"<u>{m.group(1)}</u></link>"
        ),
        safe,
    )
    for idx, span in enumerate(code_spans):
        safe = safe.replace(f"__PYTHINKER_CODE_SPAN_{idx}__", span)
    return safe


def _split_table_row(line: str) -> list[str]:
    """``| a | b | c |`` → ``["a", "b", "c"]``."""
    cells = line.strip()
    if cells.startswith("|"):
        cells = cells[1:]
    if cells.endswith("|"):
        cells = cells[:-1]
    return [c.strip() for c in cells.split("|")]


def _consume_meta_block(lines: list[str], start: int) -> tuple[MetaBlock | None, int]:
    """Try to read a Date/Subject/Status-style metadata block.

    A metadata block is two or more consecutive lines, each matching
    ``**Key:** value``. Returns the parsed block (or ``None``) and the
    index after it.
    """
    rows: list[tuple[str, str]] = []
    i = start
    while i < len(lines):
        match = _META_LINE.match(lines[i].strip())
        if not match:
            break
        rows.append((match.group(1).strip(), match.group(2).strip()))
        i += 1
    if len(rows) >= 2:
        return MetaBlock(tuple(rows)), i
    return None, start


def _markdown_to_blocks(text: str) -> list[Block]:
    """Tokenize markdown into typed blocks (see Block union)."""
    lines = text.splitlines()
    out: list[Block] = []
    pending: list[str] = []
    quote_pending: list[str] = []

    def flush_paragraph() -> None:
        if pending:
            joined = " ".join(pending).strip()
            if joined:
                out.append(Body(joined))
            pending.clear()

    def flush_quote() -> None:
        if quote_pending:
            out.append(Quote(" ".join(quote_pending).strip()))
            quote_pending.clear()

    seen_first_heading = False
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        # Blank line breaks paragraphs / quotes.
        if not stripped:
            flush_paragraph()
            flush_quote()
            i += 1
            continue

        # Headings.
        if stripped.startswith("# "):
            flush_paragraph()
            flush_quote()
            out.append(Title(stripped[2:].strip()))
            seen_first_heading = True
            # After a Title, opportunistically consume a metadata block.
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            meta, after = _consume_meta_block(lines, j)
            if meta is not None:
                out.append(meta)
                i = after
                continue
            i += 1
            continue

        if stripped.startswith("## "):
            flush_paragraph()
            flush_quote()
            out.append(Section(stripped[3:].strip()))
            seen_first_heading = True
            i += 1
            continue

        if stripped.startswith("### "):
            flush_paragraph()
            flush_quote()
            out.append(Subsection(stripped[4:].strip()))
            seen_first_heading = True
            i += 1
            continue

        # Document-level metadata block before the first heading
        # (e.g. when the cover title is supplied via ``title=`` and the
        # body opens directly with ``**Date:** …``).
        if not seen_first_heading and _META_LINE.match(stripped):
            meta, after = _consume_meta_block(lines, i)
            if meta is not None:
                flush_paragraph()
                flush_quote()
                out.append(meta)
                i = after
                continue

        # Horizontal rule.
        if stripped in {"---", "***", "___"}:
            flush_paragraph()
            flush_quote()
            out.append(HRule())
            i += 1
            continue

        # Tables: header line followed by an alignment separator line.
        if "|" in stripped and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            flush_paragraph()
            flush_quote()
            headers = tuple(_split_table_row(lines[i]))
            rows: list[tuple[str, ...]] = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(tuple(_split_table_row(lines[j])))
                j += 1
            out.append(TableBlock(headers=headers, rows=tuple(rows)))
            i = j
            continue

        # Blockquote.
        if stripped.startswith(">"):
            flush_paragraph()
            quote_pending.append(stripped[1:].lstrip())
            i += 1
            continue

        # Bullets.
        if stripped.startswith(("- ", "* ")):
            flush_paragraph()
            flush_quote()
            out.append(BulletItem(stripped[2:].strip()))
            i += 1
            continue

        # Numbered list items.
        match = _NUMBERED.match(stripped)
        if match:
            flush_paragraph()
            flush_quote()
            out.append(NumberedItem(int(match.group(1)), match.group(2).strip()))
            i += 1
            continue

        # Body paragraph (continuation lines coalesce).
        flush_quote()
        pending.append(stripped)
        i += 1

    flush_paragraph()
    flush_quote()
    return out


# --------------------------------------------------------------------------
# Font resolution — match the Obscura reference's humanist-sans look
# --------------------------------------------------------------------------

# (family, base_dir, regular_filename, bold, italic, bold_italic)
_SANS_CANDIDATES: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("NotoSans", "/usr/share/fonts/google-noto",
     "NotoSans-Regular.ttf", "NotoSans-Bold.ttf",
     "NotoSans-Italic.ttf", "NotoSans-BoldItalic.ttf"),
    ("NotoSans", "/usr/share/fonts/noto",
     "NotoSans-Regular.ttf", "NotoSans-Bold.ttf",
     "NotoSans-Italic.ttf", "NotoSans-BoldItalic.ttf"),
    ("SourceSans3", "/usr/share/fonts/adobe-source-sans-3",
     "SourceSans3-Regular.otf", "SourceSans3-Bold.otf",
     "SourceSans3-Italic.otf", "SourceSans3-BoldItalic.otf"),
    ("SourceSansPro", "/usr/share/fonts/adobe-source-sans-pro",
     "SourceSansPro-Regular.otf", "SourceSansPro-Bold.otf",
     "SourceSansPro-It.otf", "SourceSansPro-BoldIt.otf"),
    ("OpenSans", "/usr/share/fonts/open-sans",
     "OpenSans-Regular.ttf", "OpenSans-Bold.ttf",
     "OpenSans-Italic.ttf", "OpenSans-BoldItalic.ttf"),
    ("Lato", "/usr/share/fonts/lato",
     "Lato-Regular.ttf", "Lato-Bold.ttf",
     "Lato-Italic.ttf", "Lato-BoldItalic.ttf"),
    ("LiberationSans", "/usr/share/fonts/liberation-sans-fonts",
     "LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf",
     "LiberationSans-Italic.ttf", "LiberationSans-BoldItalic.ttf"),
    ("LiberationSans", "/usr/share/fonts/liberation/sans",
     "LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf",
     "LiberationSans-Italic.ttf", "LiberationSans-BoldItalic.ttf"),
    ("DejaVuSans", "/usr/share/fonts/dejavu-sans-fonts",
     "DejaVuSans.ttf", "DejaVuSans-Bold.ttf",
     "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf"),
    ("DejaVuSans", "/usr/share/fonts/truetype/dejavu",
     "DejaVuSans.ttf", "DejaVuSans-Bold.ttf",
     "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf"),
    # macOS
    ("Helvetica Neue", "/System/Library/Fonts",
     "HelveticaNeue.ttc", "HelveticaNeue.ttc",
     "HelveticaNeue.ttc", "HelveticaNeue.ttc"),
    # Windows fallbacks
    ("SegoeUI", "C:/Windows/Fonts",
     "segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"),
)

_MONO_CANDIDATES: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("LiberationMono", "/usr/share/fonts/liberation-mono-fonts",
     "LiberationMono-Regular.ttf", "LiberationMono-Bold.ttf",
     "LiberationMono-Italic.ttf", "LiberationMono-BoldItalic.ttf"),
    ("LiberationMono", "/usr/share/fonts/liberation/mono",
     "LiberationMono-Regular.ttf", "LiberationMono-Bold.ttf",
     "LiberationMono-Italic.ttf", "LiberationMono-BoldItalic.ttf"),
    ("DejaVuSansMono", "/usr/share/fonts/dejavu-sans-mono-fonts",
     "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf",
     "DejaVuSansMono-Oblique.ttf", "DejaVuSansMono-BoldOblique.ttf"),
    ("DejaVuSansMono", "/usr/share/fonts/truetype/dejavu",
     "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf",
     "DejaVuSansMono-Oblique.ttf", "DejaVuSansMono-BoldOblique.ttf"),
    ("SourceCodePro", "/usr/share/fonts/adobe-source-code-pro",
     "SourceCodePro-Regular.otf", "SourceCodePro-Bold.otf",
     "SourceCodePro-It.otf", "SourceCodePro-BoldIt.otf"),
)


@dataclass(frozen=True)
class FontFamily:
    """A resolved font family ready to hand to ReportLab styles."""

    sans: str = "Helvetica"
    sans_bold: str = "Helvetica-Bold"
    sans_italic: str = "Helvetica-Oblique"
    sans_bold_italic: str = "Helvetica-BoldOblique"
    mono: str = "Courier"
    label: str = "(builtin Helvetica/Courier)"


# Module-level cache so we only register fonts with ReportLab once per process.
_FONT_FAMILY_CACHE: FontFamily | None = None


def _try_register_family(
    candidates: tuple[tuple[str, str, str, str, str, str], ...],
    role: str,
) -> tuple[str, str, str, str] | None:
    """Walk ``candidates`` and register the first family whose 4 files exist.

    Returns ``(regular, bold, italic, bold_italic)`` registered names, or
    ``None`` if no full family was found.
    """
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        return None

    for family, base, reg, bold, italic, bold_italic in candidates:
        base_path = Path(base)
        files = [base_path / fn for fn in (reg, bold, italic, bold_italic)]
        if not all(p.exists() for p in files):
            continue
        names = (
            f"{family}-{role}-Regular",
            f"{family}-{role}-Bold",
            f"{family}-{role}-Italic",
            f"{family}-{role}-BoldItalic",
        )
        try:
            for name, path in zip(names, files):
                pdfmetrics.registerFont(TTFont(name, str(path)))
            try:
                pdfmetrics.registerFontFamily(
                    names[0],
                    normal=names[0],
                    bold=names[1],
                    italic=names[2],
                    boldItalic=names[3],
                )
            except Exception:  # noqa: BLE001 — font family registration is best-effort
                pass
            return names
        except Exception:  # noqa: BLE001 — try the next candidate on any failure
            continue
    return None


def _resolve_font_family() -> FontFamily:
    """Resolve a humanist-sans + mono pair, registering both with ReportLab.

    The result is cached per process. On platforms where no candidate
    family exists (e.g. minimal Docker images), this returns the
    built-in Helvetica/Courier defaults so the tool keeps working.
    """
    global _FONT_FAMILY_CACHE
    if _FONT_FAMILY_CACHE is not None:
        return _FONT_FAMILY_CACHE

    sans_quad = _try_register_family(_SANS_CANDIDATES, "Sans")
    mono_quad = _try_register_family(_MONO_CANDIDATES, "Mono")
    if sans_quad is None and mono_quad is None:
        _FONT_FAMILY_CACHE = FontFamily()
        return _FONT_FAMILY_CACHE

    sans = sans_quad or (
        "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"
    )
    mono = (mono_quad[0] if mono_quad else "Courier")
    sans_label = sans[0].split("-")[0]
    mono_label = mono.split("-")[0] if mono_quad else "Courier"
    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    label = f"sans={sans_label}, mono={mono_label} (platform={platform.system().lower()}, py={py})"
    _FONT_FAMILY_CACHE = FontFamily(
        sans=sans[0],
        sans_bold=sans[1],
        sans_italic=sans[2],
        sans_bold_italic=sans[3],
        mono=mono,
        label=label,
    )
    return _FONT_FAMILY_CACHE


# --------------------------------------------------------------------------
# Theme + styles
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PdfTheme:
    """Restrained, professional research-report palette.

    Inspired by the Obscura research-report layout referenced in the
    module docstring — monochrome, near-black on white with light gray
    accents for rules and code pills.
    """

    text_primary: str = "#111827"
    text_muted: str = "#4B5563"
    rule_strong: str = "#9CA3AF"
    rule_subtle: str = "#D1D5DB"
    code_bg: str = "#F1F2F4"
    table_header_bg: str = "#F3F4F6"
    quote_bar: str = "#9CA3AF"
    link: str = "#1F2937"


def _build_theme() -> PdfTheme:
    return PdfTheme()


def _build_styles(theme: PdfTheme, fonts: FontFamily | None = None) -> dict[str, Any]:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    fonts = fonts or _resolve_font_family()
    ss = getSampleStyleSheet()
    text = colors.HexColor(theme.text_primary)
    muted = colors.HexColor(theme.text_muted)

    cover_style = ParagraphStyle(
        name="ReportCover",
        parent=ss["Title"],
        fontName=fonts.sans_bold,
        fontSize=24,
        leading=30,
        alignment=TA_LEFT,
        textColor=text,
        spaceBefore=0,
        spaceAfter=4,
    )
    meta_key_style = ParagraphStyle(
        name="ReportMetaKey",
        parent=ss["BodyText"],
        fontName=fonts.sans_bold,
        fontSize=10.5,
        leading=14,
        textColor=text,
        spaceAfter=0,
    )
    meta_value_style = ParagraphStyle(
        name="ReportMetaValue",
        parent=ss["BodyText"],
        fontName=fonts.sans,
        fontSize=10.5,
        leading=14,
        textColor=text,
        spaceAfter=2,
    )
    generated_style = ParagraphStyle(
        name="ReportGenerated",
        parent=ss["BodyText"],
        fontName=fonts.sans_italic,
        fontSize=8.5,
        leading=11,
        textColor=muted,
        spaceAfter=0,
    )
    title_style = ParagraphStyle(
        name="ReportEmbeddedTitle",
        parent=ss["Heading1"],
        fontName=fonts.sans_bold,
        fontSize=20,
        leading=26,
        textColor=text,
        spaceBefore=14,
        spaceAfter=4,
    )
    section_style = ParagraphStyle(
        name="ReportSection",
        parent=ss["Heading1"],
        fontName=fonts.sans_bold,
        fontSize=17,
        leading=22,
        textColor=text,
        spaceBefore=18,
        spaceAfter=2,
        keepWithNext=1,  # never orphan a section heading at page bottom
    )
    sub_style = ParagraphStyle(
        name="ReportSub",
        parent=ss["Heading2"],
        fontName=fonts.sans_bold,
        fontSize=13,
        leading=17,
        textColor=text,
        spaceBefore=12,
        spaceAfter=4,
        keepWithNext=1,
    )
    body_style = ParagraphStyle(
        name="ReportBody",
        parent=ss["BodyText"],
        fontName=fonts.sans,
        fontSize=10.5,
        leading=15.5,
        textColor=text,
        alignment=TA_JUSTIFY,
        spaceAfter=8,
    )
    bullet_style = ParagraphStyle(
        name="ReportBullet",
        parent=ss["BodyText"],
        fontName=fonts.sans,
        fontSize=10.5,
        leading=15,
        leftIndent=20,
        bulletIndent=8,
        textColor=text,
        spaceAfter=4,
    )
    numbered_style = ParagraphStyle(
        name="ReportNumbered",
        parent=ss["BodyText"],
        fontName=fonts.sans,
        fontSize=10.5,
        leading=15,
        leftIndent=22,
        bulletIndent=8,
        textColor=text,
        spaceAfter=4,
    )
    quote_style = ParagraphStyle(
        name="ReportQuote",
        parent=ss["BodyText"],
        fontName=fonts.sans_italic,
        fontSize=10.5,
        leading=15.5,
        leftIndent=14,
        rightIndent=4,
        textColor=text,
        spaceBefore=4,
        spaceAfter=10,
    )
    table_cell_style = ParagraphStyle(
        name="ReportTableCell",
        parent=ss["BodyText"],
        fontName=fonts.sans,
        fontSize=10,
        leading=13,
        textColor=text,
        spaceBefore=0,
        spaceAfter=0,
    )
    table_header_style = ParagraphStyle(
        name="ReportTableHeader",
        parent=ss["BodyText"],
        fontName=fonts.sans_bold,
        fontSize=10,
        leading=13,
        textColor=text,
        spaceBefore=0,
        spaceAfter=0,
    )
    return {
        "Cover": cover_style,
        "Meta": meta_value_style,            # alias — backwards-compat with tests
        "MetaKey": meta_key_style,
        "MetaValue": meta_value_style,
        "Generated": generated_style,
        "Title": title_style,
        "Section": section_style,
        "Subsection": sub_style,
        "Body": body_style,
        "Bullet": bullet_style,
        "Numbered": numbered_style,
        "Quote": quote_style,
        "TableCell": table_cell_style,
        "TableHeader": table_header_style,
    }


# --------------------------------------------------------------------------
# Story builder
# --------------------------------------------------------------------------


def _hr(theme: PdfTheme, *, thickness: float = 0.5,
        color_attr: str = "rule_subtle", before: float = 2, after: float = 8) -> Any:
    from reportlab.lib import colors
    from reportlab.platypus import HRFlowable

    return HRFlowable(
        width="100%",
        thickness=thickness,
        color=colors.HexColor(getattr(theme, color_attr)),
        spaceBefore=before,
        spaceAfter=after,
    )


def _meta_table(rows: tuple[tuple[str, str], ...], styles: dict[str, Any]) -> Any:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    # Two columns: bold key, regular value. No borders — let the
    # surrounding HRFlowable rules frame the block.
    data = [
        [
            Paragraph(f"{key}:", styles["MetaKey"]),
            Paragraph(_inline_md(value), styles["MetaValue"]),
        ]
        for key, value in rows
    ]
    table = Table(data, colWidths=[1.0 * 72, None])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#111827")),
            ]
        )
    )
    return table


def _data_table(block: TableBlock, theme: PdfTheme, styles: dict[str, Any]) -> Any:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    header_row = [Paragraph(_inline_md(h), styles["TableHeader"]) for h in block.headers]
    body_rows = [
        [Paragraph(_inline_md(cell), styles["TableCell"]) for cell in row]
        for row in block.rows
    ]
    data = [header_row, *body_rows]
    rule = colors.HexColor(theme.rule_subtle)
    header_bg = colors.HexColor(theme.table_header_bg)
    table = Table(data, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), header_bg),
                ("GRID", (0, 0), (-1, -1), 0.4, rule),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _quote_flowable(text: str, theme: PdfTheme, styles: dict[str, Any]) -> Any:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    para = Paragraph(_inline_md(text), styles["Quote"])
    quote_table = Table([[para]], colWidths=[None])
    quote_table.setStyle(
        TableStyle(
            [
                ("LINEBEFORE", (0, 0), (0, 0), 2.4, colors.HexColor(theme.quote_bar)),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return quote_table


def _build_story(
    title: str,
    body: str,
    generated_at: datetime,
    styles: dict[str, Any],
    theme: PdfTheme | None = None,
) -> list[Any]:
    """Assemble the ordered list of ReportLab Flowables for the document.

    The story always opens with: cover title, thick rule, optional
    metadata block (parsed from the body or absent), thin rule, then the
    body content. A muted ``Generated: …`` timestamp closes the document
    inline at the very end so the cover stays clean.
    """
    from reportlab.platypus import Paragraph, Spacer

    theme = theme or _build_theme()
    blocks = _markdown_to_blocks(body)

    # If the body opens with a leading ``# Title``, fold it into the cover.
    if blocks and isinstance(blocks[0], Title):
        if not title.strip():
            title = blocks[0].text
        blocks = blocks[1:]

    # Pull a leading metadata block out for the cover treatment.
    leading_meta: MetaBlock | None = None
    if blocks and isinstance(blocks[0], MetaBlock):
        leading_meta = blocks[0]
        blocks = blocks[1:]

    story: list[Any] = []
    story.append(Paragraph(_inline_md(title), styles["Cover"]))
    story.append(_hr(theme, thickness=2.0, color_attr="text_primary",
                     before=4, after=10))
    if leading_meta is not None:
        story.append(_meta_table(leading_meta.rows, styles))
        story.append(_hr(theme, thickness=0.5, before=10, after=14))
    else:
        story.append(Spacer(1, 6))

    for block in blocks:
        if isinstance(block, Title):
            # Body-embedded titles fall back to a smaller in-document title.
            story.append(Paragraph(_inline_md(block.text), styles["Title"]))
        elif isinstance(block, MetaBlock):
            story.append(_meta_table(block.rows, styles))
        elif isinstance(block, Section):
            from reportlab.platypus import KeepTogether
            story.append(
                KeepTogether(
                    [
                        Paragraph(_inline_md(block.text), styles["Section"]),
                        _hr(theme, thickness=0.5, before=2, after=10),
                    ]
                )
            )
        elif isinstance(block, Subsection):
            story.append(Paragraph(_inline_md(block.text), styles["Subsection"]))
        elif isinstance(block, Body):
            story.append(Paragraph(_inline_md(block.text), styles["Body"]))
        elif isinstance(block, BulletItem):
            story.append(
                Paragraph(f"•&nbsp;&nbsp;{_inline_md(block.text)}", styles["Bullet"])
            )
        elif isinstance(block, NumberedItem):
            story.append(
                Paragraph(
                    f"{block.number}.&nbsp;&nbsp;{_inline_md(block.text)}",
                    styles["Numbered"],
                )
            )
        elif isinstance(block, Quote):
            story.append(_quote_flowable(block.text, theme, styles))
        elif isinstance(block, HRule):
            story.append(_hr(theme, thickness=0.6, before=10, after=10))
        elif isinstance(block, TableBlock):
            story.append(_data_table(block, theme, styles))
            story.append(Spacer(1, 8))

    # Close with a muted generation timestamp + thin rule.
    story.append(_hr(theme, thickness=0.4, before=14, after=4))
    story.append(
        Paragraph(
            f"Generated {generated_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            styles["Generated"],
        )
    )
    return story


# --------------------------------------------------------------------------
# Backwards-compat shim — older callers/tests imported ``_markdown_to_paragraphs``
# --------------------------------------------------------------------------


def _markdown_to_paragraphs(text: str) -> list[tuple[str, str]]:
    """Compatibility helper preserved for older tests / external callers.

    Maps the new typed-block parser back onto the original
    ``(style_name, html_content)`` tuple format used by the previous
    implementation. Headings, body, bullets, numbered list items, and
    blockquotes round-trip; tables, horizontal rules, and metadata
    blocks have no legacy tuple form and are dropped (the new
    ``_markdown_to_blocks`` + ``_build_story`` path is the supported
    way to render those).
    """
    out: list[tuple[str, str]] = []
    for block in _markdown_to_blocks(text):
        if isinstance(block, Title):
            out.append(("Title", _inline_md(block.text)))
        elif isinstance(block, Section):
            out.append(("Section", _inline_md(block.text)))
        elif isinstance(block, Subsection):
            out.append(("Subsection", _inline_md(block.text)))
        elif isinstance(block, Body):
            out.append(("Body", _inline_md(block.text)))
        elif isinstance(block, BulletItem):
            out.append(("Bullet", "• " + _inline_md(block.text)))
        elif isinstance(block, NumberedItem):
            out.append(("Numbered", f"{block.number}. {_inline_md(block.text)}"))
        elif isinstance(block, Quote):
            out.append(("Quote", _inline_md(block.text)))
        # MetaBlock / TableBlock / HRule have no legacy equivalent — skip.
    return out


# --------------------------------------------------------------------------
# Tool
# --------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        title=StringSchema(
            "Title shown on the cover of the PDF.",
            min_length=1,
            max_length=200,
        ),
        body=StringSchema(
            "Markdown body. Supported: '# / ## / ### ' headings (use numbered "
            "sections like '## 1. Executive Summary'), '- ' or '* ' bullets, "
            "'1. ' numbered lists, '> ' blockquotes, '---' horizontal rules, "
            "GitHub-flavored tables, links '[text](url)', and inline "
            "**bold** / *italic* / `code` (code renders as a faint gray pill). "
            "A document metadata block under the title — consecutive lines like "
            "**Date:** April 30, 2026 — renders as a tight key/value header. "
            "Do not repeat the cover title as a leading '# ' heading — it is "
            "rendered from the `title` parameter.",
            min_length=1,
        ),
        filename=StringSchema(
            "Optional filename (without extension). Defaults to a slug of the title.",
            nullable=True,
        ),
        required=["title", "body"],
    )
)
class MakePdfTool(Tool):
    """Render a Markdown report to a styled PDF in the agent workspace.

    Returns the absolute path of the generated file. The agent should then
    deliver the file to the user via the ``message`` tool's ``media``
    parameter (e.g. ``message(content="See attached.", media=[path])``).
    """

    def __init__(self, workspace: Path | str, allowed_dir: Path | str | None = None) -> None:
        self._workspace = Path(workspace)
        self._allowed_dir = Path(allowed_dir) if allowed_dir is not None else self._workspace

    @property
    def name(self) -> str:
        return "make_pdf"

    @property
    def description(self) -> str:
        return (
            "Render a structured Markdown report to a styled PDF in the "
            "workspace. Use this when the user asks for a research report, "
            "analysis, summary, dossier, breakdown, or any long-form "
            "deliverable that benefits from a real document instead of "
            "inline chat text. Style is a restrained monochrome research "
            "report: numbered sections with thin rules, optional Date/Subject "
            "/Status metadata header, justified body, GitHub-flavored tables, "
            "italic blockquotes with a left bar, gray-pill inline code. "
            "Returns the absolute file path — pass that path to the "
            "`message` tool's `media` parameter to deliver the PDF as an "
            "attachment. The cover title is rendered from `title`; do not "
            "duplicate it as a `# ` heading inside `body`."
        )

    async def execute(
        self,
        title: str,
        body: str,
        filename: str | None = None,
        **_kwargs: Any,
    ) -> str:
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.platypus import SimpleDocTemplate
        except ImportError:
            return f"Error: {_INSTALL_HINT}"

        try:
            slug = _slugify(filename or title)
            out_path = _resolve_path(
                str(self._workspace / "reports" / f"{slug}.pdf"),
                workspace=self._workspace,
                allowed_dir=self._allowed_dir,
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)

            theme = _build_theme()
            fonts = _resolve_font_family()
            styles = _build_styles(theme, fonts)
            story = _build_story(title, body, datetime.now(timezone.utc), styles, theme)

            doc = SimpleDocTemplate(
                str(out_path), pagesize=letter,
                rightMargin=0.85 * 72, leftMargin=0.85 * 72,
                topMargin=0.85 * 72, bottomMargin=0.85 * 72,
            )
            doc.build(story)
            return str(out_path)
        except PermissionError as e:
            return f"Error: {e}"
