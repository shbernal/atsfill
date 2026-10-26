import functools
import threading
from collections.abc import Generator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import keyring
import keyring.backend
import keyring.errors
import pytest

from atsfill.core import browser

PAGES = Path(__file__).parent / "pages"


class Pages(SimpleHTTPRequestHandler):
    """tests/pages; `/me`: 200 with the cookie login.html sets, 401 without; `/go/<path>`: a
    301 to `/<path>`, unquoted."""

    def do_GET(self) -> None:
        if self.path.startswith("/go/"):
            self.send_response(301)
            self.send_header("Location", unquote(self.path.removeprefix("/go")))
            self.send_header("Content-Length", "0")
            return self.end_headers()
        if self.path != "/me":
            return super().do_GET()
        signed_in = "session=ok" in (self.headers.get("Cookie") or "")
        self.send_response(200 if signed_in else 401)
        self.send_header("Content-Length", "0")
        self.end_headers()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shard", help="i/n: runs the i-th of n parts of the suite")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deals the suite into parts in turn: the tests on a module's browser as one, then the
    rest one by one."""
    if not (shard := config.getoption("shard")):
        return
    i, n = map(int, shard.split("/"))
    units: dict[str, list[pytest.Item]] = {}
    for item in items:
        defs = item._fixtureinfo.name2fixturedefs.values()  # ty: ignore[unresolved-attribute]
        shared = any(d[-1].scope == "module" for d in defs)
        units.setdefault(item.module.__name__ if shared else item.nodeid, []).append(item)  # ty: ignore[unresolved-attribute]
    order = sorted(units, key=lambda key: ("::" in key, key))
    kept = {id(t) for k, key in enumerate(order) if k % n == i - 1 for t in units[key]}
    config.hook.pytest_deselected(items=[t for t in items if id(t) not in kept])
    items[:] = [t for t in items if id(t) in kept]


class MemoryKeyring(keyring.backend.KeyringBackend):
    """A secret store held in memory: no test reaches the home's, and CI has none."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[service, username] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.passwords.pop((service, username), None) is None:
            raise keyring.errors.PasswordDeleteError(username)


@pytest.fixture(scope="session", autouse=True)
def offline() -> Generator[None]:
    """Every host name resolves to this machine, so a request no route serves fails here
    instead of reaching a live site: a redirect `route.fulfill` answers is followed outside
    the context's routes."""
    launch = functools.partial(
        browser.Camoufox, firefox_user_prefs={"network.dns.forceResolve": "127.0.0.1"}
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(browser, "Camoufox", launch)
        yield


@pytest.fixture(autouse=True)
def secret_store() -> Generator[MemoryKeyring]:
    store, previous = MemoryKeyring(), keyring.get_keyring()
    keyring.set_keyring(store)
    yield store
    keyring.set_keyring(previous)


@pytest.fixture(scope="session")
def server() -> Generator[str]:
    """Serves tests/pages on localhost; 127.0.0.1 on the same port is a second origin."""
    handler = functools.partial(Pages, directory=PAGES)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as httpd:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        yield f"http://localhost:{httpd.server_port}"
        httpd.shutdown()
