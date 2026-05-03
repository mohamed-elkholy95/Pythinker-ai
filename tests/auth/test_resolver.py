from unittest.mock import patch

from pythinker.auth import credential_source, is_authenticated, resolve_credential
from pythinker.config.schema import ProviderConfig
from pythinker.providers.registry import ProviderSpec


def _spec(name="dummy", *, is_oauth=False, env_key="DUMMY_KEY", token_app_name=None, token_filename=None):
    return ProviderSpec(
        name=name,
        keywords=(),
        env_key=env_key,
        display_name=name.title(),
        is_oauth=is_oauth,
        token_app_name=token_app_name or "",
        token_filename=token_filename or "",
    )


def _cfg(api_key=None):
    return ProviderConfig(api_key=api_key)


def test_oauth_with_token_returns_it(monkeypatch):
    spec = _spec(is_oauth=True, token_app_name="pythinker", token_filename="x.json")
    with patch("pythinker.auth.oauth_cli_kit.get_token", return_value="oauth-tok"):
        assert resolve_credential(spec, _cfg()) == "oauth-tok"


def test_oauth_without_token_returns_none(monkeypatch):
    spec = _spec(is_oauth=True)
    with patch("pythinker.auth.oauth_cli_kit.get_token", return_value=None):
        assert resolve_credential(spec, _cfg()) is None


def test_api_key_from_config(monkeypatch):
    spec = _spec(is_oauth=False)
    monkeypatch.delenv("DUMMY_KEY", raising=False)
    assert resolve_credential(spec, _cfg(api_key="cfg-key")) == "cfg-key"


def test_api_key_from_env_when_config_empty(monkeypatch):
    spec = _spec(is_oauth=False)
    monkeypatch.setenv("DUMMY_KEY", "env-key")
    assert resolve_credential(spec, _cfg()) == "env-key"


def test_api_key_returns_none_when_neither_set(monkeypatch):
    spec = _spec(is_oauth=False)
    monkeypatch.delenv("DUMMY_KEY", raising=False)
    assert resolve_credential(spec, _cfg()) is None


def test_local_provider_no_env_key_returns_none():
    spec = _spec(env_key=None)
    assert resolve_credential(spec, _cfg()) is None


def test_is_authenticated_truthy(monkeypatch):
    spec = _spec(is_oauth=False)
    monkeypatch.setenv("DUMMY_KEY", "x")
    assert is_authenticated(spec, _cfg())


def test_credential_source_oauth(monkeypatch):
    spec = _spec(is_oauth=True)
    with patch("pythinker.auth._has_token", return_value=True):
        assert credential_source(spec, _cfg()) == "oauth"


def test_credential_source_config():
    spec = _spec(is_oauth=False)
    assert credential_source(spec, _cfg(api_key="x")) == "config"


def test_credential_source_env(monkeypatch):
    spec = _spec(is_oauth=False)
    monkeypatch.setenv("DUMMY_KEY", "x")
    assert credential_source(spec, _cfg()) == "env"


def test_credential_source_none(monkeypatch):
    spec = _spec(is_oauth=False)
    monkeypatch.delenv("DUMMY_KEY", raising=False)
    assert credential_source(spec, _cfg()) == "none"
