"""Multi-line editor with slash-command autocomplete."""

from __future__ import annotations

from typing import Awaitable, Callable

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.processors import (
    BeforeInput,
    ConditionalProcessor,
    Processor,
    Transformation,
)

from pythinker.cli.tui.commands import SLASH_COMMANDS

_PLACEHOLDER = 'Try "summarise tasks/todo.md"'


class _PlaceholderProcessor(Processor):
    """Append dim placeholder text when the buffer is empty.

    Critically preserves any fragments added by upstream processors
    (e.g. BeforeInput's '> ' prompt) so the prompt stays visible.
    """

    def __init__(self, placeholder: str) -> None:
        self._placeholder = placeholder

    def apply_transformation(self, transformation_input):
        if transformation_input.document.text:
            return Transformation(transformation_input.fragments)
        return Transformation(
            list(transformation_input.fragments)
            + [("class:editor.placeholder", self._placeholder)]
        )


_NAME_COL_WIDTH = 22


class SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        # Only complete inside the verb token (no spaces yet).
        if " " in text:
            return
        prefix = text[1:]
        # One suggestion per command (canonical name preferred). Aliases
        # don't get their own row — they appear in the suggestion's
        # display_meta so the user knows "/quit" is a synonym for "/exit"
        # without /q showing two near-identical lines.
        #
        # display is padded to a fixed width so prompt_toolkit's
        # CompletionsMenu lays the description column out flush across all
        # rows instead of right-aligning each meta against its own name.
        for cmd in SLASH_COMMANDS:
            names = (cmd.name, *cmd.aliases)
            if not any(n.startswith(prefix) for n in names):
                continue
            meta = cmd.summary
            if cmd.aliases:
                meta = f"{cmd.summary}  ·  aliases: {', '.join('/' + a for a in cmd.aliases)}"
            yield Completion(
                cmd.name,
                start_position=-len(prefix),
                display=f"/{cmd.name}".ljust(_NAME_COL_WIDTH),
                display_meta=meta,
            )


def _slash_command_prefix(document: Document) -> bool:
    text = document.text_before_cursor
    return text.startswith("/") and " " not in text and "\n" not in text


class EditorPane:
    def __init__(self, on_submit: Callable[[str], Awaitable[None]]) -> None:
        self._on_submit = on_submit
        self._enabled = True
        # Paste compaction registry. ``<bracketed-paste>`` events for big
        # blocks get stored here keyed by an auto-incrementing id; the
        # buffer only holds the placeholder ``[Pasted text #N +M lines]``,
        # and ``submit()`` substitutes the real text back before sending
        # to the agent. Keeps the visible composer compact even for
        # multi-hundred-line pastes.
        self._pastes: dict[int, str] = {}
        self._paste_counter = 0
        # complete_while_typing is False so only our hook below opens the
        # menu — that keeps the popup deterministic without racing the
        # built-in auto-popup. select_first=True would visually highlight
        # the top match but prompt_toolkit implements selection by
        # *inserting* the completion's text into the buffer (preview-mode);
        # that breaks plain typing and Backspace because every text change
        # re-fires this hook and re-inserts the preview. Instead the popup
        # opens with no row selected, and Enter is bound to accept the top
        # match when complete_state is active (see app.py).
        self.buffer = Buffer(
            multiline=True,
            completer=SlashCompleter(),
            complete_while_typing=False,
        )
        self.buffer.on_text_changed += self._refresh_slash_completion
        # Lead the buffer with a "> " prompt and dim placeholder text when
        # empty (mirrors the Claude Code launch screen reference).
        prompt_processor = BeforeInput("> ", style="class:editor.prompt")
        empty_buffer = Condition(lambda: not self.buffer.text)
        placeholder_processor = ConditionalProcessor(
            _PlaceholderProcessor(_PLACEHOLDER), empty_buffer
        )
        self.control = BufferControl(
            buffer=self.buffer,
            input_processors=[prompt_processor, placeholder_processor],
        )

    def _refresh_slash_completion(self, _: Buffer) -> None:
        """Keep slash-command completions alive after edits such as Backspace."""
        if not _slash_command_prefix(self.buffer.document):
            if self.buffer.complete_state:
                self.buffer.cancel_completion()
            return
        try:
            self.buffer.start_completion(select_first=False)
        except RuntimeError:
            # Some unit-level buffer mutations run outside a live prompt_toolkit
            # application loop. In the real TUI, Application owns that loop.
            return

    PASTE_PLACEHOLDER_RE = __import__("re").compile(
        r"\[Pasted text #(\d+) \+\d+ lines?\]"
    )

    def register_paste(self, text: str) -> str:
        """Stash ``text`` in the paste registry and return its placeholder.

        Caller (the ``<bracketed-paste>`` keybinding in ``app.py``) inserts
        the placeholder into the buffer instead of the raw paste so the
        composer stays compact. ``submit()`` swaps every placeholder back
        for the real text right before handing the turn to the agent.
        """
        self._paste_counter += 1
        n = self._paste_counter
        # Match Claude Code's wording: "+M lines" counts newlines, so a
        # 50-line paste reports "+49 lines". Single-line big pastes show
        # "+0 lines" which is correct (no newlines were swallowed).
        line_count = text.count("\n")
        self._pastes[n] = text
        return f"[Pasted text #{n} +{line_count} lines]"

    def _expand_pastes(self, text: str) -> str:
        """Substitute every ``[Pasted text #N +M lines]`` placeholder for
        the original text recorded under ``N``. Unknown ids pass through
        unchanged so stray text that happens to look like a placeholder
        survives the round trip."""
        def _sub(match):
            n = int(match.group(1))
            return self._pastes.get(n, match.group(0))
        return self.PASTE_PLACEHOLDER_RE.sub(_sub, text)

    async def submit(self) -> None:
        """Extract buffer text, expand pastes, clear, invoke callback."""
        text = self.buffer.text
        if self._enabled and text.strip():
            self.buffer.reset()
            expanded = self._expand_pastes(text)
            # Reset the registry on every submit — placeholder ids are
            # only meaningful for the current composer turn.
            self._pastes.clear()
            self._paste_counter = 0
            await self._on_submit(expanded)

    def disable(self) -> None:
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True
