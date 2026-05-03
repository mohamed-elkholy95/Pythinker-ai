"""Clack-style wizard framework — persistent left bar, glyphs, prompts.

Visual idiom matches pythinker's `@clack/prompts` output verbatim:
  ┌  Title              (intro)
  │  body line          (bar)
  │                     (bar_break)
  ◇  <completed step>   (resolved prompt)
  │  <answer>           (resolved answer)
  ◆  <active step>      (current prompt)
  │  ● Option           (selected)
  │  ○ Option           (unselected)
  └  closing            (outro / abort)

This module owns column 0 between intro() and outro(). Subsequent rendering
must go through these helpers — bare `print` will break the bar.
"""

from __future__ import annotations

import contextlib
import os
import sys
import textwrap
import threading
import time
from typing import TextIO

import questionary

# Mutable singleton so tests can patch the output stream.
_OUT: TextIO = sys.stdout

# Glyphs (single-codepoint strings; colors/dim styling layered later).
G_OPEN = "┌"
G_CLOSE = "└"
G_BAR = "│"
G_ACTIVE = "◆"
G_DONE = "◇"
G_OPT_OFF = "○"
G_OPT_ON = "●"
G_CHECK_OFF = "□"
G_CHECK_ON = "■"
G_SPIN = "◑"


def _write(s: str) -> None:
    _OUT.write(s)
    _OUT.flush()


def intro(title: str) -> None:
    """Open the bar with a wizard title."""
    _write(f"{G_OPEN}  {title}\n")
    _write(f"{G_BAR}\n")


def outro(message: str) -> None:
    """Close the bar with a final message."""
    _write(f"{G_CLOSE}  {message}\n")


def bar(text: str = "") -> None:
    """Render a single bar-prefixed line."""
    if text:
        _write(f"{G_BAR}  {text}\n")
    else:
        _write(f"{G_BAR}\n")


def bar_break() -> None:
    """Render a blank bar line (separator between steps)."""
    _write(f"{G_BAR}\n")


def print_status(text: str) -> None:
    """Plain-status line during the wizard (e.g. 'Updated config.json')."""
    _write(f"{G_BAR}  {text}\n")


def success(title: str, detail: str | None = None) -> None:
    """Resolved-event block on the timeline: `◇ title / │  detail / │`.

    Use for in-wizard "completed" status (e.g. OAuth login finished, key
    validated). Keeps the diamond glyph on the same column as the persistent
    `│` bar and emits a trailing `│` so the timeline stays unbroken.
    """
    _write(f"{G_DONE}  {title}\n")
    if detail:
        _write(f"{G_BAR}  {detail}\n")
    _write(f"{G_BAR}\n")


def failure(title: str, detail: str | None = None) -> None:
    """Resolved-failure block on the timeline: `◇ title / │  detail / │`.

    Same shape as ``success`` — kept distinct so callers can signal intent
    even though the rendered glyph is identical (the bar/diamond are
    monochrome by design to preserve column alignment).
    """
    _write(f"{G_DONE}  {title}\n")
    if detail:
        _write(f"{G_BAR}  {detail}\n")
    _write(f"{G_BAR}\n")


def pause(message: str = "Press Enter to continue...") -> None:
    """`│  message ` prompt that keeps the timeline aligned across input().

    Uses ``input()`` instead of writing then reading so the caret sits
    immediately after the message; swallows EOF/KeyboardInterrupt so a stray
    Ctrl-C inside an info-only pause doesn't kill the wizard.
    """
    try:
        input(f"{G_BAR}  {message} ")
    except (EOFError, KeyboardInterrupt):
        pass


def _terminal_width() -> int:
    """Get terminal width, capped at 100 for readability."""
    try:
        return min(os.get_terminal_size().columns, 100)
    except OSError:
        return 80


def _truncate_hint(hint: str, *, title: str, display: str) -> str:
    """Shorten a Choice hint so questionary's recorded `?  Title  Display Hint`
    line stays within terminal width.

    Why: questionary writes its own one-line answer record after the user
    picks. If `Title + Display + Hint` overflows, the line wraps — and
    questionary's wrap continuation has no awareness of clack's `│` left
    bar, so the next line starts at column 0 and visually breaks the
    timeline. Trimming the hint here keeps that one-line record on a
    single line in any sensibly-sized terminal.

    Budget: ``terminal_width - len(prompt-glyph "?  ") - len(title) -
    len("  ") - len(display) - len("  ") - len("…")``. Anything tighter
    than 12 chars falls back to a hard 12-char minimum so the hint is
    still visible. Empty/None hints pass through.
    """
    if not hint:
        return hint
    overhead = 3 + len(title) + 2 + len(display) + 2 + 1  # "?  " + "  " + "  " + "…"
    budget = _terminal_width() - overhead
    if budget < 12:
        budget = 12
    if len(hint) <= budget:
        return hint
    return hint[: budget - 1].rstrip() + "…"


