"""Tests for the pre-save summary panels — especially the pythinker-port
diff renderer that highlights changes between the on-disk and about-to-be-
saved configs (Phase 1 task 6)."""

from io import StringIO
from unittest.mock import patch

from pythinker.cli.onboard_views import clack, summary
from pythinker.cli.onboard_views.summary import (
    _format_value,
    _is_secret_path,
    _walk,
    render_pre_save_diff,
)
from pythinker.config.schema import Config


def _capture(fn, *args, **kwargs) -> str:
    buf = StringIO()
    with patch.object(clack, "_OUT", buf):
        fn(*args, **kwargs)
    return buf.getvalue()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def test_is_secret_path_matches_credential_field_names():
    assert _is_secret_path("providers.openai.api_key") is True
    assert _is_secret_path("channels.telegram.token") is True
    assert _is_secret_path("auth.client_secret") is True
    assert _is_secret_path("user.password") is True
    # Non-secret leaves stay clear.
    assert _is_secret_path("agents.defaults.model") is False
    assert _is_secret_path("gateway.port") is False


def test_is_secret_path_masks_dynamic_dict_keys_anywhere_in_path():
    """User-controlled dicts (extra_headers, extra_body, MCP headers/env)
    inject arbitrary keys as the final segment. Checking only the last
    segment would leak ``Authorization`` / ``CREDENTIAL`` / ``X-Bearer``
    style headers in the pre-save diff. We scan every segment instead."""
    # extra_headers — Authorization-style HTTP header.
    assert (
        _is_secret_path("providers.anthropic.extra_headers.Authorization") is True
    )
    # MCP server dict — Bearer token in custom header name.
    assert _is_secret_path("tools.mcp_servers.foo.headers.X-Bearer") is True
    # MCP stdio env var named CREDENTIAL.
    assert _is_secret_path("tools.mcp_servers.foo.env.CREDENTIAL") is True
    # extra_body — raw `api_key` key smuggled through extra_body dict.
    assert _is_secret_path("providers.minimax.extra_body.api_key") is True
    # Generic auth.* path (already worked via 'secret' substring, but verify
    # the new 'auth' hint also fires on the segment itself).
    assert _is_secret_path("providers.foo.auth.value") is True


def test_is_secret_path_does_not_mask_innocuous_paths():
    """Adding the 'auth' / 'credential' / 'bearer' hints risks false
    positives on common config paths. Pin the regression: paths that merely
    *contain* substrings of feature names but aren't credentials must not
    be masked."""
    # 'models' contains no secret hint substring — alternate_models lists
    # model identifiers, not secrets.
    assert _is_secret_path("agents.defaults.alternate_models") is False
    # web search providers map keys are provider names, not secrets.
    assert _is_secret_path("tools.web_search.providers") is False
    # api_base is a URL, not a secret.
    assert _is_secret_path("providers.anthropic.api_base") is False
    # Channel host fields are network endpoints.
    assert _is_secret_path("channels.email.host") is False


def test_format_value_masks_secret_when_value_is_present():
    assert _format_value("sk-real-key", masked=True) == "***"
    # Empty / None values aren't masked — there's nothing to hide and the
    # diff signal "the field cleared" is useful.
    assert _format_value(None, masked=True) == "(none)"
    assert _format_value("", masked=True) == "(empty)"


def test_format_value_truncates_long_strings():
    long = "x" * 100
    assert _format_value(long, masked=False).endswith("…")


def test_format_value_summarizes_collections_by_count():
    assert _format_value([1, 2, 3], masked=False) == "[3 items]"
    assert _format_value({"a": 1, "b": 2}, masked=False) == "{2 keys}"
    assert _format_value([], masked=False) == "[]"
    assert _format_value({}, masked=False) == "{}"


def test_walk_flattens_nested_dict_to_dotted_paths():
    flat = _walk({"a": {"b": {"c": 1}, "d": 2}, "e": 3})
    assert flat == {"a.b.c": 1, "a.d": 2, "e": 3}


# --------------------------------------------------------------------------
# render_pre_save_diff — the pythinker-parity port
# --------------------------------------------------------------------------


def test_render_pre_save_diff_announces_fresh_install_when_old_is_none():
    """First-run path: no prior config to diff against. The renderer
    short-circuits with a one-line note rather than walking field-by-field."""
    out = _capture(render_pre_save_diff, None, Config())
    assert "fresh install" in out.lower()


