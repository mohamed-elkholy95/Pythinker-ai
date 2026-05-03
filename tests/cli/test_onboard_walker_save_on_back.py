"""Tests for _configure_pydantic_model save-on-back semantics.

The original implementation silently discarded every edit when the user
pressed Esc, Left arrow, or Ctrl-C — which mismatched what users expect
from a settings UI and let the schema-defaults backfill make the on-disk
config look like it had saved when nothing actually had. These tests pin
the new behaviour:

* ``[Done]`` — saves
* Esc / Left arrow — saves (treated as "save and back")
* Ctrl-C with no edits — clean exit, returns ``None``
* Ctrl-C with pending edits — confirm prompt; "Yes, discard" returns
  ``None``, "No" returns the working_model so edits aren't lost
"""

from __future__ import annotations

from unittest.mock import patch

from pydantic import BaseModel

from pythinker.cli import onboard


class _Sample(BaseModel):
    enabled: bool = False
    token: str = ""


def _stub_select(answers):
    """Yield successive ``_select_with_back`` returns from a list."""
    it = iter(answers)
    return lambda *a, **k: next(it)


def _patch_panel_and_clear():
    """Suppress console.clear and the rich panel — they're not under test here."""
    return patch.multiple(
        onboard,
        _show_config_panel=lambda *a, **k: None,
    ), patch("pythinker.cli.onboard.console.clear", lambda: None)


def test_done_saves_edits():
    model = _Sample()

    def fake_select(prompt, choices, default=None):
        # First call: pick the "Token: ..." field. Second: hit [Done].
        if "Select field" in prompt:
            return choices[1] if "Token" in choices[1] else "[Done]"
        return None

    select_seq = ["Token: [not set]", "[Done]"]

    with patch.object(onboard, "_select_with_back", _stub_select(select_seq)), \
         patch.object(onboard, "_input_with_existing", lambda *a, **k: "BOT-TOKEN-123"), \
         patch.object(onboard, "_show_config_panel", lambda *a, **k: None), \
         patch("pythinker.cli.onboard.console.clear", lambda: None):
        result = onboard._configure_pydantic_model(model, "Sample")

    assert result is not None
    assert result.token == "BOT-TOKEN-123"
    # Original is untouched (deep-copied).
    assert model.token == ""


def test_esc_saves_edits():
    """Esc / Left arrow returns the working_model so edits propagate up."""
    model = _Sample()

    select_seq = [
        "Token: [not set]",     # pick token field
        onboard._BACK_PRESSED,  # then Esc out — should still SAVE the edit
    ]

    with patch.object(onboard, "_select_with_back", _stub_select(select_seq)), \
         patch.object(onboard, "_input_with_existing", lambda *a, **k: "BOT-TOKEN-123"), \
         patch.object(onboard, "_show_config_panel", lambda *a, **k: None), \
         patch("pythinker.cli.onboard.console.clear", lambda: None):
        result = onboard._configure_pydantic_model(model, "Sample")

    assert result is not None, "Esc must NOT discard — that was the bug being fixed"
    assert result.token == "BOT-TOKEN-123"


def test_ctrl_c_with_no_edits_returns_none():
    """Ctrl-C without dirty edits exits cleanly, no confirm prompt."""
    model = _Sample()

    with patch.object(onboard, "_select_with_back", _stub_select([None])), \
         patch.object(onboard, "_show_config_panel", lambda *a, **k: None), \
         patch("pythinker.cli.onboard.console.clear", lambda: None):
        result = onboard._configure_pydantic_model(model, "Sample")

    assert result is None


def test_ctrl_c_with_edits_prompts_then_saves_when_user_declines_discard():
    """Ctrl-C with edits + user picks 'No, keep' → working_model is returned."""
    from pythinker.cli.onboard_views import clack

    model = _Sample()

    select_seq = [
        "Token: [not set]",  # edit token
        None,                # then Ctrl-C with dirty state
    ]

    with patch.object(onboard, "_select_with_back", _stub_select(select_seq)), \
         patch.object(onboard, "_input_with_existing", lambda *a, **k: "T"), \
         patch.object(onboard, "_show_config_panel", lambda *a, **k: None), \
         patch.object(clack, "confirm", lambda *a, **k: False), \
         patch("pythinker.cli.onboard.console.clear", lambda: None):
        result = onboard._configure_pydantic_model(model, "Sample")

    assert result is not None
    assert result.token == "T"


def test_ctrl_c_with_edits_discards_when_user_confirms():
    from pythinker.cli.onboard_views import clack

    model = _Sample()

    select_seq = [
        "Token: [not set]",
        None,  # Ctrl-C
    ]

    with patch.object(onboard, "_select_with_back", _stub_select(select_seq)), \
         patch.object(onboard, "_input_with_existing", lambda *a, **k: "T"), \
         patch.object(onboard, "_show_config_panel", lambda *a, **k: None), \
         patch.object(clack, "confirm", lambda *a, **k: True), \
         patch("pythinker.cli.onboard.console.clear", lambda: None):
        result = onboard._configure_pydantic_model(model, "Sample")

    assert result is None
