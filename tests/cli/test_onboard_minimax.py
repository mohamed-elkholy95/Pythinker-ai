"""MiniMax post-walk follow-up: trigger semantics (auto / offer / no-op)."""

from unittest.mock import patch

import httpx
import pytest

from pythinker.cli import onboard
from pythinker.config.schema import Config


def _snap(config):
    """Build a provider_snapshot from the current config (test helper)."""
    return {
        "minimax": config.providers.minimax.model_copy(deep=True),
        "minimax_anthropic": config.providers.minimax_anthropic.model_copy(deep=True),
    }


@pytest.fixture
def followup_steps_spy():
    """Patch the inner steps so we observe whether the followup body ran."""
    with patch.object(onboard, "_minimax_followup_run_steps") as spy:
        yield spy


def test_followup_auto_runs_when_key_arrived_this_pass(followup_steps_spy):
    config = Config()
    snapshot = _snap(config)
    config.providers.minimax.api_key = "sk-new"  # walker just set it
    onboard._configure_minimax_followup(config, "minimax", "", snapshot)
    followup_steps_spy.assert_called_once()


def test_followup_auto_runs_when_key_changed_this_pass(followup_steps_spy):
    config = Config()
    config.providers.minimax.api_key = "sk-old"
    snapshot = _snap(config)
    config.providers.minimax.api_key = "sk-newer"
    onboard._configure_minimax_followup(config, "minimax", "sk-old", snapshot)
    followup_steps_spy.assert_called_once()


def test_followup_no_op_when_fully_configured(followup_steps_spy):
    config = Config()
    config.providers.minimax.api_key = "sk-existing"
    config.providers.minimax.api_base = "https://api.minimax.io/v1"
    config.providers.minimax_anthropic.api_key = "sk-existing"
    config.providers.minimax_anthropic.api_base = "https://api.minimax.io/anthropic"
    config.agents.defaults.model = "MiniMax-M2.7"
    snapshot = _snap(config)
    onboard._configure_minimax_followup(config, "minimax", "sk-existing", snapshot)
    followup_steps_spy.assert_not_called()


def test_followup_offer_prompts_when_counterpart_empty(followup_steps_spy):
    config = Config()
    config.providers.minimax.api_key = "sk-existing"
    config.providers.minimax.api_base = "https://api.minimax.io/v1"
    config.agents.defaults.model = "MiniMax-M2.7"
    # counterpart minimax_anthropic stays empty
    snapshot = _snap(config)

    with patch("pythinker.cli.onboard._get_questionary") as gq:
        gq.return_value.confirm.return_value.ask.return_value = True
        onboard._configure_minimax_followup(config, "minimax", "sk-existing", snapshot)
    followup_steps_spy.assert_called_once()


def test_followup_offer_prompts_when_default_model_not_minimax(followup_steps_spy):
    config = Config()
    config.providers.minimax.api_key = "sk-existing"
    config.providers.minimax.api_base = "https://api.minimax.io/v1"
    config.providers.minimax_anthropic.api_key = "sk-existing"
    config.providers.minimax_anthropic.api_base = "https://api.minimax.io/anthropic"
    config.agents.defaults.model = "openai-codex/gpt-5.5"  # pristine default
    snapshot = _snap(config)

    with patch("pythinker.cli.onboard._get_questionary") as gq:
        gq.return_value.confirm.return_value.ask.return_value = False
        onboard._configure_minimax_followup(config, "minimax", "sk-existing", snapshot)
    # User declined the offer
    followup_steps_spy.assert_not_called()


def test_followup_offer_prompts_when_api_base_off_canonical(followup_steps_spy):
    config = Config()
    config.providers.minimax.api_key = "sk-existing"
    config.providers.minimax.api_base = "https://example.com/v1"  # weird
    config.providers.minimax_anthropic.api_key = "sk-existing"
    config.providers.minimax_anthropic.api_base = "https://api.minimax.io/anthropic"
    config.agents.defaults.model = "MiniMax-M2.7"
    snapshot = _snap(config)

    with patch("pythinker.cli.onboard._get_questionary") as gq:
        gq.return_value.confirm.return_value.ask.return_value = True
        onboard._configure_minimax_followup(config, "minimax", "sk-existing", snapshot)
    followup_steps_spy.assert_called_once()


