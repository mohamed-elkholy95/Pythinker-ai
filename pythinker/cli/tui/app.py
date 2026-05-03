"""Full-screen prompt_toolkit Application wiring."""

from __future__ import annotations

import asyncio
import os
import time as _time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from prompt_toolkit.application import Application
from prompt_toolkit.filters import has_focus
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.controls import FormattedTextControl

from pythinker import __version__
from pythinker.cli.tui import TuiOptions
from pythinker.cli.tui.commands import dispatch as dispatch_slash
from pythinker.cli.tui.layout import build_layout
from pythinker.cli.tui.logging_sink import tui_logging_redirect
from pythinker.cli.tui.panes.chat import ChatPane
from pythinker.cli.tui.panes.editor import EditorPane
from pythinker.cli.tui.panes.hint_footer import HintFooter
from pythinker.cli.tui.panes.overlay import OverlayContainer
from pythinker.cli.tui.panes.status_bar import StatusBar
from pythinker.cli.tui.panes.waiting_spinner import WaitingSpinner
from pythinker.cli.tui.streaming import AssistantStreamHandle
from pythinker.cli.tui.theme import THEMES, TuiTheme
from pythinker.config.paths import get_logs_dir
from pythinker.providers.local_models import list_local_models
from pythinker.providers.registry import find_by_name


@dataclass
class TuiState:
    session_key: str
    model: str
    provider: str
    workspace: Path
    waiting: bool = False
    waiting_started_at: float | None = None
    last_turn_tokens: int = 0
    # Live count of characters streamed during the in-flight turn. Reset to
    # zero at every turn start, incremented on every assistant delta. The
    # spinner converts this to an approximate token count (chars / 4) for
    # its caption — exact tokenization happens at turn end via `usage`.
    streamed_chars: int = 0
    theme_name: str = "default"
    in_flight_task: asyncio.Task | None = None
    # Strong refs for fire-and-forget tasks (Enter on editor, Enter on
    # picker overlays). Without retention, asyncio's weak-task GC can drop
    # mid-execution callbacks. Tasks discard themselves via add_done_callback.
    tasks: set[asyncio.Task] = field(default_factory=set)
    # Timestamp of the last Esc press while a turn was in flight. Used by
    # the keybinding to recognise a double-Esc within ESC_INTERRUPT_WINDOW_S
    # as the "interrupt agent" gesture (single Esc only arms the prompt).
    last_esc_at: float = 0.0
    queued_messages: deque[str] = field(default_factory=deque)


@dataclass
class TuiApp:
    options: TuiOptions
    config: Any
    agent_loop: Any
    theme: TuiTheme
    state: TuiState
    chat_pane: ChatPane
    overlay: OverlayContainer
    status_bar: StatusBar
    hint_footer: HintFooter
    editor: EditorPane
    application: Application


def _turn_is_running(state: TuiState) -> bool:
    task = state.in_flight_task
    return task is not None and not task.done()


def _queue_turn(state: TuiState, chat_pane: ChatPane, text: str) -> None:
    state.queued_messages.append(text)
    count = len(state.queued_messages)
    chat_pane.append_notice(
        f"Message queued ({count} pending). It will run after the current turn.",
        kind="info",
    )


def _finish_turn_and_get_next(state: TuiState, *, cancelled: bool) -> str | None:
    state.waiting = False
    state.waiting_started_at = None
    state.in_flight_task = None
    if cancelled:
        state.queued_messages.clear()
        return None
    if state.queued_messages:
        return state.queued_messages.popleft()
    return None


def _cancel_in_flight_turn(state: TuiState, chat_pane: ChatPane, message: str) -> None:
    if state.in_flight_task and not state.in_flight_task.done():
        state.in_flight_task.cancel()
    queued = len(state.queued_messages)
    state.queued_messages.clear()
    if queued:
        message = f"{message} Cleared {queued} queued message{'s' if queued != 1 else ''}."
    chat_pane.append_notice(message, kind="warn")


