"""BUILTIN_COMMAND_METADATA is the single source of truth for help text and the
WebUI command palette. Every command registered by ``register_builtin_commands``
must have a metadata row, and ``build_help_text`` must derive from it."""
from pythinker.command import CommandRouter, register_builtin_commands
from pythinker.command.builtin import build_help_text
from pythinker.command.metadata import BUILTIN_COMMAND_METADATA, CommandMeta


def test_metadata_covers_every_registered_router_name():
    router = CommandRouter()
    register_builtin_commands(router)
    registered = set(router._priority) | set(router._exact)
    declared = {m.name for m in BUILTIN_COMMAND_METADATA}
    missing = registered - declared
    assert not missing, f"BUILTIN_COMMAND_METADATA missing rows for: {missing}"


def test_every_metadata_row_has_summary():
    for meta in BUILTIN_COMMAND_METADATA:
        assert isinstance(meta, CommandMeta)
        assert meta.name.startswith("/"), f"{meta.name!r} must start with '/'"
        assert meta.summary, f"{meta.name} has empty summary"


def test_help_text_lists_every_metadata_row():
    text = build_help_text()
    for meta in BUILTIN_COMMAND_METADATA:
        # ``/dream-log`` / ``/dream-restore`` show up via prefix variants too;
        # the canonical name must appear at least once.
        assert meta.name in text, f"{meta.name} missing from /help output"
