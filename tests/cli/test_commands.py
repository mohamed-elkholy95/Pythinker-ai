import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from pythinker.bus.events import OutboundMessage
from pythinker.cli.commands import _make_provider, _webui_url_from_channel, app
from pythinker.config.schema import Config
from pythinker.cron.types import CronJob, CronPayload
from pythinker.providers.openai_codex_provider import _strip_model_prefix
from pythinker.providers.registry import find_by_name

runner = CliRunner()


@pytest.fixture(autouse=True)
def _stub_port_preflight(monkeypatch):
    """Disable ``_preflight_port_or_die`` for every gateway/serve test in this
    file. All such tests mock the actual server creation (asyncio.start_server,
    aiohttp.web.run_app, ChannelManager…), so the real preflight would just
    probe a host socket and fail when the dev box happens to have something on
    the test port — which has nothing to do with the behavior under test."""
    monkeypatch.setattr(
        "pythinker.cli.commands._preflight_port_or_die",
        lambda _host, _port, label="Service": None,
    )


class _StopGatewayError(RuntimeError):
    pass


def _strip_ansi(text: str) -> str:
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
    return ansi_escape.sub('', text)


def _stub_wizard_save(monkeypatch, final_cfg=None, should_save=True):
    """Patch run_onboard to return a fixed OnboardResult without prompting."""
    from pythinker.cli.onboard import OnboardResult

    final_cfg = final_cfg or Config()
    monkeypatch.setattr(
        "pythinker.cli.onboard.run_onboard",
        lambda *args, **kwargs: OnboardResult(config=final_cfg, should_save=should_save),
    )


def test_onboard_non_interactive_fresh_install_saves_config(tmp_path, monkeypatch):
    """`pythinker onboard --non-interactive` on a fresh install saves config via the wizard."""
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("pythinker.config.loader.get_config_path", lambda: config_path)
    monkeypatch.setattr("pythinker.channels.registry.discover_all", lambda: {})
    _stub_wizard_save(monkeypatch, should_save=True)

    result = runner.invoke(app, ["onboard", "--non-interactive"])

    assert result.exit_code == 0, result.stdout
    assert config_path.exists()


def test_onboard_non_interactive_discard_does_not_save(tmp_path, monkeypatch):
    """`pythinker onboard --non-interactive` with wizard discard leaves disk untouched."""
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("pythinker.config.loader.get_config_path", lambda: config_path)
    monkeypatch.setattr("pythinker.channels.registry.discover_all", lambda: {})
    _stub_wizard_save(monkeypatch, should_save=False)

    result = runner.invoke(app, ["onboard", "--non-interactive"])

    assert result.exit_code == 0, result.stdout
    out = _strip_ansi(result.stdout)
    assert "Configuration discarded" in out
    assert not config_path.exists()


def test_onboard_help_shows_new_flags():
    result = runner.invoke(app, ["onboard", "--help"])

    assert result.exit_code == 0
    out = _strip_ansi(result.stdout)
    assert "--workspace" in out and "-w" in out
    assert "--config" in out and "-c" in out
    assert "--non-interactive" in out
    assert "--flow" in out
    assert "--auth" in out
    assert "--auth-method" in out
    assert "--yes-security" in out
    assert "--start-gateway" in out
    assert "--skip-gateway" in out
    assert "--reset" in out
    # Old flag must be gone.
    assert "--no-wizard" not in out


def test_onboard_workspace_flag_overrides_workspace(tmp_path, monkeypatch):
    """--workspace writes the resolved path into the saved config."""
    config_path = tmp_path / "config.json"
    workspace_dir = tmp_path / "myworkspace"
    monkeypatch.setattr("pythinker.config.loader.get_config_path", lambda: config_path)
    monkeypatch.setattr("pythinker.channels.registry.discover_all", lambda: {})

    from pythinker.cli.onboard import OnboardResult

    def _fake_run_onboard(cfg, **kwargs):
        # commands.py now forwards --workspace into run_onboard via the
        # `workspace` kwarg (the wizard's _step_workspace applies it as
        # workspace_override). Mirror that here so the saved config reflects
        # what the real wizard would produce.
        ws = kwargs.get("workspace")
        if ws:
            cfg.agents.defaults.workspace = str(Path(ws).expanduser().resolve())
        return OnboardResult(config=cfg, should_save=True)

    monkeypatch.setattr("pythinker.cli.onboard.run_onboard", _fake_run_onboard)

    result = runner.invoke(
        app, ["onboard", "--workspace", str(workspace_dir)]
    )

    assert result.exit_code == 0, result.stdout
    saved = json.loads(config_path.read_text())
    assert saved["agents"]["defaults"]["workspace"] == str(workspace_dir.resolve())


def test_onboard_uses_explicit_config_path(tmp_path, monkeypatch):
    """--config picks where the config file is written, even if outside the default tree."""
    config_path = tmp_path / "instance" / "config.json"
    monkeypatch.setattr("pythinker.channels.registry.discover_all", lambda: {})
    _stub_wizard_save(monkeypatch, should_save=True)

    result = runner.invoke(app, ["onboard", "--config", str(config_path)])

    assert result.exit_code == 0, result.stdout
    assert config_path.exists()