def test_followup_region_step_global(monkeypatch):
    """Global pick rewrites api_base to api.minimax.io flavors."""
    config = Config()
    snapshot = _snap(config)
    config.providers.minimax.api_key = "sk-new"
    config.providers.minimax.api_base = "https://example.com/v1"  # stale base
    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: "Global (api.minimax.io)",
    )
    monkeypatch.setattr(
        onboard, "_minimax_followup_flavor_step", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        onboard, "_minimax_followup_plan_tier_step", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        onboard, "_minimax_followup_validate_step", lambda *a, **k: None,
    )
    onboard._minimax_followup_run_steps(config, "minimax", snapshot)
    assert config.providers.minimax.api_base == "https://api.minimax.io/v1"


def test_followup_region_step_mainland(monkeypatch):
    config = Config()
    snapshot = _snap(config)
    config.providers.minimax_anthropic.api_key = "sk-new"
    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: "Mainland China (api.minimaxi.com)",
    )
    monkeypatch.setattr(onboard, "_minimax_followup_flavor_step", lambda *a, **k: None)
    monkeypatch.setattr(onboard, "_minimax_followup_plan_tier_step", lambda *a, **k: None)
    monkeypatch.setattr(onboard, "_minimax_followup_validate_step", lambda *a, **k: None)
    onboard._minimax_followup_run_steps(config, "minimax_anthropic", snapshot)
    assert config.providers.minimax_anthropic.api_base == "https://api.minimaxi.com/anthropic"


def test_followup_region_skipped_when_pre_key_already_set_a_canonical_base(monkeypatch):
    """If api_base is already a canonical region URL, don't re-prompt."""
    config = Config()
    snapshot = _snap(config)
    config.providers.minimax.api_key = "sk-new"
    config.providers.minimax.api_base = "https://api.minimaxi.com/v1"  # canonical Mainland

    select_calls: list = []
    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: (select_calls.append(prompt) or default),
    )
    monkeypatch.setattr(onboard, "_minimax_followup_flavor_step", lambda *a, **k: None)
    monkeypatch.setattr(onboard, "_minimax_followup_plan_tier_step", lambda *a, **k: None)
    monkeypatch.setattr(onboard, "_minimax_followup_validate_step", lambda *a, **k: None)

    onboard._minimax_followup_run_steps(config, "minimax", snapshot)
    # Region prompt should NOT have been called
    assert "MiniMax region:" not in select_calls
    assert config.providers.minimax.api_base == "https://api.minimaxi.com/v1"


def _stub_other_steps(monkeypatch):
    monkeypatch.setattr(onboard, "_minimax_followup_plan_tier_step", lambda *a, **k: None)
    monkeypatch.setattr(onboard, "_minimax_followup_validate_step", lambda *a, **k: None)


def test_flavor_both_dual_writes_with_region_correct_bases(monkeypatch):
    config = Config()
    config.providers.minimax.api_key = "sk-new"
    config.providers.minimax.api_base = "https://api.minimax.io/v1"
    snapshot = _snap(config)  # taken AFTER walker's writes — counterpart still empty
    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: choices[2]  # "Both (recommended ...)"
        if prompt == "Endpoint flavor:" else default,
    )
    _stub_other_steps(monkeypatch)
    onboard._minimax_followup_run_steps(config, "minimax", snapshot)
    assert config.providers.minimax.api_key == "sk-new"
    assert config.providers.minimax.api_base == "https://api.minimax.io/v1"
    assert config.providers.minimax_anthropic.api_key == "sk-new"
    assert config.providers.minimax_anthropic.api_base == "https://api.minimax.io/anthropic"


def test_flavor_match_entered_leaves_counterpart_at_snapshot(monkeypatch):
    config = Config()
    snapshot = _snap(config)  # both empty
    config.providers.minimax.api_key = "sk-new"
    config.providers.minimax.api_base = "https://api.minimax.io/v1"
    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: choices[0]
        if prompt == "Endpoint flavor:" else default,
    )
    _stub_other_steps(monkeypatch)
    onboard._minimax_followup_run_steps(config, "minimax", snapshot)
    assert config.providers.minimax.api_key == "sk-new"
    assert config.providers.minimax_anthropic.api_key is None  # untouched (default is None)


def test_flavor_swap_undoes_walker_writes_to_unwanted_flavor(monkeypatch):
    """User entered minimax, walker wrote key there, user picks Anthropic-only."""
    config = Config()
    snapshot = _snap(config)  # snapshot captured before walker write — both empty
    config.providers.minimax.api_key = "sk-new"
    config.providers.minimax.api_base = "https://api.minimax.io/v1"
    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: choices[1]  # Anthropic-only
        if prompt == "Endpoint flavor:" else default,
    )
    _stub_other_steps(monkeypatch)
    onboard._minimax_followup_run_steps(config, "minimax", snapshot)
    # entered flavor restored: no leaked key on minimax
    assert config.providers.minimax.api_key is None
    assert config.providers.minimax.api_base is None
    # other flavor populated with the key + region-correct base
    assert config.providers.minimax_anthropic.api_key == "sk-new"
    assert config.providers.minimax_anthropic.api_base == "https://api.minimax.io/anthropic"