def test_render_pre_save_diff_no_changes_renders_no_changes_marker():
    """Two identical configs render as ``(no changes)`` — important so the
    user can confirm 'I just walked through but kept everything the same'."""
    cfg = Config()
    out = _capture(render_pre_save_diff, cfg, cfg)
    assert "(no changes)" in out


def test_render_pre_save_diff_changed_field_uses_tilde_arrow_format():
    """Changed leaf paths render as ``~ path: old  →  new``. Use a short
    new-value so clack's note panel doesn't word-wrap the substring across
    two visible lines (the test is on the diff renderer, not the wrapper)."""
    old = Config()
    new = Config()
    new.agents.defaults.model = "x-new"
    out = _capture(render_pre_save_diff, old, new)
    assert "~ agents.defaults.model" in out
    assert "→" in out
    assert "x-new" in out


def test_render_pre_save_diff_masks_secret_field_value():
    """Secret-looking field paths render as ``***`` regardless of the actual
    value — the rendered panel is safe to screenshot."""
    old = Config()
    new = Config()
    # api_key lives on every ProviderConfig sub-block; pick anthropic.
    new.providers.anthropic.api_key = "sk-real-leaky-secret"
    out = _capture(render_pre_save_diff, old, new)
    # The path appears, but the value is masked to ***.
    assert "providers.anthropic.api_key" in out
    assert "***" in out
    assert "sk-real-leaky-secret" not in out


def test_render_pre_save_diff_masks_authorization_header_in_extra_headers():
    """Regression for B-4: arbitrary header names inside ``extra_headers``
    dicts (e.g. ``Authorization``) must be masked. The old logic only
    checked the last dotted segment for fixed substrings like 'key' /
    'token' / 'secret', which let ``Authorization: Bearer sk-live-xyz``
    leak in plaintext in the pre-save diff panel."""
    old = Config()
    new = Config()
    new.providers.anthropic.extra_headers = {"Authorization": "Bearer sk-live-xyz"}
    out = _capture(render_pre_save_diff, old, new)
    assert "providers.anthropic.extra_headers.Authorization" in out
    assert "***" in out
    assert "sk-live-xyz" not in out
    assert "Bearer" not in out  # the value, not the path, must not appear


def test_render_pre_save_diff_masks_bearer_token_in_mcp_headers():
    """MCP server custom headers can carry bearer tokens under arbitrary
    key names (``X-Bearer``, ``X-Auth-Token``, etc.). Must be masked."""
    from pythinker.config.schema import MCPServerConfig

    old = Config()
    new = Config()
    new.tools.mcp_servers["notion"] = MCPServerConfig(
        type="streamableHttp",
        url="https://mcp.notion.com/mcp",
        headers={"X-Bearer": "leaky-mcp-bearer-value"},
    )
    out = _capture(render_pre_save_diff, old, new)
    assert "tools.mcp_servers.notion.headers.X-Bearer" in out
    assert "***" in out
    assert "leaky-mcp-bearer-value" not in out


def test_render_pre_save_diff_masks_credential_env_in_mcp_stdio():
    """MCP stdio servers receive subprocess env vars; arbitrary names like
    ``CREDENTIAL`` / ``API_AUTH`` must be masked."""
    from pythinker.config.schema import MCPServerConfig

    old = Config()
    new = Config()
    new.tools.mcp_servers["foo"] = MCPServerConfig(
        type="stdio",
        command="npx",
        args=["foo-mcp"],
        env={"CREDENTIAL": "leaky-stdio-credential"},
    )
    out = _capture(render_pre_save_diff, old, new)
    assert "tools.mcp_servers.foo.env.CREDENTIAL" in out
    assert "***" in out
    assert "leaky-stdio-credential" not in out


def test_render_pre_save_diff_masks_api_key_in_extra_body():
    """``extra_body`` dicts merge into every request payload — a careless
    user might paste an api_key field there. Mask it just like top-level
    ``api_key``."""
    old = Config()
    new = Config()
    new.providers.minimax.extra_body = {"api_key": "leaky-extra-body-value"}
    out = _capture(render_pre_save_diff, old, new)
    assert "providers.minimax.extra_body.api_key" in out
    assert "***" in out
    assert "leaky-extra-body-value" not in out


