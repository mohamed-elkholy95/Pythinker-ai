"""ChatPane: ordered list of ChatBlock variants rendered into ANSI for
prompt_toolkit.formatted_text.ANSI consumption."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Literal

from rich.box import ROUNDED
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pythinker.cli.tui.theme import TuiTheme


@dataclass
class _UserBlock:
    text: str
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%H:%M"))


@dataclass
class _AssistantBlock:
    buffer: str = ""
    rendered_markdown: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%H:%M"))


@dataclass
class _ToolEventBlock:
    event: object  # pythinker.agent.loop.ToolEvent — typed loosely to avoid cycles


@dataclass
class _NoticeBlock:
    text: str
    kind: Literal["info", "warn", "error"]


class AssistantBlockHandle:
    """Tiny accessor returned by ChatPane.append_assistant_stream so callers
    can append deltas and trigger the markdown swap on stream end."""

    def __init__(self, pane: "ChatPane", block: _AssistantBlock) -> None:
        self._pane = pane
        self._block = block

    def append_delta(self, delta: str) -> None:
        self._block.buffer += delta
        self._pane._touch()

    def finalize_markdown(self) -> None:
        self._block.rendered_markdown = True
        self._pane._touch()


class ChatPane:
    def __init__(self, theme: TuiTheme, *, max_blocks: int = 5000) -> None:
        self._theme = theme
        self._blocks: list[object] = []
        self._max = max_blocks
        self._version = 0
        # Scroll state. ``_user_scroll`` is the row offset the user has
        # chosen via the wheel / PageUp / Home; ``None`` means "follow the
        # bottom" (the default — keeps streaming output visible). Setting
        # any explicit value flips ``_scroll_lock`` on so the chat window's
        # ``get_vertical_scroll`` callback stops auto-pinning.
        self._scroll_lock = False
        self._user_scroll: int | None = None
        # Welcome-card context (set by app.py before first render so the
        # empty-state panel can show the live model / workspace / version).
        self._welcome_ctx: dict[str, Any] = {}

    def set_welcome_context(self, **kwargs) -> None:
        """Update the metadata shown on the empty-state welcome card."""
        self._welcome_ctx.update(kwargs)
        self._touch()

    def set_theme(self, theme: TuiTheme) -> None:
        self._theme = theme
        self._touch()

    @property
    def version(self) -> int:
        """Monotonic content version used by the TUI render cache."""
        return self._version

    @property
    def scroll_lock(self) -> bool:
        return self._scroll_lock

    def set_scroll_lock(self, value: bool) -> None:
        self._scroll_lock = value
        if not value:
            self._user_scroll = None

    @property
    def user_scroll(self) -> int | None:
        """User-chosen scroll offset, or ``None`` to follow the bottom."""
        return self._user_scroll

    def set_user_scroll(self, offset: int | None) -> None:
        """Park the chat window at ``offset`` rows from the top.

        Passing ``None`` clears the lock and resumes auto-following the
        bottom on every render — used when the user scrolls back to the
        latest content or sends a new message.
        """
        if offset is None:
            self._user_scroll = None
            self._scroll_lock = False
        else:
            self._user_scroll = max(0, int(offset))
            self._scroll_lock = True

    def block_count(self) -> int:
        return len(self._blocks)

    def append_user(self, text: str) -> None:
        # New user input always means "follow the latest output again."
        self._scroll_lock = False
        self._user_scroll = None
        self._push(_UserBlock(text=text))

    def append_assistant_stream(self) -> AssistantBlockHandle:
        block = _AssistantBlock()
        self._push(block)
        return AssistantBlockHandle(self, block)

    def append_tool_event(self, event: object) -> None:
        self._push(_ToolEventBlock(event=event))

    def append_notice(self, text: str, *, kind: Literal["info", "warn", "error"]) -> None:
        self._push(_NoticeBlock(text=text, kind=kind))

    def clear(self) -> None:
        self._blocks.clear()
        self._touch()

    def reload_from_history(self, records: Iterable[dict[str, Any]]) -> None:
        self._blocks.clear()
        self._touch()
        for rec in records:
            role = rec.get("role")
            if role == "user":
                self._push(_UserBlock(text=rec.get("content") or ""))
            elif role == "assistant":
                block = _AssistantBlock(
                    buffer=rec.get("content") or "",
                    rendered_markdown=True,
                )
                self._push(block)
            elif role == "tool":
                # Approximate a tool block; full ToolEvent reconstruction is
                # not possible from history alone.
                from pythinker.agent.loop import ToolEvent

                self._push(_ToolEventBlock(event=ToolEvent(
                    name=rec.get("name") or "tool",
                    phase="end",
                    args_preview="",
                    result_preview=str(rec.get("content") or "")[:80],
                    duration_ms=None,
                )))

    def render_ansi(self, *, width: int) -> str:
        buf = io.StringIO()
        console = Console(
            file=buf, record=False,
            force_terminal=True, color_system="truecolor",
            width=max(width, 20),
            theme=self._theme.rich_theme,
        )
        if not self._blocks:
            self._render_welcome(console, width=max(width, 20))
        else:
            for block in self._blocks:
                self._render_block(console, block)
        return buf.getvalue()

    def _render_welcome(self, console: Console, *, width: int) -> None:
        """Empty-state welcome card. Shown before any chat history exists.

        Inspired by Claude Code's launch screen: a rounded coral-bordered
        panel with two columns — left has the greeting, mascot, model and
        workspace; right has tips and recent activity.
        """
        ctx = self._welcome_ctx
        version = ctx.get("version", "")
        provider = ctx.get("provider", "")
        model = ctx.get("model", "")
        workspace = ctx.get("workspace", "")
        recent = ctx.get("recent_sessions", []) or []
        tip_command = ctx.get("tip_command", "/help to see commands")

        accent = "#C0C0C0"

        title = Text.from_markup(f"[bold {accent}]Pythinker[/]"
                                 f" [bold {accent}]v{version}[/]")

        # Mascot: pythinker is a snake-themed project (`__logo__ = "🐍"`).
        mascot = Text("🐍", style=f"bold {accent}", justify="center")

        # Left column: welcome + mascot + model line + workspace.
        provider_line = " · ".join(x for x in (model, provider) if x) \
            or "no model configured"
        left = Group(
            Text(""),
            Text("Welcome back!", style="bold", justify="center"),
            Text(""),
            mascot,
            Text(""),
            Text(provider_line, style="dim", justify="center"),
            Text(workspace or "~", style="dim", justify="center"),
            Text(""),
        )

        # Right column: tips + divider + recent activity.
        tip_lines = [
            Text("Tips for getting started", style=f"bold {accent}"),
            Text("Ask Pythinker to read a file, run a shell command,"),
            Text("or summarise a directory."),
            Text(""),
            Text("Recent activity", style=f"bold {accent}"),
        ]
        if recent:
            for s in recent[:3]:
                tip_lines.append(Text(f"• {s}", style="dim"))
        else:
            tip_lines.append(Text("No recent activity", style="dim"))
        right = Group(*tip_lines)

        # Two-column body using a Rich Table.grid with a vertical separator.
        body = Table.grid(padding=(0, 2), expand=True)
        body.add_column(ratio=1)
        body.add_column(width=1)
        body.add_column(ratio=1)
        sep_lines = max(len(tip_lines), 9)
        sep = Text("\n".join("│" for _ in range(sep_lines)), style=accent)
        body.add_row(left, sep, right)

        panel = Panel(
            body,
            title=title,
            title_align="left",
            border_style=accent,
            box=ROUNDED,
            padding=(0, 1),
            width=min(width, 100),
        )
        console.print()
        console.print(panel)
        console.print()
        console.print(Text(f"  {tip_command}", style="dim"))
        console.print()

    def _push(self, block: object) -> None:
        self._blocks.append(block)
        if len(self._blocks) > self._max:
            del self._blocks[: len(self._blocks) - self._max]
        self._touch()

    def _touch(self) -> None:
        self._version += 1

    @staticmethod
    def _render_block(console: Console, block) -> None:
        if isinstance(block, _UserBlock):
            console.print(f"[user.role]▌ you[/]  [timestamp]{block.timestamp}[/]")
            console.print(Text(block.text))
            console.print()
        elif isinstance(block, _AssistantBlock):
            console.print(f"[assistant.role]▌ pythinker[/]  [timestamp]{block.timestamp}[/]")
            if block.rendered_markdown and block.buffer:
                console.print(Markdown(block.buffer))
            else:
                console.print(Text(block.buffer or ""))
            console.print()
        elif isinstance(block, _ToolEventBlock):
            ev = block.event
            tail = []
            preview = getattr(ev, "result_preview", None)
            if preview:
                tail.append(str(preview))
            dur = getattr(ev, "duration_ms", None)
            if dur is not None:
                tail.append(f"{dur / 1000:.1f}s")
            tail_str = "  ".join(tail) if tail else ""
            phase = getattr(ev, "phase", "end")
            style = "tool.error" if phase == "error" else "tool.name"
            console.print(
                f"[{style}]▸ {getattr(ev, 'name', 'tool')}[/]  "
                f"[tool.preview]{getattr(ev, 'args_preview', '') or ''}[/]"
                + (f"  →  {tail_str}" if tail_str else "")
            )
        elif isinstance(block, _NoticeBlock):
            console.print(f"[notice.{block.kind}]{block.text}[/]")
            console.print()
