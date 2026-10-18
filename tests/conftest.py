import functools
import threading
from collections.abc import Generator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PAGES = Path(__file__).parent / "pages"


@pytest.fixture(scope="session")
def server() -> Generator[str]:
    """Serves tests/pages on localhost; 127.0.0.1 on the same port is a second origin."""
    handler = functools.partial(SimpleHTTPRequestHandler, directory=PAGES)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as httpd:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        yield f"http://localhost:{httpd.server_port}"
        httpd.shutdown()