def test_onboard_default_runs_wizard_and_save_persists(tmp_path, monkeypatch):
    """`pythinker onboard` (no flags) launches the wizard; on save, the result hits disk."""
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("pythinker.config.loader.get_config_path", lambda: config_path)
    monkeypatch.setattr("pythinker.channels.registry.discover_all", lambda: {})

    final_cfg = Config()
    final_cfg.agents.defaults.model = "openai/gpt-test"
    _stub_wizard_save(monkeypatch, final_cfg=final_cfg, should_save=True)

    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0, result.stdout
    saved = json.loads(config_path.read_text())
    assert saved["agents"]["defaults"]["model"] == "openai/gpt-test"


def test_onboard_default_wizard_discard_does_not_save(tmp_path, monkeypatch):
    """`pythinker onboard` + wizard returning should_save=False leaves disk untouched."""
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("pythinker.config.loader.get_config_path", lambda: config_path)
    monkeypatch.setattr("pythinker.channels.registry.discover_all", lambda: {})
    _stub_wizard_save(monkeypatch, should_save=False)

    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0, result.stdout
    out = _strip_ansi(result.stdout)
    assert "Configuration discarded" in out
    assert not config_path.exists()


def test_no_args_auto_launches_onboard_when_no_config(tmp_path, monkeypatch):
    """`pythinker` (no args, no config) auto-launches the onboarding wizard."""
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("pythinker.config.loader.get_config_path", lambda: config_path)
    monkeypatch.setattr("pythinker.channels.registry.discover_all", lambda: {})
    _stub_wizard_save(monkeypatch, should_save=False)

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    out = _strip_ansi(result.output)
    assert "Configuration discarded" in out


def test_no_args_shows_help_when_config_exists(tmp_path, monkeypatch):
    """`pythinker` (no args, config present) shows help text instead of launching wizard."""
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    monkeypatch.setattr("pythinker.config.loader.get_config_path", lambda: config_path)

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    out = _strip_ansi(result.output)
    assert "onboard" in out
    assert "Usage:" in out


def test_config_matches_github_copilot_codex_with_hyphen_prefix():
    config = Config()
    config.agents.defaults.model = "github-copilot/gpt-5.3-codex"

    assert config.get_provider_name() == "github_copilot"


def test_config_matches_openai_codex_with_hyphen_prefix():
    config = Config()
    config.agents.defaults.model = "openai-codex/gpt-5.5-mini"

    assert config.get_provider_name() == "openai_codex"


def test_config_dump_excludes_oauth_provider_blocks():
    config = Config()

    providers = config.model_dump(by_alias=True)["providers"]

    assert "openaiCodex" not in providers
    assert "githubCopilot" not in providers


def test_config_matches_explicit_ollama_prefix_without_api_key():
    config = Config()
    config.agents.defaults.model = "ollama/llama3.2"

    assert config.get_provider_name() == "ollama"
    assert config.get_api_base() == "http://localhost:11434/v1"


def test_config_explicit_ollama_provider_uses_default_localhost_api_base():
    config = Config()
    config.agents.defaults.provider = "ollama"
    config.agents.defaults.model = "llama3.2"

    assert config.get_provider_name() == "ollama"
    assert config.get_api_base() == "http://localhost:11434/v1"


def test_config_accepts_camel_case_explicit_provider_name_for_coding_plan():
    config = Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "provider": "volcengineCodingPlan",
                    "model": "doubao-1-5-pro",
                }
            },
            "providers": {
                "volcengineCodingPlan": {
                    "apiKey": "test-key",
                }
            },
        }
    )

    assert config.get_provider_name() == "volcengine_coding_plan"
    assert config.get_api_base() == "https://ark.cn-beijing.volces.com/api/coding/v3"


def test_config_accepts_lm_studio_without_api_key_and_uses_default_localhost_api_base():
    config = Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "provider": "lm_studio",
                    "model": "local-model",
                }
            },
            "providers": {
                "lmStudio": {
                    "apiKey": None,
                }
            },
        }
    )

    assert config.get_provider_name() == "lm_studio"
    assert config.get_api_key() is None
    assert config.get_api_base() == "http://localhost:1234/v1"


def test_find_by_name_accepts_camel_case_and_hyphen_aliases():
    assert find_by_name("volcengineCodingPlan") is not None
    assert find_by_name("volcengineCodingPlan").name == "volcengine_coding_plan"
    assert find_by_name("github-copilot") is not None
    assert find_by_name("github-copilot").name == "github_copilot"


def test_config_explicit_xiaomi_mimo_provider_uses_default_api_base():
    config = Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "provider": "xiaomi_mimo",
                    "model": "MiniMax-M1-80k",
                }
            },
            "providers": {
                "xiaomiMimo": {
                    "apiKey": "test-key",
                }
            },
        }
    )

    assert config.get_provider_name() == "xiaomi_mimo"
    assert config.get_api_base() == "https://api.xiaomimimo.com/v1"


def test_config_auto_detects_xiaomi_mimo_from_model_keyword():
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "auto", "model": "mimo/MiniMax-M1-80k"}},
            "providers": {"xiaomiMimo": {"apiKey": "test-key"}},
        }
    )

    assert config.get_provider_name() == "xiaomi_mimo"
    assert config.get_api_base() == "https://api.xiaomimimo.com/v1"


def test_config_auto_detects_ollama_from_local_api_base():
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "auto", "model": "llama3.2"}},
            "providers": {"ollama": {"apiBase": "http://localhost:11434/v1"}},
        }
    )

    assert config.get_provider_name() == "ollama"
    assert config.get_api_base() == "http://localhost:11434/v1"


