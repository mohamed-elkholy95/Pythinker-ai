import sys
from io import StringIO
from unittest.mock import patch

import pytest

from pythinker.cli.onboard_views import clack


def _capture(fn, *args, **kwargs):
    buf = StringIO()
    with patch.object(clack, "_OUT", buf):
        fn(*args, **kwargs)
    return buf.getvalue()


def test_intro_emits_open_corner():
    out = _capture(clack.intro, "Pythinker setup")
    assert out.startswith("┌  Pythinker setup")


def test_outro_emits_close_corner():
    out = _capture(clack.outro, "Pythinker is ready.")
    assert out.startswith("└  Pythinker is ready.")


def test_bar_line_emits_pipe_prefix():
    out = _capture(clack.bar, "hello")
    assert out == "│  hello\n"


def test_bar_break_emits_blank_pipe():
    out = _capture(clack.bar_break)
    assert out == "│\n"


def test_print_status_uses_bar_prefix():
    out = _capture(clack.print_status, "Updated config.json")
    assert out == "│  Updated config.json\n"


def test_note_panel_minimal():
    out = _capture(clack.note, "Title", ["body"])
    lines = out.splitlines()
    # ◇  Title ──────╮
    # │              │
    # │  body        │
    # │              │
    # ├──────────────╯
    assert lines[0].startswith("◇  Title")
    assert lines[0].endswith("╮")
    assert any(line.startswith("│") and "body" in line for line in lines)
    assert lines[-1].startswith("├") and lines[-1].endswith("╯")


def test_note_panel_wraps_long_body():
    out = _capture(clack.note, "T", ["a" * 200])
    # No body line should exceed reasonable terminal width.
    for line in out.splitlines():
        assert len(line) <= 100  # 80 inner + box framing


def test_note_panel_blank_pads_for_breathing_room():
    out = _capture(clack.note, "T", ["a", "b"])
    lines = out.splitlines()
    body_lines = [line for line in lines if line.startswith("│") and line.endswith("│")]
    # First/last body region should be blank padding.
    assert body_lines[0].strip("│ ").strip() == ""
    assert body_lines[-1].strip("│ ").strip() == ""


def test_note_panel_bottom_aligns_with_top_corner():
    out = _capture(clack.note, "Security", ["Hello world.", "Second line."])
    lines = out.splitlines()
    title_line = lines[0]
    bottom_line = lines[-1]
    # ╮ in the title line and ╯ in the bottom line should be at the same column.
    assert title_line.index("╮") == bottom_line.index("╯"), (
        f"box misaligned: title ╮ at {title_line.index('╮')}, bottom ╯ at {bottom_line.index('╯')}"
    )


def test_confirm_yes():
    with patch("pythinker.cli.onboard_views.clack.questionary") as q:
        q.confirm.return_value.ask.return_value = True
        out = _capture(clack.confirm, "Continue?", default=False)
    # Should render ◇ Continue? / │ Yes after submit.
    assert "◇  Continue?" in out
    assert "│  Yes" in out


def test_confirm_no():
    with patch("pythinker.cli.onboard_views.clack.questionary") as q:
        q.confirm.return_value.ask.return_value = False
        out = _capture(clack.confirm, "Continue?", default=False)
    assert "◇  Continue?" in out
    assert "│  No" in out


def test_confirm_cancelled_raises():
    with patch("pythinker.cli.onboard_views.clack.questionary") as q:
        q.confirm.return_value.ask.return_value = None  # Ctrl-C
        with pytest.raises(clack.WizardCancelled):
            clack.confirm("Continue?", default=False)


def test_select_returns_chosen_value():
    with patch("pythinker.cli.onboard_views.clack.questionary") as q:
        q.select.return_value.ask.return_value = "manual"
        out = _capture(
            clack.select,
            "Setup mode",
            options=[
                ("quickstart", "QuickStart", "Minimal prompts"),
                ("manual", "Manual", "Walk every section"),
            ],
            default="quickstart",
        )
    assert "◇  Setup mode" in out
    assert "│  Manual" in out