async def _self_heal_local_model(app: TuiApp) -> None:
    """Validate the configured model against the live local server.

    For local OpenAI-compatible providers (lm_studio, ollama, vllm, ovms),
    the user can change which model is loaded outside of pythinker. When
    that happens, the configured ``agents.defaults.model`` becomes a
    dangling reference — the chat-completions request fails with
    ``Failed to load model``.

    This helper probes the live ``/v1/models`` (or richer native) endpoint
    at startup and:

    * If the configured id is on the server → no-op.
    * If exactly one model is *loaded* and the configured id is missing
      → auto-switch to the loaded one, persist to config.json, surface a
      visible notice. Hot-reload picks it up at the next turn.
    * If multiple are loaded (or zero) → warn the user and tell them to
      run ``/model`` to pick.

    Errors swallow (typically connection refused = server down). The TUI
    keeps the configured id and the user gets the original error from
    the provider on the first turn — same as before this helper.
    """
    config = app.config
    provider_id = config.get_provider_name(config.agents.defaults.model) or app.state.provider
    spec = find_by_name(provider_id) if provider_id else None
    if spec is None or not getattr(spec, "is_local", False):
        return

    p = getattr(config.providers, provider_id, None)
    api_base = (getattr(p, "api_base", None) if p else None) or spec.default_api_base
    if not api_base:
        return

    try:
        models = await list_local_models(provider_id=provider_id, api_base=api_base)
    except Exception:  # noqa: BLE001 — last-resort guard, never block TUI startup
        logger.exception("self-heal: live probe failed")
        return
    if not models:
        # Server unreachable — surface a one-line notice once, then leave
        # the existing config alone so the user can still try sending
        # (and get the more informative provider error if it really is down).
        app.chat_pane.append_notice(
            f"local provider '{provider_id}' at {api_base} not reachable; "
            "model id will not be auto-validated.",
            kind="warn",
        )
        app.application.invalidate()
        return

    configured = config.agents.defaults.model
    loaded = [m for m in models if m.loaded]

    # Two early no-op paths:
    # 1) Configured id is currently *loaded* — chat-completions will work.
    # 2) Server reports loaded models but doesn't expose a `loaded` flag
    #    (e.g. older LM Studio builds, generic /v1/models fallback). In
    #    that case `loaded == []` and the configured id is in the list,
    #    so we can't actually validate; trust the user.
    if any(m.model_id == configured and m.loaded for m in models):
        return
    if not loaded and any(m.model_id == configured for m in models):
        return

    if len(loaded) == 1:
        target = loaded[0].model_id
        try:
            from pythinker.config.loader import get_config_path, save_config
            from pythinker.providers.factory import build_provider_snapshot

            new_config = config.model_copy(deep=True)
            new_config.agents.defaults.model = target
            snapshot = build_provider_snapshot(new_config)
            app.agent_loop._apply_provider_snapshot(snapshot)  # noqa: SLF001
            app.config = new_config
            app.state.model = target
            # Update the welcome-card context so an empty session that
            # rerenders after the auto-switch shows the actual loaded id
            # rather than the stale config value.
            try:
                app.chat_pane.set_welcome_context(model=target)
            except Exception:  # noqa: BLE001
                pass
            persisted = False
            try:
                save_config(new_config, get_config_path())
                persisted = True
            except Exception as save_exc:
                logger.warning("self-heal: failed to persist config: {}", save_exc)
            tag = "saved" if persisted else "session-only"
            app.chat_pane.append_notice(
                f"configured model '{configured}' isn't loaded on {provider_id}; "
                f"auto-switched to '{target}' ({tag}).",
                kind="info",
            )
        except Exception:  # noqa: BLE001
            logger.exception("self-heal: auto-switch failed")
            app.chat_pane.append_notice(
                f"configured model '{configured}' isn't loaded on {provider_id}; "
                f"run /model to pick from {len(models)} available.",
                kind="warn",
            )
    else:
        # Zero loaded (server has models on disk but none in memory) or
        # multiple loaded (ambiguous which to pick) — defer to user.
        if loaded:
            tip = f"{len(loaded)} loaded; run /model to pick"
        else:
            tip = f"{len(models)} downloaded but none loaded; run /model to pick (or load one in {provider_id} first)"
        app.chat_pane.append_notice(
            f"configured model '{configured}' isn't currently loaded on {provider_id}: {tip}.",
            kind="warn",
        )
    app.status_bar.refresh()
    app.application.invalidate()