def test_config_prefers_ollama_over_vllm_when_both_local_providers_configured():
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "auto", "model": "llama3.2"}},
            "providers": {
                "vllm": {"apiBase": "http://localhost:8000"},
                "ollama": {"apiBase": "http://localhost:11434/v1"},
            },
        }
    )

    assert config.get_provider_name() == "ollama"
    assert config.get_api_base() == "http://localhost:11434/v1"


def test_config_falls_back_to_vllm_when_ollama_not_configured():
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "auto", "model": "llama3.2"}},
            "providers": {
                "vllm": {"apiBase": "http://localhost:8000"},
            },
        }
    )

    assert config.get_provider_name() == "vllm"
    assert config.get_api_base() == "http://localhost:8000"


def test_openai_compat_provider_passes_model_through():
    from pythinker.providers.openai_compat_provider import OpenAICompatProvider

    with patch("pythinker.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(default_model="github-copilot/gpt-5.3-codex")

    assert provider.get_default_model() == "github-copilot/gpt-5.3-codex"


def test_make_provider_uses_github_copilot_backend():
    from pythinker.cli.commands import _make_provider
    from pythinker.config.schema import Config

    config = Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "provider": "github-copilot",
                    "model": "github-copilot/gpt-4.1",
                }
            }
        }
    )

    with patch("pythinker.providers.openai_compat_provider.AsyncOpenAI"):
        provider = _make_provider(config)

    assert provider.__class__.__name__ == "GitHubCopilotProvider"


def test_github_copilot_provider_strips_prefixed_model_name():
    from pythinker.providers.github_copilot_provider import GitHubCopilotProvider

    with patch("pythinker.providers.openai_compat_provider.AsyncOpenAI"):
        provider = GitHubCopilotProvider(default_model="github-copilot/gpt-5.5")

    kwargs = provider._build_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        model="github-copilot/gpt-5.5",
        max_tokens=16,
        temperature=0.1,
        reasoning_effort=None,
        tool_choice=None,
    )

    assert kwargs["model"] == "gpt-5.5"


@pytest.mark.asyncio
async def test_github_copilot_provider_refreshes_client_api_key_before_chat():
    from pythinker.providers.github_copilot_provider import GitHubCopilotProvider

    mock_client = MagicMock()
    mock_client.api_key = "no-key"
    mock_client.chat.completions.create = AsyncMock(return_value={
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })

    with patch("pythinker.providers.openai_compat_provider.AsyncOpenAI", return_value=mock_client):
        provider = GitHubCopilotProvider(default_model="github-copilot/gpt-4")

    provider._get_copilot_access_token = AsyncMock(return_value="copilot-access-token")

    response = await provider.chat(
        messages=[{"role": "user", "content": "hi"}],
        model="github-copilot/gpt-4",
        max_tokens=16,
        temperature=0.1,
    )

    assert response.content == "ok"
    assert provider._client.api_key == "copilot-access-token"
    provider._get_copilot_access_token.assert_awaited_once()
    mock_client.chat.completions.create.assert_awaited_once()


def test_openai_codex_strip_prefix_supports_hyphen_and_underscore():
    assert _strip_model_prefix("openai-codex/gpt-5.5-mini") == "gpt-5.5-mini"
    assert _strip_model_prefix("openai_codex/gpt-5.5-mini") == "gpt-5.5-mini"


def test_make_provider_passes_extra_headers_to_custom_provider():
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "custom", "model": "gpt-4o-mini"}},
            "providers": {
                "custom": {
                    "apiKey": "test-key",
                    "apiBase": "https://example.com/v1",
                    "extraHeaders": {
                        "APP-Code": "demo-app",
                        "x-session-affinity": "sticky-session",
                    },
                }
            },
        }
    )

    with patch("pythinker.providers.openai_compat_provider.AsyncOpenAI") as mock_async_openai:
        _make_provider(config)

    kwargs = mock_async_openai.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["base_url"] == "https://example.com/v1"
    assert kwargs["default_headers"]["APP-Code"] == "demo-app"
    assert kwargs["default_headers"]["x-session-affinity"] == "sticky-session"


@pytest.fixture
def mock_agent_runtime(tmp_path):
    """Mock agent command dependencies for focused CLI tests."""
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "default-workspace")

    with patch("pythinker.config.loader.load_config", return_value=config) as mock_load_config, \
         patch("pythinker.config.loader.resolve_config_env_vars", side_effect=lambda c: c), \
         patch("pythinker.cli.commands.sync_workspace_templates") as mock_sync_templates, \
         patch("pythinker.cli.commands._make_provider", return_value=object()), \
         patch("pythinker.cli.commands._print_agent_response") as mock_print_response, \
         patch("pythinker.bus.queue.MessageBus"), \
         patch("pythinker.cron.service.CronService"), \
         patch("pythinker.agent.loop.AgentLoop") as mock_agent_loop_cls:
        agent_loop = MagicMock()
        agent_loop.channels_config = None
        agent_loop.process_direct = AsyncMock(
            return_value=OutboundMessage(channel="cli", chat_id="direct", content="mock-response"),
        )
        agent_loop.close_mcp = AsyncMock(return_value=None)
        agent_loop.close_browser = AsyncMock(return_value=None)
        mock_agent_loop_cls.return_value = agent_loop

        yield {
            "config": config,
            "load_config": mock_load_config,
            "sync_templates": mock_sync_templates,
            "agent_loop_cls": mock_agent_loop_cls,
            "agent_loop": agent_loop,
            "print_response": mock_print_response,
        }