def test_select_cancelled_raises():
    with patch("pythinker.cli.onboard_views.clack.questionary") as q:
        q.select.return_value.ask.return_value = None
        with pytest.raises(clack.WizardCancelled):
            clack.select("X", options=[("a", "A", "")], default="a")


def test_multiselect_returns_list():
    with patch("pythinker.cli.onboard_views.clack.questionary") as q:
        q.checkbox.return_value.ask.return_value = ["a", "c"]
        out = _capture(
            clack.multiselect,
            "Pick channels",
            options=[("a", "A", ""), ("b", "B", ""), ("c", "C", "")],
            defaults=["a"],
        )
    assert "◇  Pick channels" in out
    assert "│  A, C" in out


def test_multiselect_none_selected_shows_none():
    with patch("pythinker.cli.onboard_views.clack.questionary") as q:
        q.checkbox.return_value.ask.return_value = []
        out = _capture(
            clack.multiselect,
            "Pick channels",
            options=[("a", "A", ""), ("b", "B", "")],
        )
    assert "◇  Pick channels" in out
    assert "│  (none)" in out


def test_text_returns_string():
    with patch("pythinker.cli.onboard_views.clack.questionary") as q:
        q.text.return_value.ask.return_value = "hello"
        out = _capture(clack.text, "Workspace?", default="~/.pythinker/workspace")
    assert "◇  Workspace?" in out
    assert "│  hello" in out


def test_text_cancelled_raises():
    with patch("pythinker.cli.onboard_views.clack.questionary") as q:
        q.text.return_value.ask.return_value = None
        with pytest.raises(clack.WizardCancelled):
            clack.text("X")


def test_spinner_writes_label_and_finalizes():
    buf = StringIO()
    with patch.object(clack, "_OUT", buf):
        with clack.spinner("Working"):
            pass
    captured = buf.getvalue()
    assert "Working" in captured
    # Spinner replaces with ◇ on context exit.
    assert "◇" in captured


def test_progress_handle_default_finalizes_with_label_and_period():
    """``progress(label).stop()`` writes ``◇  <label>.`` as the final line —
    mirrors pythinker's prompter.progress() default success render."""
    buf = StringIO()
    with patch.object(clack, "_OUT", buf):
        prog = clack.progress("Loading models")
        prog.stop()
    captured = buf.getvalue()
    assert "Loading models" in captured
    assert "◇" in captured
    assert captured.rstrip().endswith("Loading models.")


def test_progress_handle_stop_with_success_label_overrides_default():
    """Passing ``success_label="Done"`` writes ``◇  Done`` (no period) so
    callers can announce a different message at the success line."""
    buf = StringIO()
    with patch.object(clack, "_OUT", buf):
        prog = clack.progress("Working")
        prog.stop(success_label="Done")
    out = buf.getvalue()
    assert "◇  Done" in out
    # Final label should not still have the in-progress wording.
    assert "Working." not in out.rstrip().split("\n")[-1]


def test_progress_handle_silent_stop_writes_no_check_mark():
    """``stop(success_label="")`` clears the spin line without emitting
    a success symbol — used when the caller will print its own outcome
    (e.g. an error message follows)."""
    buf = StringIO()
    with patch.object(clack, "_OUT", buf):
        prog = clack.progress("Working")
        prog.stop(success_label="")
    out = buf.getvalue()
    assert "◇" not in out


def test_progress_handle_double_stop_is_noop():
    """Defensive: calling ``stop`` twice must not double-print or crash."""
    buf = StringIO()
    with patch.object(clack, "_OUT", buf):
        prog = clack.progress("Working")
        prog.stop()
        prog.stop()
    out = buf.getvalue()
    assert out.count("Working.") == 1


