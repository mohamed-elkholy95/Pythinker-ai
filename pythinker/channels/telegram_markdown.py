"""Markdown → Telegram-HTML conversion helpers.

Extracted from ``pythinker/channels/telegram.py`` so the regex pipeline that
turns model output into Telegram-safe HTML lives next to the inline-table
formatter and stripper helpers it actually uses, instead of inside the
1,200-LOC channel module.

The public surface is re-exported from ``pythinker.channels.telegram`` so
existing imports (notably ``tests/command/test_task_commands.py`` and
``tests/channels/test_telegram_channel.py``) keep working unchanged.
"""

from __future__ import annotations

import re
import unicodedata


def _escape_telegram_html(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tool_hint_to_telegram_blockquote(text: str) -> str:
    """Render tool hints as an expandable blockquote (collapsed by default)."""
    return f"<blockquote expandable>{_escape_telegram_html(text)}</blockquote>" if text else ""


def _strip_md(s: str) -> str:
    """Strip markdown inline formatting from text."""
    s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
    s = re.sub(r'__(.+?)__', r'\1', s)
    s = re.sub(r'~~(.+?)~~', r'\1', s)
    s = re.sub(r'`([^`]+)`', r'\1', s)
    return s.strip()


def _strip_md_block(text: str) -> str:
    """Strip block-level and inline markdown for readable plain-text preview.

    Used during streaming mid-edits so users see clean text instead of raw
    markdown syntax while the response is still being generated.
    """
    # Code blocks -> just the code
    text = re.sub(r'```[\w]*\n?([\s\S]*?)```', r'\1', text)
    # Headers -> plain text
    text = re.sub(r'^#{1,6}\s+(.+)$', r'\1', text, flags=re.MULTILINE)
    # Blockquotes
    text = re.sub(r'^>\s*(.*)$', r'\1', text, flags=re.MULTILINE)
    # Bold / italic / strikethrough
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    # Inline code
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Links [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Bullet lists
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
    # Numbered lists (normalize spacing)
    text = re.sub(r'^(\d+)\.\s+', r'\1. ', text, flags=re.MULTILINE)
    return text


def _render_table_box(table_lines: list[str]) -> str:
    """Convert markdown pipe-table to compact aligned text for <pre> display."""

    def dw(s: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in s)

    rows: list[list[str]] = []
    has_sep = False
    for line in table_lines:
        cells = [_strip_md(c) for c in line.strip().strip('|').split('|')]
        if all(re.match(r'^:?-+:?$', c) for c in cells if c):
            has_sep = True
            continue
        rows.append(cells)
    if not rows or not has_sep:
        return '\n'.join(table_lines)

    ncols = max(len(r) for r in rows)
    for r in rows:
        r.extend([''] * (ncols - len(r)))
    widths = [max(dw(r[c]) for r in rows) for c in range(ncols)]

    def dr(cells: list[str]) -> str:
        return '  '.join(f'{c}{" " * (w - dw(c))}' for c, w in zip(cells, widths))

    out = [dr(rows[0])]
    out.append('  '.join('─' * w for w in widths))
    for row in rows[1:]:
        out.append(dr(row))
    return '\n'.join(out)


def _markdown_to_telegram_html(text: str) -> str:
    """
    Convert markdown to Telegram-safe HTML.
    """
    if not text:
        return ""

    # 1. Extract and protect code blocks (preserve content from other processing)
    code_blocks: list[str] = []
    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r'```[\w]*\n?([\s\S]*?)```', save_code_block, text)

    # 1.5. Convert markdown tables to box-drawing (reuse code_block placeholders)
    lines = text.split('\n')
    rebuilt: list[str] = []
    li = 0
    while li < len(lines):
        if re.match(r'^\s*\|.+\|', lines[li]):
            tbl: list[str] = []
            while li < len(lines) and re.match(r'^\s*\|.+\|', lines[li]):
                tbl.append(lines[li])
                li += 1
            box = _render_table_box(tbl)
            if box != '\n'.join(tbl):
                code_blocks.append(box)
                rebuilt.append(f"\x00CB{len(code_blocks) - 1}\x00")
            else:
                rebuilt.extend(tbl)
        else:
            rebuilt.append(lines[li])
            li += 1
    text = '\n'.join(rebuilt)

    # 2. Extract and protect inline code
    inline_codes: list[str] = []
    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r'`([^`]+)`', save_inline_code, text)

    # 3. Headers # Title -> <b>Title</b> (preserve visual hierarchy)
    text = re.sub(r'^#{1,6}\s+(.+)$', r'⟪B⟫\1⟪/B⟫', text, flags=re.MULTILINE)

    # 4. Blockquotes > text -> just the text (before HTML escaping)
    text = re.sub(r'^>\s*(.*)$', r'\1', text, flags=re.MULTILINE)

    # 5. Escape HTML special characters
    text = _escape_telegram_html(text)

    # 6. Links [text](url) - must be before bold/italic to handle nested cases
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)

    # 7. Bold **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)

    # 8. Italic _text_ (avoid matching inside words like some_var_name)
    text = re.sub(r'(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])', r'<i>\1</i>', text)

    # 9. Strikethrough ~~text~~
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)

    # 10. Bullet lists - item -> • item
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)

    # 10.5. Numbered lists  1. item -> 1. item (keep number, normalize indent)
    text = re.sub(r'^(\d+)\.\s+', r'\1. ', text, flags=re.MULTILINE)

    # 11. Restore inline code with HTML tags
    for i, code in enumerate(inline_codes):
        # Escape HTML in code content
        escaped = _escape_telegram_html(code)
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")

    # 12. Restore code blocks with HTML tags
    for i, code in enumerate(code_blocks):
        # Escape HTML in code content
        escaped = _escape_telegram_html(code)
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")

    # 13. Restore header bold markers (inserted in step 3, after HTML escaping)
    text = text.replace('⟪B⟫', '<b>').replace('⟪/B⟫', '</b>')

    return text