def test_agent_help_shows_workspace_and_config_options():
    result = runner.invoke(app, ["agent", "--help"])

    assert result.exit_code == 0
    stripped_output = _strip_ansi(result.stdout)
    assert "--workspace" in stripped_output
    assert "-w" in stripped_output
    assert "--config" in stripped_output
    assert "-c" in stripped_output


def test_agent_uses_default_config_when_no_workspace_or_config_flags(mock_agent_runtime):
    result = runner.invoke(app, ["agent", "-m", "hello"])

    assert result.exit_code == 0
    assert mock_agent_runtime["load_config"].call_args.args == (None,)
    assert mock_agent_runtime["sync_templates"].call_args.args == (
        mock_agent_runtime["config"].workspace_path,
    )
    assert mock_agent_runtime["agent_loop_cls"].call_args.kwargs["workspace"] == (
        mock_agent_runtime["config"].workspace_path
    )
    mock_agent_runtime["agent_loop"].process_direct.assert_awaited_once()
    mock_agent_runtime["print_response"].assert_called_once_with(
        "mock-response", render_markdown=True, metadata={},
    )


def test_agent_uses_explicit_config_path(mock_agent_runtime, tmp_path: Path):
    config_path = tmp_path / "agent-config.json"
    config_path.write_text("{}")

    result = runner.invoke(app, ["agent", "-m", "hello", "-c", str(config_path)])

    assert result.exit_code == 0
    assert mock_agent_runtime["load_config"].call_args.args == (config_path.resolve(),)