def test_progress_handle_update_changes_label_for_final_line():
    """``update`` mutates the displayed label so a later ``stop()`` writes the
    new label, not the original one."""
    buf = StringIO()
    with patch.object(clack, "_OUT", buf):
        prog = clack.progress("Step 1")
        prog.update("Step 2")
        prog.stop()
    out = buf.getvalue()
    assert "Step 2." in out


def test_abort_outro_when_cancelled():
    out = _capture(clack.abort, "User cancelled")
    assert out.startswith("└  Onboarding aborted: User cancelled")


def test_select_default_passed_as_choice_value():
    """Regression: questionary 2.1.1 validates `default` against Choice.value
    (`common.py:275-288`). Our Choices have `value=opt_id`, so the id must be
    passed through unchanged — translating it to the rendered title makes
    questionary raise ValueError("Invalid `default` value passed").
    """
    captured_calls = []

    def fake_select(title, choices, default, **kwargs):
        captured_calls.append(
            {
                "title": title,
                "choices": choices,
                "default": default,
                "kwargs": kwargs,
            }
        )
        # Mirror questionary's own validation so the test fails the same way
        # production would if we ever pass a non-value default again.
        choice_values = [c.value for c in choices]
        if default is not None and default not in choice_values:
            raise ValueError(f"Invalid `default` value passed. ({default})")
        result = type("R", (), {"ask": lambda self: choices[0].value})()
        return result

    with patch("pythinker.cli.onboard_views.clack.questionary.select", side_effect=fake_select):
        result = clack.select(
            "Choose provider",
            options=[
                ("openai_codex", "OpenAI Codex", "ChatGPT subscription"),
                ("azure_openai", "Azure OpenAI", ""),
            ],
            default="openai_codex",
        )

    assert result == "openai_codex"
    assert len(captured_calls) == 1
    assert captured_calls[0]["default"] == "openai_codex"


def test_select_searchable_flag_threads_to_questionary():
    """``clack.select(..., searchable=True)`` must enable questionary's
    incremental-search mode and disable the j/k navigation that would
    otherwise capture those letters as filter input. Regression guard for
    the pythinker-parity port (Phase 1, task 2)."""
    captured: dict = {}

    def fake_select(title, choices, default, **kwargs):
        captured.update(kwargs)
        return type("R", (), {"ask": lambda self: choices[0].value})()

    with patch("pythinker.cli.onboard_views.clack.questionary.select", side_effect=fake_select):
        clack.select(
            "Pick one",
            options=[("a", "Alpha", ""), ("b", "Beta", "")],
            default="a",
            searchable=True,
        )

    assert captured.get("use_search_filter") is True
    assert captured.get("use_jk_keys") is False


def test_select_searchable_flag_defaults_off():
    """Default ``searchable=False`` keeps the legacy j/k navigation enabled
    so short pickers (yes/no/skip-style) still respond to vim-style keys."""
    captured: dict = {}

    def fake_select(title, choices, default, **kwargs):
        captured.update(kwargs)
        return type("R", (), {"ask": lambda self: choices[0].value})()

    with patch("pythinker.cli.onboard_views.clack.questionary.select", side_effect=fake_select):
        clack.select("Pick one", options=[("a", "Alpha", "")], default="a")

    assert captured.get("use_search_filter") is False
    assert captured.get("use_jk_keys") is True


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="real questionary instantiates a prompt_toolkit Application"
    " against a missing console buffer in CI on Windows",
)
def test_select_default_accepted_by_real_questionary():
    """Bind the contract to the installed questionary version: build the
    same Choice list `clack.select` does and let real questionary validate
    `default`. No mocks — under a4328d4 this raised ValueError at construct
    time, before any `.ask()`.
    """
    import questionary

    options = [
        ("use-existing", "Use existing", "Load current config; refresh new schema fields."),
        ("update", "Update", "Walk the wizard; edit only what differs."),
    ]
    choices = [
        questionary.Choice(title=f"{display}  {hint}" if hint else display, value=opt_id)
        for opt_id, display, hint in options
    ]

    questionary.select("What would you like to do?", choices=choices, default="use-existing")