def note(title: str, body: list[str]) -> None:
    """Render a `◇ Title ──╮ body ├─╯` info panel.

    Body is auto-wrapped to fit within `terminal_width - 6` (leaving room for
    `│  ` prefix and `  │` suffix). Multiple lines in body are wrapped
    independently. A blank padding line is inserted at top and bottom of the
    body region for breathing room.
    """
    width = _terminal_width()
    inner_width = width - 6  # `│  ` prefix + `  │` suffix

    wrapped: list[str] = []
    for line in body:
        if not line:
            wrapped.append("")
            continue
        wrapped.extend(textwrap.wrap(line, width=inner_width) or [""])

    # Pad with blank top/bottom for visual breathing room.
    wrapped = [""] + wrapped + [""]

    box_width = max((len(line) for line in wrapped), default=0)
    box_width = max(box_width, len(title))
    rule_len = box_width + 2

    # ◇  <title> ──...──╮
    title_line = f"{G_DONE}  {title} {'─' * (rule_len - len(title) - 1)}╮"
    _write(title_line + "\n")

    for line in wrapped:
        padded = line.ljust(box_width)
        _write(f"{G_BAR}  {padded}  {G_BAR}\n")

    # ├─...─╯
    _write(f"├{'─' * (rule_len + 2)}╯\n")


class WizardCancelled(Exception):  # noqa: N818
    """User pressed Ctrl-C / Esc inside a prompt."""


def confirm(question: str, *, default: bool = False) -> bool:
    """Render a `◆ Question? / ○ Yes / ● No` confirm.

    On submit, replaces with `◇ Question? / │ Yes` (or `No`). Returns the bool.
    Raises `WizardCancelled` on Ctrl-C / Esc (questionary returns None).
    """
    # Active state: questionary owns the render area.
    answer = questionary.confirm(question, default=default).ask()
    if answer is None:
        raise WizardCancelled(question)

    # Post-resolve: write the persistent record.
    _rewrite_resolved(question, "Yes" if answer else "No")
    return answer


def select(
    title: str,
    options: list[tuple[str, str, str]],  # (id, display, hint)
    *,
    default: str | None = None,
    searchable: bool = False,
) -> str:
    """Render `◆ Title / ● Option / ○ Option`. Returns chosen id.

    `options[i]` is (id, display, hint). Hint is shown dim after display.
    Raises `WizardCancelled` if the user cancels.

    When ``searchable`` is True, questionary's incremental-search mode is
    enabled so the user can type to filter long option lists. Off by
    default to preserve the legacy "press Enter on highlighted row" muscle
    memory for short pickers.
    """
    choices = [
        questionary.Choice(
            title=(
                f"{display}  {_truncate_hint(hint, title=title, display=display)}"
                if hint
                else display
            ),
            value=opt_id,
        )
        for opt_id, display, hint in options
    ]
    # questionary validates `default` against each Choice.value (our opt_id),
    # so pass the id straight through.
    answer = questionary.select(
        title,
        choices=choices,
        default=default,
        use_search_filter=searchable,
        use_jk_keys=not searchable,  # j/k navigation conflicts with search input.
    ).ask()
    if answer is None:
        raise WizardCancelled(title)

    chosen_display = next(
        (display for opt_id, display, _ in options if opt_id == answer),
        str(answer),
    )
    _rewrite_resolved(title, chosen_display)
    return answer


def _rewrite_resolved(title: str, answer: str) -> None:
    """Write the persistent record of a completed prompt.

    questionary owns the active-prompt render area while it's interactive.
    Once it returns, we write a clean `◇ Title / │ Answer / │` block to
    record what happened on the bar.
    """
    _write(f"{G_DONE}  {title}\n")
    _write(f"{G_BAR}  {answer}\n")
    _write(f"{G_BAR}\n")


def text(question: str, *, default: str = "") -> str:
    """Render a free-text prompt; return the entered string.

    Raises WizardCancelled on Ctrl-C / Esc.
    """
    answer = questionary.text(question, default=default).ask()
    if answer is None:
        raise WizardCancelled(question)
    _rewrite_resolved(question, answer)
    return answer