def test_agent_config_sets_active_path(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    seen: dict[str, Path] = {}

    monkeypatch.setattr(
        "pythinker.config.loader.set_config_path",
        lambda path: seen.__setitem__("config_path", path),
    )
    monkeypatch.setattr("pythinker.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("pythinker.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("pythinker.cli.commands._make_provider", lambda _config: object())
    monkeypatch.setattr("pythinker.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("pythinker.cron.service.CronService", lambda _store: object())

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def process_direct(self, *_args, **_kwargs):
            return OutboundMessage(channel="cli", chat_id="direct", content="ok")

        async def close_mcp(self) -> None:
            return None

        async def close_browser(self) -> None:
            return None

    monkeypatch.setattr("pythinker.agent.loop.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("pythinker.cli.commands._print_agent_response", lambda *_args, **_kwargs: None)

    result = runner.invoke(app, ["agent", "-m", "hello", "-c", str(config_file)])

    assert result.exit_code == 0
    assert seen["config_path"] == config_file.resolve()


def test_agent_uses_workspace_directory_for_cron_store(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "agent-workspace")
    seen: dict[str, Path] = {}

    monkeypatch.setattr("pythinker.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("pythinker.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("pythinker.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("pythinker.cli.commands._make_provider", lambda _config: object())
    monkeypatch.setattr("pythinker.bus.queue.MessageBus", lambda: object())

    class _FakeCron:
        def __init__(self, store_path: Path) -> None:
            seen["cron_store"] = store_path

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def process_direct(self, *_args, **_kwargs):
            return OutboundMessage(channel="cli", chat_id="direct", content="ok")

        async def close_mcp(self) -> None:
            return None

        async def close_browser(self) -> None:
            return None

    monkeypatch.setattr("pythinker.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("pythinker.agent.loop.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("pythinker.cli.commands._print_agent_response", lambda *_args, **_kwargs: None)

    result = runner.invoke(app, ["agent", "-m", "hello", "-c", str(config_file)])

    assert result.exit_code == 0
    assert seen["cron_store"] == config.workspace_path / "cron" / "jobs.json"


def test_agent_workspace_override_does_not_migrate_legacy_cron(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    legacy_dir = tmp_path / "global" / "cron"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "jobs.json"
    legacy_file.write_text('{"jobs": []}')

    override = tmp_path / "override-workspace"
    config = Config()
    seen: dict[str, Path] = {}

    monkeypatch.setattr("pythinker.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("pythinker.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("pythinker.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("pythinker.cli.commands._make_provider", lambda _config: object())
    monkeypatch.setattr("pythinker.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("pythinker.config.paths.get_cron_dir", lambda: legacy_dir)

    class _FakeCron:
        def __init__(self, store_path: Path) -> None:
            seen["cron_store"] = store_path

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def process_direct(self, *_args, **_kwargs):
            return OutboundMessage(channel="cli", chat_id="direct", content="ok")

        async def close_mcp(self) -> None:
            return None

        async def close_browser(self) -> None:
            return None

    monkeypatch.setattr("pythinker.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("pythinker.agent.loop.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("pythinker.cli.commands._print_agent_response", lambda *_args, **_kwargs: None)

    result = runner.invoke(
        app,
        ["agent", "-m", "hello", "-c", str(config_file), "-w", str(override)],
    )

    assert result.exit_code == 0
    assert seen["cron_store"] == override / "cron" / "jobs.json"
    assert legacy_file.exists()
    assert not (override / "cron" / "jobs.json").exists()


def test_agent_custom_config_workspace_does_not_migrate_legacy_cron(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    legacy_dir = tmp_path / "global" / "cron"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "jobs.json"
    legacy_file.write_text('{"jobs": []}')

    custom_workspace = tmp_path / "custom-workspace"
    config = Config()
    config.agents.defaults.workspace = str(custom_workspace)
    seen: dict[str, Path] = {}

    monkeypatch.setattr("pythinker.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("pythinker.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("pythinker.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("pythinker.cli.commands._make_provider", lambda _config: object())
    monkeypatch.setattr("pythinker.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("pythinker.config.paths.get_cron_dir", lambda: legacy_dir)

    class _FakeCron:
        def __init__(self, store_path: Path) -> None:
            seen["cron_store"] = store_path

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def process_direct(self, *_args, **_kwargs):
            return OutboundMessage(channel="cli", chat_id="direct", content="ok")

        async def close_mcp(self) -> None:
            return None

        async def close_browser(self) -> None:
            return None

    monkeypatch.setattr("pythinker.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("pythinker.agent.loop.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr(
        "pythinker.cli.commands._print_agent_response", lambda *_args, **_kwargs: None
    )

    result = runner.invoke(app, ["agent", "-m", "hello", "-c", str(config_file)])

    assert result.exit_code == 0
    assert seen["cron_store"] == custom_workspace / "cron" / "jobs.json"
    assert legacy_file.exists()
    assert not (custom_workspace / "cron" / "jobs.json").exists()


def test_agent_overrides_workspace_path(mock_agent_runtime):
    workspace_path = Path("/tmp/agent-workspace")

    result = runner.invoke(app, ["agent", "-m", "hello", "-w", str(workspace_path)])

    assert result.exit_code == 0
    assert mock_agent_runtime["config"].agents.defaults.workspace == str(workspace_path)
    assert mock_agent_runtime["sync_templates"].call_args.args == (workspace_path,)
    assert mock_agent_runtime["agent_loop_cls"].call_args.kwargs["workspace"] == workspace_path


def test_agent_workspace_override_wins_over_config_workspace(mock_agent_runtime, tmp_path: Path):
    config_path = tmp_path / "agent-config.json"
    config_path.write_text("{}")
    workspace_path = Path("/tmp/agent-workspace")

    result = runner.invoke(
        app,
        ["agent", "-m", "hello", "-c", str(config_path), "-w", str(workspace_path)],
    )

    assert result.exit_code == 0
    assert mock_agent_runtime["load_config"].call_args.args == (config_path.resolve(),)
    assert mock_agent_runtime["config"].agents.defaults.workspace == str(workspace_path)
    assert mock_agent_runtime["sync_templates"].call_args.args == (workspace_path,)
    assert mock_agent_runtime["agent_loop_cls"].call_args.kwargs["workspace"] == workspace_path


def test_agent_hints_about_deprecated_memory_window(mock_agent_runtime, tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"agents": {"defaults": {"memoryWindow": 42}}}))

    result = runner.invoke(app, ["agent", "-m", "hello", "-c", str(config_file)])

    assert result.exit_code == 0
    assert "memoryWindow" in result.stdout
    assert "no longer used" in result.stdout


def test_heartbeat_retains_recent_messages_by_default():
    config = Config()

    assert config.gateway.heartbeat.keep_recent_messages == 8


def _write_instance_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")
    return config_file


def _stop_gateway_provider(_config) -> object:
    raise _StopGatewayError("stop")


def _patch_cli_command_runtime(
    monkeypatch,
    config: Config,
    *,
    set_config_path=None,
    sync_templates=None,
    make_provider=None,
    message_bus=None,
    session_manager=None,
    cron_service=None,
    get_cron_dir=None,
) -> None:
    monkeypatch.setattr(
        "pythinker.config.loader.set_config_path",
        set_config_path or (lambda _path: None),
    )
    monkeypatch.setattr("pythinker.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("pythinker.config.loader.resolve_config_env_vars", lambda c: c)
    monkeypatch.setattr(
        "pythinker.cli.commands.sync_workspace_templates",
        sync_templates or (lambda _path: None),
    )
    monkeypatch.setattr(
        "pythinker.cli.commands._make_provider",
        make_provider or (lambda _config: object()),
    )

    if message_bus is not None:
        monkeypatch.setattr("pythinker.bus.queue.MessageBus", message_bus)
    if session_manager is not None:
        monkeypatch.setattr("pythinker.session.manager.SessionManager", session_manager)
    if cron_service is not None:
        monkeypatch.setattr("pythinker.cron.service.CronService", cron_service)
    if get_cron_dir is not None:
        monkeypatch.setattr("pythinker.config.paths.get_cron_dir", get_cron_dir)


def _patch_serve_runtime(monkeypatch, config: Config, seen: dict[str, object]) -> None:
    pytest.importorskip("aiohttp")

    class _FakeApiApp:
        def __init__(self) -> None:
            self.on_startup: list[object] = []
            self.on_cleanup: list[object] = []

    class _FakeAgentLoop:
        def __init__(self, **kwargs) -> None:
            seen["workspace"] = kwargs["workspace"]

        async def _connect_mcp(self) -> None:
            return None

        async def close_mcp(self) -> None:
            return None

        async def close_browser(self) -> None:
            return None

    def _fake_create_app(agent_loop, model_name: str, request_timeout: float):
        seen["agent_loop"] = agent_loop
        seen["model_name"] = model_name
        seen["request_timeout"] = request_timeout
        return _FakeApiApp()

    def _fake_run_app(api_app, host: str, port: int, print):
        seen["api_app"] = api_app
        seen["host"] = host
        seen["port"] = port

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        message_bus=lambda: object(),
        session_manager=lambda _workspace, *_a, **_kw: object(),
    )
    monkeypatch.setattr("pythinker.agent.loop.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("pythinker.api.server.create_app", _fake_create_app)
    monkeypatch.setattr("aiohttp.web.run_app", _fake_run_app)


def test_gateway_uses_workspace_from_config_by_default(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    seen: dict[str, Path] = {}

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        set_config_path=lambda path: seen.__setitem__("config_path", path),
        sync_templates=lambda path: seen.__setitem__("workspace", path),
        make_provider=_stop_gateway_provider,
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    assert seen["config_path"] == config_file.resolve()
    assert seen["workspace"] == Path(config.agents.defaults.workspace)


def test_gateway_workspace_option_overrides_config(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    override = tmp_path / "override-workspace"
    seen: dict[str, Path] = {}

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        sync_templates=lambda path: seen.__setitem__("workspace", path),
        make_provider=_stop_gateway_provider,
    )

    result = runner.invoke(
        app,
        ["gateway", "--config", str(config_file), "--workspace", str(override)],
    )

    assert isinstance(result.exception, _StopGatewayError)
    assert seen["workspace"] == override
    assert config.workspace_path == override


def test_gateway_uses_workspace_directory_for_cron_store(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    seen: dict[str, Path] = {}

    class _StopCron:
        def __init__(self, store_path: Path) -> None:
            seen["cron_store"] = store_path
            raise _StopGatewayError("stop")

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        message_bus=lambda: object(),
        session_manager=lambda _workspace, *_a, **_kw: object(),
        cron_service=_StopCron,
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    assert seen["cron_store"] == config.workspace_path / "cron" / "jobs.json"


def test_gateway_cron_evaluator_receives_scheduled_reminder_context(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    provider = object()
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    seen: dict[str, object] = {}

    monkeypatch.setattr("pythinker.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("pythinker.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("pythinker.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("pythinker.cli.commands._make_provider", lambda _config: provider)
    monkeypatch.setattr("pythinker.bus.queue.MessageBus", lambda: bus)
    monkeypatch.setattr(
        "pythinker.session.manager.SessionManager",
        lambda _workspace, *_a, **_kw: object(),
    )

    class _FakeCron:
        def __init__(self, _store_path: Path) -> None:
            self.on_job = None
            seen["cron"] = self

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            self.model = "test-model"
            self.tools = {}

        async def process_direct(self, *_args, **_kwargs):
            return OutboundMessage(
                channel="telegram",
                chat_id="user-1",
                content="Time to stretch.",
            )

        async def close_mcp(self) -> None:
            return None

        async def close_browser(self) -> None:
            return None

        async def run(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class _StopAfterCronSetup:
        def __init__(self, *_args, **_kwargs) -> None:
            raise _StopGatewayError("stop")

    async def _capture_evaluate_response(
        response: str,
        task_context: str,
        provider_arg: object,
        model: str,
    ) -> bool:
        seen["response"] = response
        seen["task_context"] = task_context
        seen["provider"] = provider_arg
        seen["model"] = model
        return True

    monkeypatch.setattr("pythinker.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("pythinker.agent.loop.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("pythinker.channels.manager.ChannelManager", _StopAfterCronSetup)
    monkeypatch.setattr(
        "pythinker.utils.evaluator.evaluate_response",
        _capture_evaluate_response,
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    cron = seen["cron"]
    assert isinstance(cron, _FakeCron)
    assert cron.on_job is not None

    job = CronJob(
        id="cron-1",
        name="stretch",
        payload=CronPayload(
            message="Remind me to stretch.",
            deliver=True,
            channel="telegram",
            to="user-1",
        ),
    )

    response = asyncio.run(cron.on_job(job))

    assert response == "Time to stretch."
    assert seen["response"] == "Time to stretch."
    assert seen["provider"] is provider
    assert seen["model"] == "test-model"
    assert seen["task_context"] == (
        "[Scheduled Task] Timer finished.\n\n"
        "Task 'stretch' has been triggered.\n"
        "Scheduled instruction: Remind me to stretch."
    )
    bus.publish_outbound.assert_awaited_once_with(
        OutboundMessage(
            channel="telegram",
            chat_id="user-1",
            content="Time to stretch.",
        )
    )


def test_gateway_cron_job_suppresses_intermediate_progress(
    monkeypatch, tmp_path: Path
) -> None:
    """Cron jobs must pass on_progress=_silent to process_direct so that
    tool hints and streaming deltas are never leaked to the user channel
    before evaluate_response decides whether to deliver."""
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    seen: dict[str, object] = {}

    monkeypatch.setattr("pythinker.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("pythinker.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("pythinker.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("pythinker.cli.commands._make_provider", lambda _config: object())
    monkeypatch.setattr("pythinker.bus.queue.MessageBus", lambda: bus)
    monkeypatch.setattr(
        "pythinker.session.manager.SessionManager",
        lambda _workspace, *_a, **_kw: object(),
    )

    class _FakeCron:
        def __init__(self, _store_path: Path) -> None:
            self.on_job = None
            seen["cron"] = self

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            self.model = "test-model"
            self.tools = {}

        async def process_direct(self, *_args, on_progress=None, **_kwargs):
            seen["on_progress"] = on_progress
            return OutboundMessage(
                channel="telegram",
                chat_id="user-1",
                content="Done.",
            )

        async def close_mcp(self) -> None:
            return None

        async def close_browser(self) -> None:
            return None

        async def run(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class _StopAfterCronSetup:
        def __init__(self, *_args, **_kwargs) -> None:
            raise _StopGatewayError("stop")

    async def _always_reject(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr("pythinker.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("pythinker.agent.loop.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("pythinker.channels.manager.ChannelManager", _StopAfterCronSetup)
    monkeypatch.setattr(
        "pythinker.utils.evaluator.evaluate_response",
        _always_reject,
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])
    assert isinstance(result.exception, _StopGatewayError)

    cron = seen["cron"]
    job = CronJob(
        id="cron-silent-test",
        name="test-silent",
        payload=CronPayload(
            message="Run something.",
            deliver=True,
            channel="telegram",
            to="user-1",
        ),
    )
    response = asyncio.run(cron.on_job(job))

    assert response == "Done."
    # on_progress must be a callable (the _silent noop), not None and not bus_progress
    assert seen["on_progress"] is not None
    assert callable(seen["on_progress"])
    # Verify it actually swallows calls (no side effects)
    asyncio.run(seen["on_progress"]("tool_hint", "🔧 $ echo test"))
    # Nothing published to bus since evaluator rejected
    bus.publish_outbound.assert_not_awaited()


def test_gateway_workspace_override_does_not_migrate_legacy_cron(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = _write_instance_config(tmp_path)
    legacy_dir = tmp_path / "global" / "cron"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "jobs.json"
    legacy_file.write_text('{"jobs": []}')

    override = tmp_path / "override-workspace"
    config = Config()
    seen: dict[str, Path] = {}

    class _StopCron:
        def __init__(self, store_path: Path) -> None:
            seen["cron_store"] = store_path
            raise _StopGatewayError("stop")

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        message_bus=lambda: object(),
        session_manager=lambda _workspace, *_a, **_kw: object(),
        cron_service=_StopCron,
        get_cron_dir=lambda: legacy_dir,
    )

    result = runner.invoke(
        app,
        ["gateway", "--config", str(config_file), "--workspace", str(override)],
    )

    assert isinstance(result.exception, _StopGatewayError)
    assert seen["cron_store"] == override / "cron" / "jobs.json"
    assert legacy_file.exists()
    assert not (override / "cron" / "jobs.json").exists()


def test_gateway_custom_config_workspace_does_not_migrate_legacy_cron(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = _write_instance_config(tmp_path)
    legacy_dir = tmp_path / "global" / "cron"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "jobs.json"
    legacy_file.write_text('{"jobs": []}')

    custom_workspace = tmp_path / "custom-workspace"
    config = Config()
    config.agents.defaults.workspace = str(custom_workspace)
    seen: dict[str, Path] = {}

    class _StopCron:
        def __init__(self, store_path: Path) -> None:
            seen["cron_store"] = store_path
            raise _StopGatewayError("stop")

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        message_bus=lambda: object(),
        session_manager=lambda _workspace, *_a, **_kw: object(),
        cron_service=_StopCron,
        get_cron_dir=lambda: legacy_dir,
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    assert seen["cron_store"] == custom_workspace / "cron" / "jobs.json"
    assert legacy_file.exists()
    assert not (custom_workspace / "cron" / "jobs.json").exists()


def test_migrate_cron_store_moves_legacy_file(tmp_path: Path) -> None:
    """Legacy global jobs.json is moved into the workspace on first run."""
    from pythinker.cli.commands import _migrate_cron_store

    legacy_dir = tmp_path / "global" / "cron"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "jobs.json"
    legacy_file.write_text('{"jobs": []}')

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    workspace_cron = config.workspace_path / "cron" / "jobs.json"

    with patch("pythinker.config.paths.get_cron_dir", return_value=legacy_dir):
        _migrate_cron_store(config)

    assert workspace_cron.exists()
    assert workspace_cron.read_text() == '{"jobs": []}'
    assert not legacy_file.exists()


def test_migrate_cron_store_skips_when_workspace_file_exists(tmp_path: Path) -> None:
    """Migration does not overwrite an existing workspace cron store."""
    from pythinker.cli.commands import _migrate_cron_store

    legacy_dir = tmp_path / "global" / "cron"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "jobs.json").write_text('{"old": true}')

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    workspace_cron = config.workspace_path / "cron" / "jobs.json"
    workspace_cron.parent.mkdir(parents=True)
    workspace_cron.write_text('{"new": true}')

    with patch("pythinker.config.paths.get_cron_dir", return_value=legacy_dir):
        _migrate_cron_store(config)

    assert workspace_cron.read_text() == '{"new": true}'


def test_gateway_uses_configured_port_when_cli_flag_is_missing(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.gateway.port = 18791

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        make_provider=_stop_gateway_provider,
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGatewayError)
    assert "port 18791" in result.stdout


def test_gateway_cli_port_overrides_configured_port(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.gateway.port = 18791

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        make_provider=_stop_gateway_provider,
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file), "--port", "18792"])

    assert isinstance(result.exception, _StopGatewayError)
    assert "port 18792" in result.stdout


def test_gateway_webui_url_uses_localhost_for_wildcard_bind() -> None:
    channel = SimpleNamespace(
        config={
            "host": "0.0.0.0",
            "port": 8765,
            "ssl_certfile": "",
            "ssl_keyfile": "",
        }
    )

    assert _webui_url_from_channel(channel) == "http://127.0.0.1:8765/"


def test_gateway_webui_url_uses_https_when_tls_configured() -> None:
    channel = SimpleNamespace(
        config=SimpleNamespace(
            host="::1",
            port=9443,
            ssl_certfile="/tmp/cert.pem",
            ssl_keyfile="/tmp/key.pem",
        )
    )

    assert _webui_url_from_channel(channel) == "https://[::1]:9443/"


def test_gateway_health_endpoint_binds_and_serves_expected_responses(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.gateway.port = 18791
    captured: dict[str, object] = {}

    class _FakeDream:
        model = None
        max_batch_size = 0
        max_iterations = 0

        async def run(self) -> None:
            return None

    class _FakeSessionManager:
        def flush_all(self) -> int:
            return 0

    class _FakeAgentLoop:
        def __init__(self, **_kwargs) -> None:
            self.model = "test-model"
            self.dream = _FakeDream()
            self.sessions = _FakeSessionManager()

        async def run(self) -> None:
            await asyncio.Event().wait()

        async def close_mcp(self) -> None:
            return None

        async def close_browser(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class _FakeChannelManager:
        def __init__(self, _config, _bus, **_kwargs) -> None:
            self.channels = {}
            self.enabled_channels = ["telegram", "discord"]

        async def start_all(self) -> None:
            await asyncio.Event().wait()

        async def stop_all(self) -> None:
            return None

    class _FakeCronService:
        def __init__(self, _store_path: Path) -> None:
            self.on_job = None

        async def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def status(self) -> dict[str, int]:
            return {"jobs": 0}

        def register_system_job(self, _job) -> None:
            return None

    class _FakeHeartbeatService:
        def __init__(self, **_kwargs) -> None:
            return None

        async def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class _FakeServer:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def serve_forever(self) -> None:
            raise _StopGatewayError("stop")

    async def _fake_start_server(handler, host: str, port: int):
        captured["handler"] = handler
        captured["host"] = host
        captured["port"] = port
        return _FakeServer()

    class _FakeReader:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        async def read(self, _size: int) -> bytes:
            return self.payload

    class _FakeWriter:
        def __init__(self) -> None:
            self.output = b""
            self.closed = False

        def write(self, data: bytes) -> None:
            self.output += data

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    _patch_cli_command_runtime(
        monkeypatch,
        config,
        message_bus=lambda: object(),
        session_manager=lambda _workspace, *_a, **_kw: object(),
    )
    monkeypatch.setattr("pythinker.agent.loop.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("pythinker.channels.manager.ChannelManager", _FakeChannelManager)
    monkeypatch.setattr("pythinker.cron.service.CronService", _FakeCronService)
    monkeypatch.setattr("pythinker.heartbeat.service.HeartbeatService", _FakeHeartbeatService)
    monkeypatch.setattr("asyncio.start_server", _fake_start_server)

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert result.exit_code == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 18791
    assert "Health endpoint: http://127.0.0.1:18791/health" in result.stdout
    assert "WebUI: disabled" in result.stdout

    def _call_handler(path: str) -> tuple[str, _FakeWriter]:
        request = f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
        writer = _FakeWriter()
        handler = captured["handler"]
        assert callable(handler)
        asyncio.run(handler(_FakeReader(request), writer))
        return writer.output.decode(), writer

    root_response, root_writer = _call_handler("/")
    assert root_writer.closed is True
    assert "HTTP/1.0 404 Not Found" in root_response
    assert root_response.endswith("\r\n\r\nNot Found")

    health_response, health_writer = _call_handler("/health")
    assert health_writer.closed is True
    assert "HTTP/1.0 200 OK" in health_response
    health_body = json.loads(health_response.split("\r\n\r\n", 1)[1])
    assert health_body == {"status": "ok"}

    missing_response, missing_writer = _call_handler("/missing")
    assert missing_writer.closed is True
    assert "HTTP/1.0 404 Not Found" in missing_response
    assert missing_response.endswith("\r\n\r\nNot Found")


def test_serve_uses_api_config_defaults_and_workspace_override(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    config.api.host = "127.0.0.2"
    config.api.port = 18900
    config.api.timeout = 45.0
    override_workspace = tmp_path / "override-workspace"
    seen: dict[str, object] = {}

    _patch_serve_runtime(monkeypatch, config, seen)

    result = runner.invoke(
        app,
        ["serve", "--config", str(config_file), "--workspace", str(override_workspace)],
    )

    assert result.exit_code == 0
    assert seen["workspace"] == override_workspace
    assert seen["host"] == "127.0.0.2"
    assert seen["port"] == 18900
    assert seen["request_timeout"] == 45.0


def test_serve_cli_options_override_api_config(monkeypatch, tmp_path: Path) -> None:
    config_file = _write_instance_config(tmp_path)
    config = Config()
    config.api.host = "127.0.0.2"
    config.api.port = 18900
    config.api.timeout = 45.0
    seen: dict[str, object] = {}

    _patch_serve_runtime(monkeypatch, config, seen)

    result = runner.invoke(
        app,
        [
            "serve",
            "--config",
            str(config_file),
            "--host",
            "127.0.0.1",
            "--port",
            "18901",
            "--timeout",
            "46",
        ],
    )

    assert result.exit_code == 0
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 18901
    assert seen["request_timeout"] == 46.0


def test_channels_login_requires_channel_name() -> None:
    result = runner.invoke(app, ["channels", "login"])

    assert result.exit_code == 2