async def run(
    opts: TuiOptions,
    *,
    _input: Any | None = None,
    _output: Any | None = None,
) -> int:
    """Boot the Application and block until exit. Returns exit code."""
    log_file = (
        Path(opts.log_file) if opts.log_file else (get_logs_dir() / f"tui-{os.getpid()}.log")
    )

    with tui_logging_redirect(log_file):
        app = await _build_app(opts, _input=_input, _output=_output)

        # Schedule the self-heal probe as a background task so it doesn't
        # block the Application from starting. The state.tasks set retains
        # a strong reference (Python 3.11+ weakly references create_task
        # results otherwise).
        heal_task = asyncio.create_task(_self_heal_local_model(app))
        app.state.tasks.add(heal_task)
        heal_task.add_done_callback(app.state.tasks.discard)

        try:
            await app.application.run_async()
            return 0
        except KeyboardInterrupt:
            return 0
        except Exception:
            import sys

            print(f"TUI exited with error; logs at {log_file}", file=sys.stderr)
            return 1


async def _build_app(
    opts: TuiOptions,
    *,
    _input: Any | None,
    _output: Any | None,
) -> TuiApp:
    from pythinker.agent.loop import AgentLoop
    from pythinker.bus.queue import MessageBus
    from pythinker.cli.commands import _load_browser_config, _load_runtime_config, _make_provider
    from pythinker.cron.service import CronService

    config = _load_runtime_config(opts.config_path, opts.workspace)
    bus = MessageBus()
    provider = _make_provider(config)

    cron = CronService(config.workspace_path / "cron" / "jobs.json")
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        web_config=config.tools.web,
        context_block_limit=config.agents.defaults.context_block_limit,
        max_tool_result_chars=config.agents.defaults.max_tool_result_chars,
        provider_retry_mode=config.agents.defaults.provider_retry_mode,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        timezone=config.agents.defaults.timezone,
        unified_session=config.agents.defaults.unified_session,
        disabled_skills=config.agents.defaults.disabled_skills,
        session_ttl_minutes=config.agents.defaults.session_ttl_minutes,
        tools_config=config.tools,
        browser_config_loader=_load_browser_config,
    )

    theme_name = opts.theme or config.cli.tui.theme
    theme = THEMES.get(theme_name, THEMES["default"])

    state = TuiState(
        session_key=opts.session_key,
        model=config.agents.defaults.model,
        provider=config.get_provider_name(config.agents.defaults.model) or "unknown",
        workspace=config.workspace_path,
        theme_name=theme.name,
    )

    chat_pane = ChatPane(theme=theme)
    # Welcome card metadata (shown before any chat history exists).
    try:
        recent = [s.get("key", "") for s in agent_loop.sessions.list_sessions()][:3]
    except Exception:
        recent = []
    chat_pane.set_welcome_context(
        version=__version__,
        model=state.model,
        provider=state.provider,
        workspace=str(state.workspace).replace(str(Path.home()), "~", 1),
        recent_sessions=recent,
        tip_command="/help to see commands  ·  /model to switch models",
    )
    overlay = OverlayContainer()
    status_bar = StatusBar(state)
    hint_footer = HintFooter(state, overlay)
    spinner = WaitingSpinner(state)

    def _get_width() -> int:
        # Prefer the explicit test output, then the application's runtime
        # output, then the OS terminal size. Falling back to a hard 80 cols
        # caused severe truncation when the actual terminal was wider.
        if _output is not None and hasattr(_output, "get_size"):
            return max(_output.get_size().columns - 4, 20)
        try:
            app = holder.get("app") if holder else None
            if app is not None:
                cols = app.application.output.get_size().columns
                return max(cols - 4, 20)
        except Exception:
            pass
        import shutil
        return max(shutil.get_terminal_size((80, 24)).columns - 4, 20)

    # Chat content: Rich → ANSI → prompt_toolkit fragments. The Window below
    # wraps lines, so we need to strip Rich's trailing-whitespace padding
    # from each line — leaving it in confused prompt_toolkit's wrap arithmetic
    # and produced the truncated "▌ py" / "Doin" artefacts.
    #
    # ``_chat_lines`` backs both the rendered text and the height callback.
    # Height is queried before prompt_toolkit asks the control to render, so
    # this cache is keyed by ChatPane.version and width to avoid using stale
    # welcome-card dimensions after the first user message.
    _chat_lines: list[str] = []
    _chat_cache_key: tuple[int, int] | None = None

    def _ensure_chat_lines() -> list[str]:
        nonlocal _chat_cache_key
        width = _get_width()
        key = (chat_pane.version, width)
        if key != _chat_cache_key:
            raw = chat_pane.render_ansi(width=width)
            # Strip trailing spaces per line (Rich pads to console width).
            _chat_lines[:] = [line.rstrip() for line in raw.split("\n")]
            _chat_cache_key = key
        return _chat_lines

    def _chat_text():
        return ANSI("\n".join(_ensure_chat_lines()))

    def _chat_cursor_position():
        # The cursor is invisible (show_cursor=False) but prompt_toolkit's
        # _scroll_to_make_cursor_visible adjusts vertical_scroll to keep it
        # in view, which would override get_vertical_scroll. Two modes:
        #   - Streaming (no lock): cursor at last line → window follows bottom.
        #   - User scroll locked:  cursor at user_scroll → scroll stays put.
        from prompt_toolkit.data_structures import Point

        lines = _ensure_chat_lines()

        if chat_pane.scroll_lock and chat_pane.user_scroll is not None:
            y = min(chat_pane.user_scroll, max(0, len(lines) - 1))
            return Point(x=0, y=y)
        return Point(x=0, y=max(0, len(lines) - 1))

    def _chat_height_dimension():
        """Snug-but-bounded chat region.

        - When transcript fits in the available rows, ``preferred=max=n``
          caps the window at exactly the content height so the filler
          below absorbs the remaining rows and the editor sits snug
          under the last message.
        - When transcript overflows the terminal, HSplit can only satisfy
          up to ``max=n`` rows, but is bounded by available space; the
          window fills what's left and auto-scroll (via cursor position)
          keeps the bottom visible.
        """
        from prompt_toolkit.layout.dimension import Dimension

        n = max(1, len(_ensure_chat_lines()))
        return Dimension(min=1, preferred=n, max=n)

    chat_control = FormattedTextControl(
        _chat_text,
        focusable=False,
        show_cursor=False,
        get_cursor_position=_chat_cursor_position,
    )
    overlay_control = FormattedTextControl(
        lambda: overlay.top.render() if overlay.visible else [],
    )

    # holder lets _on_submit refer to TuiApp after construction (chicken-and-egg)
    holder: dict = {}

    def _start_spinner_tick() -> None:
        # Spinner ticker: invalidate the application periodically so the
        # WaitingSpinner repaints its frame counter (driven from
        # time.monotonic). Exits as soon as state.waiting flips off.
        async def _spinner_tick() -> None:
            # ~12 Hz to match WaitingSpinner.FRAME_HZ — at the slower
            # 10 Hz tick the 10-frame Braille glyph occasionally repeats
            # the same character on consecutive paints, which reads as
            # a stutter rather than smooth motion.
            while state.waiting:
                holder["app"].application.invalidate()
                await asyncio.sleep(0.08)

        tick_task = asyncio.create_task(_spinner_tick())
        state.tasks.add(tick_task)
        tick_task.add_done_callback(state.tasks.discard)

    def _start_turn(text: str) -> None:
        chat_pane.append_user(text)
        state.waiting = True
        state.waiting_started_at = _time.monotonic()
        state.streamed_chars = 0
        holder["app"].application.invalidate()
        _start_spinner_tick()

        async def _run_turn() -> None:
            cancelled = False
            try:
                stream = AssistantStreamHandle(
                    chat_pane, holder["app"].application, state=state,
                )

                async def _on_tool(ev: Any) -> None:
                    chat_pane.append_tool_event(ev)
                    holder["app"].application.invalidate()

                resp = await agent_loop.process_direct(
                    content=text,
                    session_key=state.session_key,
                    on_progress=stream.on_progress,
                    on_stream=stream.on_delta,
                    on_stream_end=stream.on_end,
                    on_tool_event=_on_tool,
                )
                # Non-streaming fallback: some providers (or providers
                # mid-misconfiguration) return the full response without
                # ever firing on_stream callbacks. ``stream.started`` is a
                # sticky flag set on the first delta — it survives
                # on_end's _handle=None reset, so we only enter this
                # branch when zero deltas ever arrived (no duplicate
                # assistant block on streaming providers).
                if not stream.started and resp is not None:
                    body = (getattr(resp, "content", "") or "").strip()
                    if body:
                        h = chat_pane.append_assistant_stream()
                        h.append_delta(body)
                        h.finalize_markdown()
                    else:
                        chat_pane.append_notice(
                            "Provider returned an empty response. Check that "
                            "the active provider has valid credentials and "
                            "supports the selected model.",
                            kind="warn",
                        )
                # Token usage lives on AgentLoop._last_usage (populated after each
                # provider call). resp.metadata does not carry it.
                usage = getattr(agent_loop, "_last_usage", None) or {}
                if isinstance(usage, dict):
                    state.last_turn_tokens = int(
                        usage.get("total_tokens")
                        or (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
                        or 0
                    )
            except asyncio.CancelledError:
                cancelled = True
            except Exception as e:
                chat_pane.append_notice(f"error: {e} (see logs)", kind="error")
            finally:
                next_text = _finish_turn_and_get_next(state, cancelled=cancelled)
                if next_text is not None:
                    _start_turn(next_text)
                holder["app"].application.invalidate()

        state.in_flight_task = asyncio.create_task(_run_turn())

    async def _on_submit(text: str) -> None:
        # slash commands are handled locally
        handled = await dispatch_slash(holder["app"], text)
        if handled:
            holder["app"].application.invalidate()
            return
        if _turn_is_running(state):
            _queue_turn(state, chat_pane, text)
            holder["app"].application.invalidate()
            return
        _start_turn(text)

    editor = EditorPane(on_submit=_on_submit)

    layout = build_layout(
        status_bar=status_bar,
        chat_control=chat_control,
        editor_control=editor.control,
        hint_footer=hint_footer,
        overlay_control=overlay_control,
        overlay_visible=lambda: overlay.visible,
        spinner=spinner,
        chat_height=_chat_height_dimension,
        chat_pane=chat_pane,
    )
    bindings = _build_key_bindings(
        overlay=overlay, state=state, chat_pane=chat_pane, editor=editor,
    )

    application = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=True,
        style=theme.pt_style,
        mouse_support=True,
        input=_input,
        output=_output,
    )
    application.ttimeoutlen = 0.01

    app = TuiApp(
        options=opts,
        config=config,
        agent_loop=agent_loop,
        theme=theme,
        state=state,
        chat_pane=chat_pane,
        overlay=overlay,
        status_bar=status_bar,
        hint_footer=hint_footer,
        editor=editor,
        application=application,
    )
    holder["app"] = app
    return app