def multiselect(
    title: str,
    options: list[tuple[str, str, str]],
    *,
    defaults: list[str] | None = None,
) -> list[str]:
    """Render a multi-checkbox; return list of chosen ids.

    `defaults` is a list of pre-checked option ids.
    Raises WizardCancelled on Ctrl-C / Esc.
    """
    defaults = defaults or []
    choices = [
        questionary.Choice(
            title=(
                f"{display}  {_truncate_hint(hint, title=title, display=display)}"
                if hint
                else display
            ),
            value=opt_id,
            checked=opt_id in defaults,
        )
        for opt_id, display, hint in options
    ]
    # questionary.checkbox doesn't have a `default` parameter like `select` does,
    # so no translation needed here. Defaults are set via Choice.checked above.
    # Append a hint so users know SPACE toggles a row (Enter only confirms the
    # current set — without this, hitting Enter on a row submits an empty list).
    answer = questionary.checkbox(
        f"{title}  (space to toggle, enter to confirm)", choices=choices
    ).ask()
    if answer is None:
        raise WizardCancelled(title)

    chosen_displays = [display for opt_id, display, _ in options if opt_id in answer]
    _rewrite_resolved(title, ", ".join(chosen_displays) or "(none)")
    return answer


SPIN_FRAMES = ["◐", "◓", "◑", "◒"]


@contextlib.contextmanager
def spinner(label: str):
    """Animated `◑` spinner. On exit, rewrites as `◇ <label>.`.

    Use as a context manager:

        with clack.spinner("Working"):
            do_long_thing()
    """
    stop = threading.Event()
    state = {"frame": 0}

    def _animate():
        while not stop.is_set():
            frame = SPIN_FRAMES[state["frame"] % len(SPIN_FRAMES)]
            _OUT.write(f"\r{frame}  {label}…")
            _OUT.flush()
            state["frame"] += 1
            time.sleep(0.1)

    t = threading.Thread(target=_animate, daemon=True)

    try:
        _write(f"{G_SPIN}  {label}…")
        t.start()
        yield
    finally:
        stop.set()
        t.join(timeout=0.5)
        _OUT.write(f"\r{G_DONE}  {label}.\n")
        _OUT.flush()


class _ProgressHandle:
    """Manually-controlled progress indicator. Returned by ``progress(label)``.

    Unlike ``spinner``, which is a ``with``-block, this is started on
    construction and stopped explicitly, so it composes with awaited work
    via ``try/finally``::

        prog = clack.progress("Loading available models")
        try:
            catalog = await load_catalog()
            prog.update(f"Loaded {len(catalog)} models")
        finally:
            prog.stop()

    Thread-safe stop; double-stop is a no-op. Final line is one of:

    - ``◇  <last-label>.``   (success — default)
    - ``◇  <success_label>``  (override via ``stop(success_label=...)``)
    - blank-rewrite + nothing if ``stop(success_label="")`` (silent stop).
    """

    def __init__(self, label: str) -> None:
        self._label = label
        self._stop_event = threading.Event()
        self._stopped = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._animate, daemon=True)
        _write(f"{G_SPIN}  {label}…")
        self._thread.start()

    def _animate(self) -> None:
        frame = 0
        while not self._stop_event.is_set():
            with self._lock:
                label = self._label
            char = SPIN_FRAMES[frame % len(SPIN_FRAMES)]
            _OUT.write(f"\r{char}  {label}…")
            _OUT.flush()
            frame += 1
            time.sleep(0.1)

    def update(self, label: str) -> None:
        """Change the displayed label without restarting the spinner."""
        with self._lock:
            self._label = label

    def stop(self, success_label: str | None = None) -> None:
        """Stop the animation and write the final line.

        ``success_label=None`` (default) → ``◇  <last-label>.``.
        ``success_label=""`` → silent stop; just clears the spin line.
        ``success_label="X"`` → ``◇  X``.
        """
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            label = self._label
        self._stop_event.set()
        self._thread.join(timeout=0.5)
        if success_label == "":
            _OUT.write("\r" + " " * (len(label) + 6) + "\r")
        elif success_label is None:
            _OUT.write(f"\r{G_DONE}  {label}.\n")
        else:
            _OUT.write(f"\r{G_DONE}  {success_label}\n")
        _OUT.flush()


def progress(label: str) -> _ProgressHandle:
    """Start a manually-controlled progress indicator. Returns a handle whose
    ``.update(label)`` and ``.stop(success_label=None)`` methods drive the
    line. Mirrors pythinker's ``prompter.progress()``. See ``_ProgressHandle``.
    """
    return _ProgressHandle(label)


def abort(reason: str) -> None:
    """Close the bar with an aborted-state outro line."""
    _write(f"{G_CLOSE}  Onboarding aborted: {reason}\n")
