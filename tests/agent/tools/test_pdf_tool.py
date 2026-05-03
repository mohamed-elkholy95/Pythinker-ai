"""Tests for the ``make_pdf`` agent tool — research-report styling."""

from __future__ import annotations

import importlib.util
from unittest.mock import MagicMock

import pytest

from pythinker.agent.loop import AgentLoop
from pythinker.agent.tools.pdf import (
    Body,
    BulletItem,
    HRule,
    MakePdfTool,
    MetaBlock,
    NumberedItem,
    PdfTheme,
    Quote,
    Section,
    Subsection,
    TableBlock,
    Title,
    _build_styles,
    _build_theme,
    _inline_md,
    _markdown_to_blocks,
    _markdown_to_paragraphs,
    _slugify,
)
from pythinker.bus.queue import MessageBus

_HAS_REPORTLAB = importlib.util.find_spec("reportlab") is not None


# --------------------------------------------------------------------------
# Pure helpers — no reportlab dependency
# --------------------------------------------------------------------------


def test_slugify_strips_unsafe_chars_and_lowercases():
    assert _slugify("GitHub Python Repo Trends 2026") == "github_python_repo_trends_2026"
    assert _slugify("hello/world..pdf") == "hello_world_pdf"
    assert _slugify("") == "report"
    assert _slugify("   ") == "report"


def test_slugify_truncates_to_max_len():
    big = "x" * 200
    assert len(_slugify(big, max_len=40)) == 40


def test_inline_md_escapes_then_applies_inline_styles():
    out = _inline_md("**bold** *italic* `code` <not-html>", mono_font="Courier")
    assert "&lt;not-html&gt;" in out
    assert "<b>bold</b>" in out
    assert "<i>italic</i>" in out
    # Code now renders as a faint gray pill, with the resolved mono font name.
    assert "face='Courier'" in out
    assert "backColor='#F1F2F4'" in out


def test_inline_md_does_not_match_italic_inside_bold():
    out = _inline_md("**this is bold**", mono_font="Courier")
    assert out == "<b>this is bold</b>"
    assert "<i>" not in out


def test_inline_md_preserves_markdown_delimiters_inside_code_spans():
    out = _inline_md("Use `**kwargs` in code.", mono_font="Courier")
    assert "**kwargs" in out
    assert "<b>" not in out
    assert "<i>" not in out


def test_inline_md_renders_links_with_underline():
    out = _inline_md("See [the docs](https://example.com/x).", mono_font="Courier")
    assert "<link href='https://example.com/x'" in out
    assert "<u>the docs</u>" in out


# --------------------------------------------------------------------------
# Block tokenizer
# --------------------------------------------------------------------------


def test_markdown_to_blocks_recognizes_basic_constructs():
    body = """\
# Cover Title

## 1. Section A

Some intro paragraph
spanning two lines.

- bullet one
- bullet two

### 1.1 Sub one

1. Numbered alpha
2. Numbered beta

> A pithy callout.

---

Final paragraph."""
    blocks = _markdown_to_blocks(body)
    assert isinstance(blocks[0], Title)
    assert blocks[0].text == "Cover Title"
    assert isinstance(blocks[1], Section)
    assert blocks[1].text == "1. Section A"
    body_blocks = [b for b in blocks if isinstance(b, Body)]
    assert "Some intro paragraph spanning two lines." in body_blocks[0].text
    bullets = [b for b in blocks if isinstance(b, BulletItem)]
    assert [b.text for b in bullets] == ["bullet one", "bullet two"]
    numbered = [b for b in blocks if isinstance(b, NumberedItem)]
    assert [(n.number, n.text) for n in numbered] == [
        (1, "Numbered alpha"),
        (2, "Numbered beta"),
    ]
    assert any(isinstance(b, Subsection) and b.text == "1.1 Sub one" for b in blocks)
    assert any(isinstance(b, Quote) and "pithy callout" in b.text for b in blocks)
    assert any(isinstance(b, HRule) for b in blocks)


def test_markdown_to_blocks_detects_metadata_block_under_title():
    body = """\
# Research Report: Demo

**Date:** April 30, 2026
**Subject:** Technical Analysis of Demo
**Status:** Comprehensive

## 1. Executive Summary

Body text."""
    blocks = _markdown_to_blocks(body)
    assert isinstance(blocks[0], Title)
    meta = blocks[1]
    assert isinstance(meta, MetaBlock)
    assert meta.rows == (
        ("Date", "April 30, 2026"),
        ("Subject", "Technical Analysis of Demo"),
        ("Status", "Comprehensive"),
    )
    assert isinstance(blocks[2], Section)
    assert blocks[2].text == "1. Executive Summary"


