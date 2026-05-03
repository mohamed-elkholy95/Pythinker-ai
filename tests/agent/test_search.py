"""search_sessions: case-insensitive substring scan with snippet centering."""
from pythinker.agent.search import build_snippet, search_sessions


def test_build_snippet_centers_match():
    text = "the quick brown fox jumps over the lazy dog " * 5
    snip, offsets = build_snippet(text, match_start=80, match_end=83, span=60)
    # Snippet must include the match characters at the offsets we report.
    for s, e in offsets:
        assert snip[s:e].lower() == text[80:83].lower()
    assert len(snip) <= 60 + 2  # +2 for the ellipses


def test_build_snippet_no_leading_ellipsis_at_start():
    text = "hello world"
    snip, _ = build_snippet(text, match_start=0, match_end=5, span=120)
    assert snip == "hello world"


def test_search_returns_hits_with_match_offsets():
    sessions = [
        ("websocket:a", [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "Hello there!"},
        ]),
        ("websocket:b", [
            {"role": "user", "content": "foo bar"},
        ]),
    ]
    hits = search_sessions(iter(sessions), query="hello")
    assert len(hits) == 2
    assert hits[0]["session_key"] == "websocket:a"
    assert hits[0]["message_index"] == 0
    assert hits[0]["role"] == "user"
    assert "hello" in hits[0]["snippet"].lower()
    assert hits[0]["match_offsets"][0][1] - hits[0]["match_offsets"][0][0] == 5
    assert hits[1]["session_key"] == "websocket:a"
    assert hits[1]["message_index"] == 1


def test_search_is_case_insensitive():
    sessions = [
        ("websocket:a", [{"role": "user", "content": "Rust release notes"}]),
    ]
    hits = search_sessions(iter(sessions), query="RUST")
    assert len(hits) == 1


def test_search_pagination():
    sessions = [
        ("websocket:a", [
            {"role": "user", "content": "rust"},
            {"role": "user", "content": "rust"},
            {"role": "user", "content": "rust"},
        ]),
    ]
    page1 = search_sessions(iter(sessions), query="rust", limit=2, offset=0)
    page2 = search_sessions(iter(sessions), query="rust", limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 1


def test_search_skips_non_string_content():
    sessions = [
        ("websocket:a", [
            {"role": "tool", "content": ["not", "a", "string"]},
            {"role": "user", "content": "rust"},
        ]),
    ]
    hits = search_sessions(iter(sessions), query="rust")
    assert len(hits) == 1
    assert hits[0]["message_index"] == 1


def test_search_caps_at_hard_limit():
    """No matter how high `limit` goes, the helper enforces 200."""
    sessions = [
        ("websocket:a", [{"role": "user", "content": "rust"}] * 500),
    ]
    hits = search_sessions(iter(sessions), query="rust", limit=10_000)
    assert len(hits) == 200
