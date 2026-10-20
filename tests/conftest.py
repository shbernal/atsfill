import functools
import os
import threading
from collections.abc import Generator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.checkout import MAIN

PAGES = Path(__file__).parent / "pages"


class Pages(SimpleHTTPRequestHandler):
    """tests/pages, and `/me`: 200 with the cookie login.html sets, 401 without."""

    def do_GET(self) -> None:
        if self.path != "/me":
            return super().do_GET()
        signed_in = "session=ok" in (self.headers.get("Cookie") or "")
        self.send_response(200 if signed_in else 401)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture(scope="session")
def server() -> Generator[str]:
    """Serves tests/pages on localhost; 127.0.0.1 on the same port is a second origin."""
    handler = functools.partial(Pages, directory=PAGES)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as httpd:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        yield f"http://localhost:{httpd.server_port}"
        httpd.shutdown()


def _load_env() -> None:
    """Live tests read their postings and answers from the gitignored .env."""
    path = MAIN / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


_load_env()