def test_flavor_swap_preserves_pre_existing_other_flavor_extras(monkeypatch):
    """Snapshot preserves extra_headers on the unselected flavor."""
    config = Config()
    config.providers.minimax_anthropic.extra_headers = {"X-Custom": "1"}
    snapshot = _snap(config)
    config.providers.minimax.api_key = "sk-new"
    config.providers.minimax.api_base = "https://api.minimax.io/v1"
    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: choices[2]  # Both
        if prompt == "Endpoint flavor:" else default,
    )
    _stub_other_steps(monkeypatch)
    onboard._minimax_followup_run_steps(config, "minimax", snapshot)
    assert config.providers.minimax_anthropic.extra_headers == {"X-Custom": "1"}


def test_flavor_swap_anthropic_to_openai_only(monkeypatch):
    """Symmetric reverse: user entered minimax_anthropic, picks OpenAI-only."""
    config = Config()
    snapshot = _snap(config)
    config.providers.minimax_anthropic.api_key = "sk-new"
    config.providers.minimax_anthropic.api_base = "https://api.minimax.io/anthropic"
    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: choices[0]  # OpenAI-only
        if prompt == "Endpoint flavor:" else default,
    )
    _stub_other_steps(monkeypatch)
    onboard._minimax_followup_run_steps(config, "minimax_anthropic", snapshot)
    assert config.providers.minimax_anthropic.api_key is None
    assert config.providers.minimax.api_key == "sk-new"
    assert config.providers.minimax.api_base == "https://api.minimax.io/v1"


PRISTINE_DEFAULT = "openai-codex/gpt-5.5"


def test_plan_tier_standard_writes_m27_when_default_pristine(monkeypatch):
    config = Config()
    assert config.agents.defaults.model == PRISTINE_DEFAULT
    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: choices[0]
        if prompt == "MiniMax plan tier:" else default,
    )
    onboard._minimax_followup_plan_tier_step(config, "Global (api.minimax.io)")
    assert config.agents.defaults.model == "MiniMax-M2.7"


def test_plan_tier_highspeed_writes_m27_highspeed_when_default_pristine(monkeypatch):
    config = Config()
    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: choices[1]
        if prompt == "MiniMax plan tier:" else default,
    )
    onboard._minimax_followup_plan_tier_step(config, "Global (api.minimax.io)")
    assert config.agents.defaults.model == "MiniMax-M2.7-highspeed"


def test_plan_tier_no_clobber_when_user_already_set_default(monkeypatch):
    config = Config()
    config.agents.defaults.model = "anthropic/claude-3.5-sonnet"
    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: choices[0]
        if prompt == "MiniMax plan tier:" else default,
    )
    onboard._minimax_followup_plan_tier_step(config, "Global (api.minimax.io)")
    assert config.agents.defaults.model == "anthropic/claude-3.5-sonnet"


def test_plan_tier_custom_uses_autocomplete(monkeypatch):
    config = Config()
    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: choices[2]
        if prompt == "MiniMax plan tier:" else default,
    )
    monkeypatch.setattr(
        onboard, "_input_model_with_autocomplete",
        lambda display_name, current, provider: "MiniMax-some-future-model",
    )
    onboard._minimax_followup_plan_tier_step(config, "Global (api.minimax.io)")
    assert config.agents.defaults.model == "MiniMax-some-future-model"


# ---------------------------------------------------------------------------
# Task 9: _minimax_followup_validate_step
# ---------------------------------------------------------------------------

def _make_response(status_code, json_payload=None):
    request = httpx.Request("GET", "https://example.com/models")
    return httpx.Response(status_code, json=(json_payload or {}), request=request)


def test_validate_skipped_when_user_declines(monkeypatch):
    config = Config()
    config.providers.minimax.api_key = "sk-new"
    config.providers.minimax.api_base = "https://api.minimax.io/v1"
    with patch("pythinker.cli.onboard._get_questionary") as gq, \
         patch("pythinker.cli.onboard.httpx.get") as get:
        gq.return_value.confirm.return_value.ask.return_value = False
        onboard._minimax_followup_validate_step(config, "minimax")
    get.assert_not_called()


