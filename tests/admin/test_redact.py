"""Focused unit tests for ``pythinker.admin.redact``.

Pins the existing redaction-helper behavior after the §A4 split so the
helpers are testable in isolation. These tests document what the code
currently does — they do not drive new behavior.
"""

from __future__ import annotations

from pythinker.admin.redact import _path_value, _redact_url, _redacted_path_is_set


# ---------------------------------------------------------------------------
# _path_value
# ---------------------------------------------------------------------------


def test_path_value_returns_top_level_value() -> None:
    payload = {"providers": {"openai": {"api_key": "set"}}}
    assert _path_value(payload, "providers") == {"openai": {"api_key": "set"}}


def test_path_value_traverses_nested_snake_case_keys() -> None:
    payload = {"providers": {"openai": {"api_key": "set"}}}
    assert _path_value(payload, "providers.openai.api_key") == "set"


def test_path_value_falls_back_to_camel_case_alias() -> None:
    # Disk payloads use camelCase (alias_generator=to_camel); the helper
    # accepts either snake_case or camelCase per segment.
    payload = {"providers": {"openai": {"apiKey": "set"}}}
    assert _path_value(payload, "providers.openai.api_key") == "set"


def test_path_value_returns_none_when_segment_missing() -> None:
    payload = {"providers": {"openai": {}}}
    assert _path_value(payload, "providers.openai.api_key") is None


def test_path_value_returns_none_when_intermediate_is_not_dict() -> None:
    payload = {"providers": "not-a-dict"}
    assert _path_value(payload, "providers.openai.api_key") is None


def test_path_value_returns_none_for_empty_payload() -> None:
    assert _path_value({}, "providers.openai.api_key") is None


# ---------------------------------------------------------------------------
# _redacted_path_is_set
# ---------------------------------------------------------------------------


def test_redacted_path_is_set_true_for_non_empty_string() -> None:
    payload = {"providers": {"openai": {"api_key": "sk-redacted"}}}
    assert _redacted_path_is_set(payload, "providers.openai.api_key") is True


def test_redacted_path_is_set_false_for_none() -> None:
    payload = {"providers": {"openai": {"api_key": None}}}
    assert _redacted_path_is_set(payload, "providers.openai.api_key") is False


def test_redacted_path_is_set_false_for_empty_string() -> None:
    payload = {"providers": {"openai": {"api_key": ""}}}
    assert _redacted_path_is_set(payload, "providers.openai.api_key") is False


def test_redacted_path_is_set_false_for_empty_list_and_dict() -> None:
    payload = {"a": {"empty_list": [], "empty_dict": {}}}
    assert _redacted_path_is_set(payload, "a.empty_list") is False
    assert _redacted_path_is_set(payload, "a.empty_dict") is False


def test_redacted_path_is_set_false_when_path_missing() -> None:
    assert _redacted_path_is_set({}, "providers.openai.api_key") is False


def test_redacted_path_is_set_true_for_zero_and_false() -> None:
    # `value not in (None, "", [], {})` — 0 and False are "set".
    payload = {"a": {"zero": 0, "flag": False}}
    assert _redacted_path_is_set(payload, "a.zero") is True
    assert _redacted_path_is_set(payload, "a.flag") is True


# ---------------------------------------------------------------------------
# _redact_url
# ---------------------------------------------------------------------------


def test_redact_url_returns_none_for_none() -> None:
    assert _redact_url(None) is None


def test_redact_url_returns_none_for_empty_string() -> None:
    assert _redact_url("") is None


def test_redact_url_strips_query_and_fragment() -> None:
    assert (
        _redact_url("https://example.com/path?token=secret#frag")
        == "https://example.com/path"
    )


def test_redact_url_preserves_url_without_query_or_fragment() -> None:
    assert _redact_url("https://example.com/path") == "https://example.com/path"


def test_redact_url_preserves_credentials_in_netloc() -> None:
    # The helper only strips query+fragment; userinfo in the netloc is
    # passed through verbatim. Pinning the actual behavior, not the name.
    assert (
        _redact_url("https://user:pass@example.com/path?q=1#frag")
        == "https://user:pass@example.com/path"
    )


def test_redact_url_returns_none_for_unparseable_input() -> None:
    # urlsplit raises ValueError on malformed IPv6 brackets; the helper
    # swallows it and returns None.
    assert _redact_url("http://[::1") is None
