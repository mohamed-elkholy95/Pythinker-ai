"""Tiny HTTP server bound to 0.0.0.0:0 for browser integration tests.

Bound to ``0.0.0.0`` so the Chromium running in the Docker container can reach
it via ``host.docker.internal``. Returns the chosen port to the test.
"""

from __future__ import annotations

import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator


class _Handler(SimpleHTTPRequestHandler):
    fixture_dir: Path

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.fixture_dir), **kwargs)

    def log_message(self, fmt, *args):  # silence default stderr logging
        return


def serve(fixture_dir: Path) -> Iterator[int]:
    """Yield the bound port; tear the server down on cleanup."""
    handler_cls = type("Handler", (_Handler,), {"fixture_dir": fixture_dir})
    httpd = ThreadingHTTPServer(("0.0.0.0", 0), handler_cls)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()