def test_validate_200_proceeds(monkeypatch, capsys):
    config = Config()
    config.providers.minimax.api_key = "sk-good"
    config.providers.minimax.api_base = "https://api.minimax.io/v1"
    with patch("pythinker.cli.onboard._get_questionary") as gq, \
         patch("pythinker.cli.onboard.httpx.get",
               return_value=_make_response(200, {"data": [
                   {"id": "MiniMax-M2.7"},
                   {"id": "MiniMax-M2.7-highspeed"},
                   {"id": "MiniMax-M2.7-pro"},
               ]})):
        gq.return_value.confirm.return_value.ask.return_value = True
        onboard._minimax_followup_validate_step(config, "minimax")
    assert config.providers.minimax.api_key == "sk-good"  # unchanged


def test_validate_401_clears_key_and_returns(monkeypatch):
    config = Config()
    config.providers.minimax.api_key = "sk-bad"
    config.providers.minimax.api_base = "https://api.minimax.io/v1"
    with patch("pythinker.cli.onboard._get_questionary") as gq, \
         patch("pythinker.cli.onboard.httpx.get",
               return_value=_make_response(401)):
        gq.return_value.confirm.return_value.ask.return_value = True
        onboard._minimax_followup_validate_step(config, "minimax")
    assert config.providers.minimax.api_key == ""  # cleared on 401


def test_validate_timeout_warns_and_proceeds(monkeypatch, capsys):
    config = Config()
    config.providers.minimax.api_key = "sk-keep"
    config.providers.minimax.api_base = "https://api.minimax.io/v1"
    with patch("pythinker.cli.onboard._get_questionary") as gq, \
         patch("pythinker.cli.onboard.httpx.get",
               side_effect=httpx.TimeoutException("slow")):
        gq.return_value.confirm.return_value.ask.return_value = True
        onboard._minimax_followup_validate_step(config, "minimax")
    assert config.providers.minimax.api_key == "sk-keep"


def test_validate_uses_openai_flavor_base_for_anthropic_provider(monkeypatch):
    """Anthropic provider validation hits api.minimax.io/v1, not /anthropic."""
    config = Config()
    config.providers.minimax_anthropic.api_key = "sk-good"
    config.providers.minimax_anthropic.api_base = "https://api.minimax.io/anthropic"
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return _make_response(200, {"data": [{"id": "MiniMax-M2.7"}]})

    with patch("pythinker.cli.onboard._get_questionary") as gq, \
         patch("pythinker.cli.onboard.httpx.get", side_effect=fake_get):
        gq.return_value.confirm.return_value.ask.return_value = True
        onboard._minimax_followup_validate_step(config, "minimax_anthropic")
    assert captured["url"] == "https://api.minimax.io/v1/models"


def test_validate_anthropic_provider_mainland_uses_minimaxi_v1(monkeypatch):
    config = Config()
    config.providers.minimax_anthropic.api_key = "sk-good"
    config.providers.minimax_anthropic.api_base = "https://api.minimaxi.com/anthropic"
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return _make_response(200, {"data": [{"id": "MiniMax-M2.7"}]})

    with patch("pythinker.cli.onboard._get_questionary") as gq, \
         patch("pythinker.cli.onboard.httpx.get", side_effect=fake_get):
        gq.return_value.confirm.return_value.ask.return_value = True
        onboard._minimax_followup_validate_step(config, "minimax_anthropic")
    assert captured["url"] == "https://api.minimaxi.com/v1/models"


# ---------------------------------------------------------------------------
# Task 10: _warn_on_anthropic_env_overrides
# ---------------------------------------------------------------------------


def test_env_hygiene_warns_when_anthropic_base_url_set(monkeypatch, capsys):
    config = Config()
    config.providers.minimax_anthropic.api_key = "sk-new"
    config.providers.minimax_anthropic.api_base = "https://api.minimax.io/anthropic"
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.com")
    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: default,
    )
    monkeypatch.setattr(onboard, "_minimax_followup_flavor_step", lambda *a, **k: None)
    monkeypatch.setattr(onboard, "_minimax_followup_plan_tier_step", lambda *a, **k: None)
    monkeypatch.setattr(onboard, "_minimax_followup_validate_step", lambda *a, **k: None)
    onboard._minimax_followup_run_steps(
        config, "minimax_anthropic", _snap(config),
    )
    out = capsys.readouterr().out
    assert "ANTHROPIC_BASE_URL" in out


