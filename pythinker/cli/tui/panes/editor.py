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
                display=f"/{cmd.name}",
                display_meta=meta,
            )


def _slash_command_prefix(document: Document) -> bool:
    text = document.text_before_cursor
    return text.startswith("/") and " " not in text and "\n" not in text


class EditorPane:
    def __init__(self, on_submit: Callable[[str], Awaitable[None]]) -> None:
        self._on_submit = on_submit
        self._enabled = True
        self.buffer = Buffer(
            multiline=True,
            completer=SlashCompleter(),
            complete_while_typing=True,
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

    async def submit(self) -> None:
        """Extract buffer text, clear it, and invoke the submit callback."""
        text = self.buffer.text
        if self._enabled and text.strip():
            self.buffer.reset()
            await self._on_submit(text)

    def disable(self) -> None:
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True
