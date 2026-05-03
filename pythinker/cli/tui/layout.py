"""Layout composition for the main TUI screen."""

from __future__ import annotations

from typing import Any

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.containers import (
    AnyContainer,
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
)
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.widgets import Frame

# Minimum mouse wheel step size, in rendered rows. 3 matches the convention
# used by terminals (xterm, Alacritty, Kitty) and most editors when
# translating a single wheel notch; taller chat panes scale this up so one
# notch has an immediately visible effect.
_WHEEL_STEP_ROWS = 3
_MAX_WHEEL_STEP_ROWS = 12


class ChatScrollWindow(Window):
    """Window that owns its own scroll state for the chat region.

    The chat pane streams output, so by default the window auto-pins to
    the bottom on every render (via ``get_vertical_scroll``). The
    moment the user scrolls up with the mouse wheel — or PageUp / Home
    via the keyboard hooks in ``app.py`` — the user's chosen offset
    wins until they scroll back to the bottom or send a new message.

    Without this subclass, mouse wheel events were silently undone on
    the next frame: prompt_toolkit's ``_scroll_to_make_cursor_visible``
    saw the synthetic cursor parked on the last line of the transcript
    and snapped ``vertical_scroll`` back to the bottom within ~80 ms
    (the spinner ticker's invalidate cadence).
    """

    def __init__(self, *args: Any, chat_pane: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._chat_pane = chat_pane

    # The Window superclass calls ``_mouse_handler`` when its child
    # ``UIControl`` returns ``NotImplemented`` for a mouse event, which
    # is exactly what ``FormattedTextControl`` does for scroll events
    # in the absence of per-fragment handlers. We intercept SCROLL_UP /
    # SCROLL_DOWN here and let everything else (clicks, drags) fall
    # through to the default handler.
    def _mouse_handler(self, mouse_event: MouseEvent):  # type: ignore[override]
        step = self._wheel_step_rows()
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.scroll_by_rows(-step)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.scroll_by_rows(step)
            return None
        return super()._mouse_handler(mouse_event)

    def _max_scroll(self) -> int:
        info = self.render_info
        if info is None:
            return 0
        return max(0, info.content_height - info.window_height)

    def _user_scroll_by(self, delta_rows: int) -> None:
        self.scroll_by_rows(delta_rows)

    def _wheel_step_rows(self) -> int:
        info = self.render_info
        if info is None:
            return _WHEEL_STEP_ROWS
        scaled = max(_WHEEL_STEP_ROWS, info.window_height // 5)
        return min(_MAX_WHEEL_STEP_ROWS, scaled)

    def _apply_scroll_offset(self, offset: int) -> None:
        # prompt_toolkit ignores get_vertical_scroll for wrap_lines=True, so
        # update the Window's concrete scroll fields immediately. Without this,
        # the pane state changes but the visible content and scrollbar lag until
        # a later render reconciles the internal vertical_scroll.
        self.vertical_scroll = offset
        self.vertical_scroll_2 = 0

    def scroll_by_rows(self, delta_rows: int) -> None:
        """Move the chat scroll offset by rendered rows.

        This uses Window.render_info so PageUp/PageDown and mouse wheel
        operate on the same content/viewport geometry as prompt_toolkit.
        """
        max_scroll = self._max_scroll()
        if max_scroll <= 0:
            # Nothing to scroll — content already fits in the window.
            self._chat_pane.set_user_scroll(None)
            self._apply_scroll_offset(0)
            return
        current = self._chat_pane.user_scroll
        if current is None:
            # First user-driven scroll: anchor at the current bottom so
            # SCROLL_UP starts from where the user actually sees the
            # transcript, not from row 0.
            current = max_scroll
        new_offset = max(0, min(max_scroll, current + delta_rows))
        if new_offset >= max_scroll:
            # Reached the bottom again — release the lock and resume
            # auto-following streaming output.
            self._chat_pane.set_user_scroll(None)
            self._apply_scroll_offset(max_scroll)
        else:
            self._chat_pane.set_user_scroll(new_offset)
            self._apply_scroll_offset(new_offset)
        try:
            get_app().invalidate()
        except Exception:  # noqa: BLE001 — invalidate is best-effort
            pass

    def scroll_page_up(self) -> None:
        info = self.render_info
        page = max(1, (info.window_height - 1) if info is not None else 10)
        self.scroll_by_rows(-page)

    def scroll_page_down(self) -> None:
        info = self.render_info
        page = max(1, (info.window_height - 1) if info is not None else 10)
        self.scroll_by_rows(page)


def _chat_get_vertical_scroll(chat_pane: Any):
    """Return the ``get_vertical_scroll`` callback for the chat window.

    When the chat pane has an active user scroll offset, return it so
    the window paints exactly where the user parked it. Otherwise
    auto-pin to ``content_height - window_height`` so streaming output
    stays visible. This is still useful for non-wrapped renders and as
    a render-time fallback; wrapped chat windows also update their
    concrete ``vertical_scroll`` immediately in ``ChatScrollWindow``.
    """

    def _resolve(window: Window) -> int:
        info = window.render_info
        if info is None:
            return 0
        max_scroll = max(0, info.content_height - info.window_height)
        offset = chat_pane.user_scroll if chat_pane is not None else None
        if offset is None:
            return max_scroll
        return max(0, min(max_scroll, offset))

    return _resolve


def build_layout(
    *, status_bar, chat_control, editor_control, hint_footer, overlay_control,
    overlay_visible, spinner=None, chat_height=None, chat_pane=None,
) -> Layout:
    """Compose the TUI layout.

    ``overlay_visible`` is a zero-arg callable returning bool — used to gate
    the picker/help/status overlay Float so it doesn't paint a blank box on
    top of the chat when no overlay is active.

    ``chat_height`` is an optional callable returning a ``Dimension`` per
    render. The default ``Dimension(weight=1)`` makes chat fill all
    leftover space; passing a dynamic dimension based on
    ``len(_chat_lines)`` gives the snug-editor-under-chat feel for
    short transcripts while still letting HSplit shrink the window
    when the transcript overflows.

    ``chat_pane`` (when supplied) wires the wheel-scroll handler in
    ``ChatScrollWindow`` to a single source of truth for the user's
    scroll offset. When omitted, the chat window falls back to a plain
    ``Window`` with auto-scroll behavior — used by tests that build
    layouts without a real ChatPane.
    """
    # Chat region. With a chat_pane attached, we use ChatScrollWindow so
    # the wheel actually works (see class docstring); otherwise plain
    # Window keeps the historical behavior so existing tests still work.
    chat_window: Window
    chat_window_height = chat_height if chat_height is not None else Dimension(weight=1)
    if chat_pane is not None:
        chat_window = ChatScrollWindow(
            chat_pane=chat_pane,
            content=chat_control,
            wrap_lines=True,
            height=chat_window_height,
            allow_scroll_beyond_bottom=False,
            right_margins=[ScrollbarMargin(display_arrows=False)],
            dont_extend_height=True,
            get_vertical_scroll=_chat_get_vertical_scroll(chat_pane),
        )
    else:
        chat_window = Window(
            content=chat_control,
            wrap_lines=True,
            height=chat_window_height,
            allow_scroll_beyond_bottom=False,
            right_margins=[ScrollbarMargin(display_arrows=False)],
            dont_extend_height=True,
        )
    # Single-line input by default; grows up to 10 rows as the user types
    # multi-line content. dont_extend_height prevents HSplit from flexing
    # the editor beyond its preferred 1 row when filler exists. Matches
    # Claude Code's compact prompt layout (one row between the rule lines).
    editor_window = Window(
        content=editor_control,
        height=Dimension(min=1, preferred=1, max=10),
        wrap_lines=True,
        style="class:editor",
        dont_extend_height=True,
    )

    body_children: list[AnyContainer] = [
        Window(content=status_bar.control, height=1, style="class:status"),
        Window(height=1, char="─", style="class:status.frame"),
        Window(height=1, style="class:status.gap"),
        chat_window,
    ]
    if spinner is not None:
        body_children.append(
            ConditionalContainer(
                content=Window(content=spinner.control,
                               height=Dimension.exact(spinner.height),
                               style="class:spinner"),
                filter=Condition(lambda: bool(getattr(spinner, "_state").waiting)),
            )
        )
    # Trailing filler with weight=1 absorbs unused vertical space below
    # the hint when the transcript is short — keeping the editor snug
    # right under the last chat block instead of pinning it to the
    # terminal floor. When the transcript outgrows the available rows,
    # HSplit shrinks the filler to 0 and the chat window is constrained
    # to the rows that remain, which is exactly when auto-scroll kicks in.
    filler = Window(height=Dimension(weight=1), style="class:default")

    body_children.extend([
        Window(height=1, char="─", style="class:rule"),
        editor_window,
        Window(height=1, char="─", style="class:rule"),
        Window(content=hint_footer.control, height=1, style="class:hint"),
        filler,
    ])
    body = HSplit(body_children)

    # Overlay needs a solid background fill so it occludes the chat below.
    # Without this, picker rows render with empty backgrounds and the chat
    # text bleeds through, making the overlay look 'overlapped' / partly
    # invisible. Wrapping in a Frame draws a coral-bordered box similar to
    # the welcome card.
    overlay_window = ConditionalContainer(
        content=Frame(
            body=Window(
                content=overlay_control,
                style="class:overlay",
                always_hide_cursor=True,
                dont_extend_height=True,
            ),
            style="class:overlay.frame",
        ),
        filter=Condition(overlay_visible),
    )
    # Slash-command autocomplete popup. Anchored to the editor's cursor
    # position via xcursor=True/ycursor=True so it appears right above the
    # input line as the user types '/'.
    completions_float = Float(
        xcursor=True,
        ycursor=True,
        content=CompletionsMenu(max_height=8, scroll_offset=1),
    )

    floats = [
        Float(top=4, left=6, right=6, content=overlay_window),
        completions_float,
    ]

    return Layout(FloatContainer(content=body, floats=floats), focused_element=editor_window)