def test_env_hygiene_silent_for_openai_flavor(monkeypatch, capsys):
    config = Config()
    config.providers.minimax.api_key = "sk-new"
    config.providers.minimax.api_base = "https://api.minimax.io/v1"
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.com")
    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: default,
    )
    monkeypatch.setattr(onboard, "_minimax_followup_flavor_step", lambda *a, **k: None)
    monkeypatch.setattr(onboard, "_minimax_followup_plan_tier_step", lambda *a, **k: None)
    monkeypatch.setattr(onboard, "_minimax_followup_validate_step", lambda *a, **k: None)
    onboard._minimax_followup_run_steps(config, "minimax", _snap(config))
    out = capsys.readouterr().out
    assert "ANTHROPIC_BASE_URL" not in out


# ---------------------------------------------------------------------------
# B-9 regression: swap case must validate the destination slot, not the source
# ---------------------------------------------------------------------------


def test_run_steps_validates_destination_slot_on_swap(monkeypatch):
    """User entered a key into `minimax` then picks Anthropic-only (a swap).
    Validation must run against `minimax_anthropic` — the slot that ended up
    holding the key — not the original empty `minimax` slot.
    """
    config = Config()
    snapshot = _snap(config)  # both empty
    config.providers.minimax.api_key = "sk-test-xyz"  # walker just wrote it
    config.providers.minimax.api_base = "https://api.minimax.io/v1"

    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: choices[1]  # Anthropic-only → swap
        if prompt == "Endpoint flavor:" else default,
    )
    monkeypatch.setattr(onboard, "_minimax_followup_plan_tier_step", lambda *a, **k: None)

    captured: list[str] = []

    def fake_validate(cfg, provider_name):
        captured.append(provider_name)

    monkeypatch.setattr(onboard, "_minimax_followup_validate_step", fake_validate)

    onboard._minimax_followup_run_steps(config, "minimax", snapshot)

    assert captured == ["minimax_anthropic"]


def test_run_steps_validates_original_slot_when_no_swap(monkeypatch):
    """No-swap case (user picks the flavor matching the entered slot, or Both):
    validation must target the original `provider_name`."""
    config = Config()
    snapshot = _snap(config)
    config.providers.minimax.api_key = "sk-test-xyz"
    config.providers.minimax.api_base = "https://api.minimax.io/v1"

    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: choices[0]  # OpenAI matches entered
        if prompt == "Endpoint flavor:" else default,
    )
    monkeypatch.setattr(onboard, "_minimax_followup_plan_tier_step", lambda *a, **k: None)

    captured: list[str] = []

    def fake_validate(cfg, provider_name):
        captured.append(provider_name)

    monkeypatch.setattr(onboard, "_minimax_followup_validate_step", fake_validate)

    onboard._minimax_followup_run_steps(config, "minimax", snapshot)

    assert captured == ["minimax"]


def test_run_steps_validates_original_slot_on_both(monkeypatch):
    """When user picks Both, the entered slot keeps the key and `other` is
    populated too. Validation should still target the original slot."""
    config = Config()
    snapshot = _snap(config)
    config.providers.minimax_anthropic.api_key = "sk-test-xyz"
    config.providers.minimax_anthropic.api_base = "https://api.minimax.io/anthropic"

    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: choices[2]  # Both
        if prompt == "Endpoint flavor:" else default,
    )
    monkeypatch.setattr(onboard, "_minimax_followup_plan_tier_step", lambda *a, **k: None)

    captured: list[str] = []

    def fake_validate(cfg, provider_name):
        captured.append(provider_name)

    monkeypatch.setattr(onboard, "_minimax_followup_validate_step", fake_validate)

    onboard._minimax_followup_run_steps(config, "minimax_anthropic", snapshot)

    assert captured == ["minimax_anthropic"]


def test_run_steps_validates_destination_slot_on_reverse_swap(monkeypatch):
    """Symmetric reverse swap: user entered into `minimax_anthropic`, picks
    OpenAI-only. Validation must target `minimax`."""
    config = Config()
    snapshot = _snap(config)
    config.providers.minimax_anthropic.api_key = "sk-test-xyz"
    config.providers.minimax_anthropic.api_base = "https://api.minimax.io/anthropic"

    monkeypatch.setattr(
        onboard, "_select_with_back",
        lambda prompt, choices, default=None: choices[0]  # OpenAI-only → swap
        if prompt == "Endpoint flavor:" else default,
    )
    monkeypatch.setattr(onboard, "_minimax_followup_plan_tier_step", lambda *a, **k: None)

    captured: list[str] = []

    def fake_validate(cfg, provider_name):
        captured.append(provider_name)

    monkeypatch.setattr(onboard, "_minimax_followup_validate_step", fake_validate)

    onboard._minimax_followup_run_steps(config, "minimax_anthropic", snapshot)

    assert captured == ["minimax"]