def test_markdown_to_blocks_detects_metadata_block_without_explicit_title():
    body = """\
**Date:** April 30, 2026
**Subject:** Demo

## 1. Body

Stuff."""
    blocks = _markdown_to_blocks(body)
    assert isinstance(blocks[0], MetaBlock)
    assert blocks[0].rows == (("Date", "April 30, 2026"), ("Subject", "Demo"))


def test_markdown_to_blocks_parses_table_after_separator():
    body = """\
| Feature | Obscura | Headless Chrome |
| :--- | :--- | :--- |
| Memory Usage | ~30 MB | 200+ MB |
| Binary Size | ~70 MB | 300+ MB |"""
    blocks = _markdown_to_blocks(body)
    assert len(blocks) == 1
    table = blocks[0]
    assert isinstance(table, TableBlock)
    assert table.headers == ("Feature", "Obscura", "Headless Chrome")
    assert table.rows == (
        ("Memory Usage", "~30 MB", "200+ MB"),
        ("Binary Size", "~70 MB", "300+ MB"),
    )


def test_markdown_to_blocks_blank_input_returns_empty():
    assert _markdown_to_blocks("") == []
    assert _markdown_to_blocks("\n\n  \n\n") == []


def test_markdown_to_paragraphs_legacy_shim_still_works():
    """The legacy ``(style, html)`` tuple format is preserved for callers
    outside the tool that still depend on it."""
    body = "# Cover\n\n## A\n\nbody\n\n- one\n\n### sub\n\n1. first"
    out = _markdown_to_paragraphs(body)
    kinds = [k for k, _ in out]
    assert kinds == ["Title", "Section", "Body", "Bullet", "Subsection", "Numbered"]
    body_text = next(html for k, html in out if k == "Body")
    assert "body" in body_text
    bullet_text = next(html for k, html in out if k == "Bullet")
    assert bullet_text.startswith("• ")
    numbered_text = next(html for k, html in out if k == "Numbered")
    assert numbered_text.startswith("1. ")


# --------------------------------------------------------------------------
# Theme + styles
# --------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_REPORTLAB, reason="reportlab not installed in this env")
def test_resolve_font_family_returns_a_family():
    """Resolution always returns a usable FontFamily — either a real
    humanist sans + matching mono if any candidate paths exist on the
    host, or built-in Helvetica/Courier as the last-resort fallback."""
    from pythinker.agent.tools.pdf import FontFamily, _resolve_font_family

    fam = _resolve_font_family()
    assert isinstance(fam, FontFamily)
    # Sans triplet must be self-consistent: regular ≠ bold ≠ italic.
    assert fam.sans
    assert fam.sans_bold
    assert fam.sans_italic
    assert fam.sans_bold_italic
    assert fam.mono


def test_build_theme_uses_monochrome_research_palette():
    """The default theme is a restrained monochrome research-report
    palette — no brand color accents — so the layout reads as a serious
    document rather than marketing collateral."""
    theme = _build_theme()
    assert theme == PdfTheme()
    # Spot-check the user-visible colors.
    assert theme.text_primary == "#111827"
    assert theme.text_muted == "#4B5563"
    assert theme.rule_subtle == "#D1D5DB"
    assert theme.code_bg == "#F1F2F4"
    assert theme.table_header_bg == "#F3F4F6"


