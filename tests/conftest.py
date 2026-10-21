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


@pytest.hookimpl(tryfirst=True)  # before xdist reads the groups
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """A test on a module's browser runs on that module's worker; any other, on any worker."""
    for item in items:
        defs = item._fixtureinfo.name2fixturedefs.values()  # ty: ignore[unresolved-attribute]
        if any(d[-1].scope == "module" for d in defs):
            item.add_marker(pytest.mark.xdist_group(item.module.__name__))  # ty: ignore[unresolved-attribute]


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
