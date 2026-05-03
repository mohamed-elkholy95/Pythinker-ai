import threading
import time

import pytest

from pythinker.auth.refresh_lock import _lock_path, refresh_lock


def test_lock_path_is_deterministic():
    a = _lock_path("openai-codex", "default")
    b = _lock_path("openai-codex", "default")
    assert a == b


def test_lock_path_distinguishes_providers():
    a = _lock_path("openai-codex", "default")
    b = _lock_path("github-copilot", "default")
    assert a != b


def test_lock_path_distinguishes_profiles():
    a = _lock_path("openai-codex", "alice")
    b = _lock_path("openai-codex", "bob")
    assert a != b


def test_lock_path_under_pythinker_data_dir():
    p = _lock_path("dummy")
    assert "pythinker" in str(p)
    assert "oauth-refresh" in str(p)


def test_refresh_lock_creates_file():
    with refresh_lock("test-provider"):
        path = _lock_path("test-provider")
        assert path.exists()


@pytest.mark.skipif(
    __import__("sys").platform == "win32",
    reason="fcntl.flock not available on Windows",
)
def test_refresh_lock_serializes_concurrent_acquisitions():
    """Two threads holding the same lock must serialize."""
    log = []

    def worker(name: str):
        with refresh_lock("serialize-test"):
            log.append(f"{name}-acquired")
            time.sleep(0.05)
            log.append(f"{name}-released")

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Either order is fine, but acquisition+release of one must complete
    # before the other's acquisition.
    assert log[0].endswith("acquired")
    assert log[1].endswith("released")
    assert log[2].endswith("acquired")
    assert log[3].endswith("released")
    # The two "acquired" events must not be adjacent.
    first_owner = log[0].split("-")[0]
    assert log[1] == f"{first_owner}-released"
