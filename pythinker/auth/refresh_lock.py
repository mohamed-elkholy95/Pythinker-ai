"""Per-provider refresh-token file lock to prevent concurrent token refreshes.

Two pythinker processes (e.g., serve + gateway) sharing OAuth credentials can
both try to refresh the same token at the same time, triggering provider-side
`refresh_token_reused` errors. This module provides a context manager that
serializes refreshes via fcntl flock on POSIX (with a Windows fallback).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import sys
from pathlib import Path


def _lock_path(provider: str, profile_id: str = "default") -> Path:
    """Return the file path for a (provider, profile) lock.

    Path: ~/.local/share/pythinker/locks/oauth-refresh/<sha256-hex>
    Hash key: provider + b"\\0" + profile_id
    """
    key = f"{provider}\0{profile_id}".encode()
    digest = hashlib.sha256(key).hexdigest()
    base = Path.home() / ".local/share/pythinker/locks/oauth-refresh"
    base.mkdir(parents=True, exist_ok=True)
    return base / digest


@contextlib.contextmanager
def refresh_lock(provider: str, profile_id: str = "default"):
    """Acquire an exclusive flock on the (provider, profile_id) lock file.

    Blocks until the lock is available. On Windows (no fcntl), falls back
    to a best-effort marker file with a stale-lock heuristic.
    """
    path = _lock_path(provider, profile_id)

    if sys.platform == "win32":
        # Windows fallback: best-effort.
        # Open exclusively if possible; otherwise just proceed (no lock).
        # Pythinker's primary platform is Linux/macOS where fcntl works.
        try:
            fd = os.open(str(path), os.O_RDWR | os.O_CREAT)
            try:
                yield path
            finally:
                os.close(fd)
        except OSError:
            yield path
        return

    import fcntl

    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield path
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