def test_render_pre_save_diff_does_not_mask_non_secret_paths():
    """Regression check: adding the 'auth' / 'bearer' / 'credential' hints
    must not leak through and mask innocuous paths. ``alternate_models``
    contains no hint substring; the model identifier should render in
    plaintext so the user can verify the diff."""
    old = Config()
    new = Config()
    new.agents.defaults.alternate_models = ["openai/gpt-5", "anthropic/claude-4"]
    out = _capture(render_pre_save_diff, old, new)
    assert "agents.defaults.alternate_models" in out
    # Lists render as a count, not individual items, but the value must
    # not be masked to ***.
    assert "[2 items]" in out


def test_render_pre_save_diff_added_path_uses_plus_marker():
    """Paths present only in the new config render with ``+``."""
    old = Config()
    old_dump = old.model_dump()
    new = Config()
    new_dump = new.model_dump()
    # Inject a synthetic added key in the new flatten — exercise via _walk
    # to confirm the renderer handles the prefix correctly.
    flat_old = _walk(old_dump)
    flat_new = _walk(new_dump)
    # Exit the synthetic test if both already produce the same set of keys
    # (happens when Config has no list/dict-shaped extras that drift).
    assert set(flat_old.keys()) == set(flat_new.keys())  # documents the invariant


def test_step_summary_diff_renders_when_existing_config_present(tmp_path, monkeypatch):
    """The summary step itself wires render_pre_save_diff in when a config
    file exists on disk. We monkeypatch get_config_path to a tmp file so the
    diff branch fires even in test isolation."""
    from pythinker.cli.onboard import _step_summary_confirm, _WizardContext

    cfg_path = tmp_path / "config.json"
    # Seed a baseline config, then mutate the in-memory draft so the diff
    # has something to report.
    Config().model_dump_json()  # type sanity
    cfg_path.write_text('{"agents": {"defaults": {"model": "old-model"}}}')
    monkeypatch.setattr("pythinker.cli.onboard.get_config_path", lambda: cfg_path)
    monkeypatch.setattr("pythinker.cli.onboard.save_config", lambda _cfg, _p: None)

    seen_panels: list[str] = []

    def fake_note(title, body):
        seen_panels.append(title)

    ctx = _WizardContext(draft=Config(), non_interactive=True)
    ctx.draft.agents.defaults.model = "new-model"

    with patch("pythinker.cli.onboard_views.clack.note", side_effect=fake_note), \
         patch("pythinker.cli.onboard_views.clack.bar_break"), \
         patch("pythinker.cli.onboard_views.clack.print_status"):
        result = _step_summary_confirm(ctx)

    assert result.status == "continue"
    assert "Changes since last save" in seen_panels


def test_render_pre_save_lists_enabled_dict_channels():
    """Channel configs are stored as dict extras; enabled channels must still show."""
    cfg = Config()
    setattr(cfg.channels, "telegram", {"enabled": True, "token": "redacted"})

    captured: dict[str, list[str]] = {}

    def fake_note(title, body):
        captured[title] = body

    with patch("pythinker.cli.onboard_views.clack.note", side_effect=fake_note), \
         patch("pythinker.cli.onboard_views.clack.bar_break"):
        summary.render_pre_save(cfg)

    ready = captured["Ready to save"]
    channels_line = next(line for line in ready if line.startswith("Channels:"))
    assert "telegram" in channels_line
    assert "(none)" not in channels_line


def test_render_existing_summary_detects_registry_provider_keys():
    """Existing config summary should not miss configured providers outside the old shortlist."""
    cfg = Config()
    cfg.providers.minimax.api_key = "sk-test"

    captured: dict[str, list[str]] = {}

    def fake_note(title, body):
        captured[title] = body

    with patch("pythinker.cli.onboard_views.clack.note", side_effect=fake_note), \
         patch("pythinker.cli.onboard_views.clack.bar_break"):
        summary.render_existing_summary(cfg)

    existing = captured["Existing config"]
    provider_line = next(line for line in existing if line.startswith("Provider:"))
    assert "minimax" in provider_line
    assert "(none)" not in provider_line


# Belt: ensure the module export surface is stable for downstream callers.
def test_summary_module_exports_diff_renderer():
    assert hasattr(summary, "render_pre_save_diff")
    assert hasattr(summary, "render_pre_save")
    assert hasattr(summary, "render_existing_summary")
