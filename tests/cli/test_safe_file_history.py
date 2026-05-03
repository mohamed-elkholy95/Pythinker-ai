"""Regression tests for SafeFileHistory (issue #2846).

Surrogate characters in CLI input must not crash history file writes.
"""

from pythinker.cli.commands import SafeFileHistory


class TestSafeFileHistory:
    def test_surrogate_replaced(self, tmp_path):
        """Surrogate pairs are replaced with U+FFFD, not crash."""
        hist = SafeFileHistory(str(tmp_path / "history"))
        hist.store_string("hello \udce9 world")
        entries = list(hist.load_history_strings())
        assert len(entries) == 1
        assert "\udce9" not in entries[0]
        assert "hello" in entries[0]
        assert "world" in entries[0]

    def test_normal_text_unchanged(self, tmp_path):
        hist = SafeFileHistory(str(tmp_path / "history"))
        hist.store_string("normal ascii text")
        entries = list(hist.load_history_strings())
        assert entries[0] == "normal ascii text"

    def test_emoji_preserved(self, tmp_path):
        hist = SafeFileHistory(str(tmp_path / "history"))
        hist.store_string("hello 🐍 pythinker")
        entries = list(hist.load_history_strings())
        assert entries[0] == "hello 🐍 pythinker"

    def test_mixed_unicode_preserved(self, tmp_path):
        """Latin-extended + emoji should pass through cleanly."""
        hist = SafeFileHistory(str(tmp_path / "history"))
        hist.store_string("naïve café — résumé 🎉")
        entries = list(hist.load_history_strings())
        assert entries[0] == "naïve café — résumé 🎉"

    def test_multiple_surrogates(self, tmp_path):
        hist = SafeFileHistory(str(tmp_path / "history"))
        hist.store_string("\udce9\udcf1\udcff")
        entries = list(hist.load_history_strings())
        assert len(entries) == 1
        assert "\udce9" not in entries[0]