def _build_key_bindings(*, overlay, state, chat_pane, editor) -> KeyBindings:
    from prompt_toolkit.filters import Condition

    kb = KeyBindings()
    overlay_visible = Condition(lambda: overlay.visible)
    editor_focused_no_overlay = Condition(
        lambda: not overlay.visible
    ) & has_focus(editor.buffer)

    # ── Overlay (picker / help / status) navigation ─────────────────────
    # These take priority over the editor's bindings via the overlay_visible
    # filter, so arrow keys / typing / Enter drive the modal instead of the
    # text input underneath.

    @kb.add("up", filter=overlay_visible)
    def _(event):
        top = overlay.top
        if top is not None and hasattr(top, "move_cursor"):
            top.move_cursor(-1)
            event.app.invalidate()

    @kb.add("down", filter=overlay_visible)
    def _(event):
        top = overlay.top
        if top is not None and hasattr(top, "move_cursor"):
            top.move_cursor(1)
            event.app.invalidate()

    @kb.add("pageup", filter=overlay_visible)
    def _(event):
        top = overlay.top
        if top is not None and hasattr(top, "move_cursor"):
            top.move_cursor(-5)
            event.app.invalidate()

    @kb.add("pagedown", filter=overlay_visible)
    def _(event):
        top = overlay.top
        if top is not None and hasattr(top, "move_cursor"):
            top.move_cursor(5)
            event.app.invalidate()

    @kb.add("enter", filter=overlay_visible)
    def _(event):
        top = overlay.top
        if top is not None and hasattr(top, "commit"):
            task = asyncio.create_task(top.commit())
            state.tasks.add(task)
            task.add_done_callback(state.tasks.discard)

    @kb.add("backspace", filter=overlay_visible)
    def _(event):
        top = overlay.top
        if top is not None and hasattr(top, "set_query"):
            current = getattr(top, "_query", "")
            top.set_query(current[:-1])
            event.app.invalidate()

    @kb.add("<any>", filter=overlay_visible)
    def _(event):
        top = overlay.top
        if top is None or not hasattr(top, "set_query"):
            return
        # Append printable single-character keypresses to the picker query.
        for k in event.key_sequence:
            data = getattr(k, "data", "") or ""
            if len(data) == 1 and data.isprintable():
                current = getattr(top, "_query", "")
                top.set_query(current + data)
        event.app.invalidate()

    # ── Chat scroll (PageUp/PageDown/Home/End) ───────────────────────────
    # All routes update chat_pane.user_scroll; the chat window's
    # get_vertical_scroll callback is the single scroll authority.
    # Mouse wheel is handled directly by ChatScrollWindow in layout.py.

    def _chat_window(event):
        from pythinker.cli.tui.layout import ChatScrollWindow

        try:
            for window in event.app.layout.find_all_windows():
                if isinstance(window, ChatScrollWindow):
                    return window
        except Exception:  # noqa: BLE001 — best-effort lookup
            return None
        return None

    @kb.add("pageup", filter=editor_focused_no_overlay)
    def _(event):
        win = _chat_window(event)
        if win is not None:
            win.scroll_page_up()

    @kb.add("pagedown", filter=editor_focused_no_overlay)
    def _(event):
        win = _chat_window(event)
        if win is not None:
            win.scroll_page_down()

    @kb.add("home", filter=editor_focused_no_overlay)
    def _(event):
        chat_pane.set_user_scroll(0)
        event.app.invalidate()

    @kb.add("end", filter=editor_focused_no_overlay)
    def _(event):
        chat_pane.set_user_scroll(None)
        event.app.invalidate()

    # ── Editor (only when no overlay is up) ──────────────────────────────

    @kb.add("enter", filter=editor_focused_no_overlay)
    def _(event):
        # Submit the editor buffer as a new turn. Retain the task so asyncio's
        # weak-task GC doesn't drop it mid-flight (Python 3.11+ docs).
        task = asyncio.create_task(editor.submit())
        state.tasks.add(task)
        task.add_done_callback(state.tasks.discard)

    @kb.add("c-j", filter=editor_focused_no_overlay)
    def _(event):
        # Ctrl+J inserts a newline for multi-line messages without submitting.
        event.current_buffer.insert_text("\n")

    # ── Global ───────────────────────────────────────────────────────────

    @kb.add("escape", filter=overlay_visible, eager=True)
    def _(event):
        # eager=True bypasses prompt_toolkit's meta-key (Alt+X) timeout so
        # ONE Esc closes the modal immediately. Without this the key
        # parser waited ~0.5s to see if a follow-up key arrived to form
        # 'Alt+...' — felt like Esc needed 2-3 presses to register.
        overlay.pop()
        event.app.invalidate()

    # Double-Esc to interrupt the in-flight agent turn. First press arms
    # the gesture (and surfaces a one-line hint); a second Esc within
    # esc_interrupt_window_s cancels state.in_flight_task. The
    # ``~overlay_visible`` filter ensures this binding never fires while
    # the user is closing a picker / help overlay — that path stays on
    # the dedicated overlay-Esc handler above.
    esc_interrupt_window_s = 0.8

    @kb.add("escape", filter=~overlay_visible, eager=True)
    def _(event):
        running = state.in_flight_task and not state.in_flight_task.done()
        if not running:
            return  # idle Esc is a no-op (no overlay, no turn)
        now = _time.monotonic()
        if now - state.last_esc_at <= esc_interrupt_window_s:
            _cancel_in_flight_turn(
                state,
                chat_pane,
                "Turn interrupted (double-Esc). The next message will close "
                "out the cancelled turn.",
            )
            state.last_esc_at = 0.0
            event.app.invalidate()
        else:
            state.last_esc_at = now
            chat_pane.append_notice(
                "Press Esc again within 0.8s to interrupt the agent.",
                kind="info",
            )
            event.app.invalidate()

    @kb.add("c-c")
    def _(event):
        if state.in_flight_task and not state.in_flight_task.done():
            _cancel_in_flight_turn(
                state,
                chat_pane,
                "Turn cancelled. The next message will close out the interrupted turn.",
            )
            event.app.invalidate()
        else:
            event.app.exit()

    @kb.add("c-d")
    def _(event):
        event.app.exit()

    return kb