@pytest.mark.skipif(not _HAS_REPORTLAB, reason="reportlab not installed in this env")
def test_build_styles_use_research_report_type_scale():
    """The type scale and color choices are stable across whichever
    humanist-sans family the resolver picked up. Font *names* depend on
    what's installed, so we assert against the family the test rig
    asked for explicitly."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_JUSTIFY

    from pythinker.agent.tools.pdf import FontFamily

    theme = _build_theme()
    fonts = FontFamily()  # force the deterministic Helvetica/Courier defaults
    styles = _build_styles(theme, fonts)

    assert styles["Cover"].fontName == fonts.sans_bold
    assert styles["Cover"].fontSize == 24
    assert styles["Section"].fontSize == 17
    assert styles["Section"].keepWithNext == 1
    assert styles["Subsection"].fontSize == 13
    assert styles["Subsection"].keepWithNext == 1
    assert styles["Section"].textColor == colors.HexColor(theme.text_primary)
    assert styles["Body"].fontName == fonts.sans
    assert styles["Body"].fontSize == 10.5
    assert styles["Body"].leading == 15.5
    assert styles["Body"].alignment == TA_JUSTIFY
    assert styles["Body"].textColor == colors.HexColor(theme.text_primary)
    assert styles["Bullet"].leftIndent == 20
    assert styles["Quote"].fontName == fonts.sans_italic
    assert "TableHeader" in styles
    assert styles["TableHeader"].fontName == fonts.sans_bold


# --------------------------------------------------------------------------
# Tool registration + execution
# --------------------------------------------------------------------------


def test_agent_loop_registers_make_pdf(tmp_path):
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")

    assert "make_pdf" in loop.tools.tool_names


async def test_execute_returns_install_hint_when_reportlab_missing(tmp_path, monkeypatch):
    """If reportlab is unavailable the tool returns an actionable error string
    (not an exception) so the agent can fall back to inline text rather than
    crashing the turn."""
    import builtins

    real_import = builtins.__import__

    def _block_reportlab(name, *args, **kwargs):
        if name == "reportlab" or name.startswith("reportlab."):
            raise ImportError("reportlab")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_reportlab)

    tool = MakePdfTool(workspace=tmp_path)
    result = await tool.execute(title="Demo", body="hello")
    assert result.startswith("Error:")
    assert "reportlab" in result.lower()
    assert "install" in result.lower()


@pytest.mark.skipif(not _HAS_REPORTLAB, reason="reportlab not installed in this env")
async def test_execute_renders_pdf_to_workspace_reports(tmp_path):
    """Render an Obscura-style report end-to-end and confirm the file
    exists, parses as PDF, and is non-trivial in size (the new style
    has more flowables than the previous minimal layout)."""
    tool = MakePdfTool(workspace=tmp_path)
    body = """\
**Date:** April 30, 2026
**Subject:** Technical Analysis of Demo
**Status:** Comprehensive Research Report

## 1. Executive Summary

The Demo platform represents a meaningful shift in how agents
operate, with **lightweight footprint** and high-speed execution.

## 2. Architecture

### 2.1 Foundation

- Language: Rust
- Engine: V8
- Protocol: CDP

### 2.2 Comparison

| Feature | Demo | Baseline |
| :--- | :--- | :--- |
| Memory | ~30 MB | 200+ MB |
| Binary | ~70 MB | 300+ MB |

> **Definition:** An agentic browser is a web environment optimized for
> autonomous AI agents.

## 3. Conclusion

1. Lightweight
2. Stealth
3. Open source

---

## References

1. [Demo GitHub](https://example.com/demo)
2. [HN thread](https://example.com/hn)
"""
    out = await tool.execute(title="Research Report: Demo", body=body)
    out_path = tmp_path / "reports" / "research_report_demo.pdf"
    assert out == str(out_path)
    assert out_path.exists()
    raw = out_path.read_bytes()
    assert raw.startswith(b"%PDF-")
    assert raw.rstrip().endswith(b"%%EOF")
    # Sanity check: a multi-section report with a metadata header, a table
    # and a quote produces meaningfully more bytes than a near-empty PDF.
    assert len(raw) > 3_000


@pytest.mark.skipif(not _HAS_REPORTLAB, reason="reportlab not installed in this env")
async def test_execute_honors_explicit_filename(tmp_path):
    tool = MakePdfTool(workspace=tmp_path)
    out = await tool.execute(title="Anything", body="body", filename="custom-name")
    assert out.endswith("custom-name.pdf")
    assert (tmp_path / "reports" / "custom-name.pdf").exists()


@pytest.mark.skipif(not _HAS_REPORTLAB, reason="reportlab not installed in this env")
async def test_execute_rejects_symlink_escape(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "reports").symlink_to(outside, target_is_directory=True)

    tool = MakePdfTool(workspace=workspace)
    result = await tool.execute(title="Escape", body="body")

    assert result.startswith("Error:")
    assert "outside allowed directory" in result
    assert not (outside / "escape.pdf").exists()


# --------------------------------------------------------------------------
# Schema / registration sanity
# --------------------------------------------------------------------------


def test_tool_advertises_required_parameters():
    tool = MakePdfTool(workspace="/tmp")
    schema = tool.parameters
    assert schema["type"] == "object"
    assert set(schema.get("required", [])) == {"title", "body"}
    assert "filename" in schema["properties"]


def test_tool_description_mentions_message_media_handoff_and_new_features():
    """The description must explicitly tell the agent to deliver via
    ``message`` + ``media`` and surface the new structural features so the
    agent can pick them when assembling a report."""
    tool = MakePdfTool(workspace="/tmp")
    desc = tool.description.lower()
    assert "message" in desc
    assert "media" in desc
    # Style / structural cues the agent needs.
    assert "metadata" in desc
    assert "table" in desc
    assert "blockquote" in desc
