from unittest.mock import MagicMock, patch

import pytest

from pythinker.auth.oauth_remote import (
    _SSH_HINT,
    StateValidationError,
    parse_pasted,
    run_oauth_with_hint,
    run_oauth_with_timeout_fallback,
)


def test_parse_pasted_bare_code():
    assert parse_pasted("abc123") == ("abc123", None)


def test_parse_pasted_full_url():
    url = "https://localhost:1455/auth/callback?code=xyz789&state=stateval"
    assert parse_pasted(url) == ("xyz789", "stateval")


def test_parse_pasted_strips_whitespace():
    assert parse_pasted("  abc123  \n") == ("abc123", None)


def test_parse_pasted_empty_raises():
    with pytest.raises(ValueError):
        parse_pasted("   ")


class _FakeClient:
    def __init__(self, *, callback_succeeds=True, raise_on_exchange=False):
        self.state = "expected-state"
        self.auth_url = "https://auth.example.com/oauth/authorize"
        self._callback_succeeds = callback_succeeds
        self._raise_on_exchange = raise_on_exchange

    def wait_for_callback(self, timeout):
        if self._callback_succeeds:
            return "browser-token"
        from pythinker.auth.oauth_remote import CallbackTimeoutError

        raise CallbackTimeoutError()

    def exchange_code(self, code):
        if self._raise_on_exchange:
            raise RuntimeError("network down")
        return f"token-from-code-{code}"


def test_browser_succeeds_returns_token(capsys):
    client = _FakeClient(callback_succeeds=True)
    fn = MagicMock(return_value=None)
    fn.__self__ = client
    token = run_oauth_with_timeout_fallback(fn, timeout=1.0, panel_text="info")
    assert token == "browser-token"


def test_browser_timeout_paste_bare_code_rejected(monkeypatch):
    """B-7: pasting a bare code (state=None) must be rejected, not silently accepted.

    The CSRF defense for the paste path requires the full redirect URL so the
    state token is present and can be validated against client.state.
    """
    client = _FakeClient(callback_succeeds=False)
    fn = MagicMock(return_value=None)
    fn.__self__ = client

    with patch("pythinker.auth.oauth_remote._prompt_pasted", return_value="codeval"):
        with pytest.raises(StateValidationError, match="State missing"):
            run_oauth_with_timeout_fallback(fn, timeout=0.05, panel_text="info")


def test_browser_timeout_paste_full_url(monkeypatch):
    client = _FakeClient(callback_succeeds=False)
    fn = MagicMock(return_value=None)
    fn.__self__ = client

    pasted = "https://localhost:1455/auth/callback?code=urlcode&state=expected-state"
    with patch("pythinker.auth.oauth_remote._prompt_pasted", return_value=pasted):
        token = run_oauth_with_timeout_fallback(fn, timeout=0.05, panel_text="info")
    assert token == "token-from-code-urlcode"


def test_state_mismatch_raises():
    client = _FakeClient(callback_succeeds=False)
    fn = MagicMock(return_value=None)
    fn.__self__ = client

    bad_url = "https://localhost:1455/auth/callback?code=x&state=phisher"
    with patch("pythinker.auth.oauth_remote._prompt_pasted", return_value=bad_url):
        with pytest.raises(StateValidationError):
            run_oauth_with_timeout_fallback(fn, timeout=0.05, panel_text="info")


def test_state_compared_with_compare_digest():
    """B-7: state comparison must use ``secrets.compare_digest`` (constant-time).

    We monkeypatch ``secrets.compare_digest`` and assert it's invoked with the
    pasted state and the client's expected state, then forwards its bool result.
    """
    client = _FakeClient(callback_succeeds=False)
    fn = MagicMock(return_value=None)
    fn.__self__ = client

    pasted = "https://localhost:1455/auth/callback?code=urlcode&state=expected-state"
    calls: list[tuple[str, str]] = []

    def fake_compare_digest(a, b):
        calls.append((a, b))
        return a == b

    with patch("pythinker.auth.oauth_remote._prompt_pasted", return_value=pasted):
        with patch(
            "pythinker.auth.oauth_remote.secrets.compare_digest",
            side_effect=fake_compare_digest,
        ):
            token = run_oauth_with_timeout_fallback(fn, timeout=0.05, panel_text="info")

    assert token == "token-from-code-urlcode"
    assert calls == [("expected-state", "expected-state")]


def test_state_url_missing_state_param_rejected():
    """B-7: a redirect URL missing the ``state`` query param must be rejected.

    Regression guard for the previous ``state is not None and state != client.state``
    bypass — a URL without state parses to state=None and would have skipped the
    CSRF check.
    """
    client = _FakeClient(callback_succeeds=False)
    fn = MagicMock(return_value=None)
    fn.__self__ = client

    bad_url = "https://localhost:1455/auth/callback?code=x"  # no state param
    with patch("pythinker.auth.oauth_remote._prompt_pasted", return_value=bad_url):
        with pytest.raises(StateValidationError, match="State missing"):
            run_oauth_with_timeout_fallback(fn, timeout=0.05, panel_text="info")


# ---------------------------------------------------------------------------
# run_oauth_with_hint tests
# ---------------------------------------------------------------------------

def _make_fake_login(return_value="tok"):
    """Return a callable that records calls and returns `return_value`."""
    def fake_login(*, print_fn, prompt_fn):
        return return_value
    return fake_login


def test_run_oauth_with_hint_emits_ssh_hint():
    """The SSH hint must be printed before the login function is called."""
    printed: list[str] = []
    login_fn = _make_fake_login("tok")
    run_oauth_with_hint(login_fn, print_fn=printed.append, prompt_fn=lambda s: "")
    assert printed and _SSH_HINT in printed[0]


def test_run_oauth_with_hint_returns_login_result():
    """The token returned by login_fn must be forwarded to the caller."""
    fake_token = object()
    login_fn = _make_fake_login(fake_token)
    result = run_oauth_with_hint(login_fn, print_fn=lambda s: None, prompt_fn=lambda s: "")
    assert result is fake_token


def test_run_oauth_with_hint_passes_print_and_prompt_fns():
    """print_fn and prompt_fn must be forwarded verbatim to login_fn."""
    received: dict = {}

    def recording_login(*, print_fn, prompt_fn):
        received["print_fn"] = print_fn
        received["prompt_fn"] = prompt_fn
        return "t"

    my_print = lambda s: None  # noqa: E731
    my_prompt = lambda s: ""   # noqa: E731
    run_oauth_with_hint(recording_login, print_fn=my_print, prompt_fn=my_prompt)
    assert received["print_fn"] is my_print
    assert received["prompt_fn"] is my_prompt


def test_run_oauth_with_hint_custom_ssh_hint():
    """Callers can supply a provider-specific ssh_hint override."""
    printed: list[str] = []
    custom = "Custom hint for tests"
    run_oauth_with_hint(
        _make_fake_login(),
        print_fn=printed.append,
        prompt_fn=lambda s: "",
        ssh_hint=custom,
    )
    assert custom in printed[0]
