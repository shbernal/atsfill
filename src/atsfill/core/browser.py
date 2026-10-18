"""The browser launcher, the only module that imports camoufox and playwright (ADR 0002)."""

import fcntl
import json
import locale
import platform
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from camoufox.exceptions import CamoufoxNotInstalled
from camoufox.fingerprints import Screen, generate_fingerprint
from camoufox.pkgman import installed_verstr
from camoufox.sync_api import Camoufox
from playwright.sync_api import APIRequestContext as Request
from playwright.sync_api import Browser, Locator, Page, Response, sync_playwright
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import TimeoutError as Timeout

__all__ = [
    "BrowserError",
    "Covered",
    "Locator",
    "OutOfTime",
    "Page",
    "Request",
    "Response",
    "Timeout",
    "click",
    "install",
    "lock",
    "login_window",
    "open_page",
    "save_session",
    "session_request",
    "version",
]

MIN_SCREEN_HEIGHT = 1050  # leaves the window the 900px Greenhouse and Teamtailor need
HOST_OS = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}[platform.system()]
BUDGET = 180.0  # seconds per command, lock wait included


class OutOfTime(Exception):
    """The command's time budget ran out."""


class Covered(Exception):
    """Another element is on top of the click target; the message describes it."""


def version() -> str:
    """The installed Camoufox build, or `not installed`."""
    try:
        return installed_verstr()
    except CamoufoxNotInstalled:
        return "not installed"


def install() -> int:
    """Fetches the browser build the pinned camoufox release pairs with."""
    return subprocess.call([sys.executable, "-m", "camoufox", "fetch"])


def identity(data_dir: Path) -> dict:
    """The device identity every launch reuses, generated for the host OS on the first one."""
    path = data_dir / "identity.json"
    if path.exists():
        return json.loads(path.read_text())
    fingerprint = generate_fingerprint(os=HOST_OS, screen=Screen(min_height=MIN_SCREEN_HEIGHT))
    path.write_text(json.dumps(fingerprint, default=str))
    path.chmod(0o600)
    return fingerprint


def machine_locale() -> str | None:
    """The machine's locale as a BCP 47 tag; Firefox reads the timezone from the host."""
    tag = locale.getlocale()[0]
    return tag.replace("_", "-") if tag and tag not in ("C", "POSIX") else None


@contextmanager
def lock(data_dir: Path, deadline: float) -> Generator[None]:
    """Holds the data directory; a second command waits here until its deadline."""
    with (data_dir / "lock").open("w") as f:
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise OutOfTime("time budget ran out waiting for another command") from None
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


@contextmanager
def _launch(data_dir: Path, headless: bool) -> Generator[Browser]:
    with Camoufox(
        headless=headless,
        fingerprint=identity(data_dir),
        os=HOST_OS,
        locale=machine_locale(),
        i_know_what_im_doing=True,
    ) as browser:
        assert isinstance(browser, Browser)  # not a persistent context
        yield browser


@contextmanager
def open_page(
    data_dir: Path, session: Path | None = None, seconds: float = BUDGET
) -> Generator[Page]:
    """A page in a fresh context of a fresh headless browser, under the data directory's lock
    and the time budget, closed on exit. `session` seeds the context with one storage state.

    When the budget runs out the browser is closed under the command, and what that breaks
    is raised as `OutOfTime`; a command that handles the breakage itself returns as it is.
    """
    deadline = time.monotonic() + seconds
    with lock(data_dir, deadline), _launch(data_dir, headless=True) as browser:
        expired = threading.Event()

        def expire() -> None:  # on the timer's thread: hand the close to Playwright's loop
            expired.set()
            close = browser._impl_obj.close(reason="time budget ran out")
            browser._loop.call_soon_threadsafe(browser._loop.create_task, close)

        timer = threading.Timer(max(0.0, deadline - time.monotonic()), expire)
        timer.start()
        try:
            context = browser.new_context(storage_state=session)
            yield context.new_page()
            context.close()
        except Exception as e:
            if expired.is_set():
                raise OutOfTime(f"time budget of {seconds:.0f}s ran out") from e
            raise
        finally:
            timer.cancel()


@contextmanager
def login_window(data_dir: Path, url: str, headless: bool = False) -> Generator[Page]:
    """A visible window on `url`, the one browser a person drives (ADR 0007). It takes no
    lock: the session is written once, at the end, and commands meanwhile run as usual."""
    with _launch(data_dir, headless=headless) as browser:
        page = browser.new_context().new_page()
        page.goto(url)
        yield page


def save_session(page: Page, path: Path) -> None:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.touch(mode=0o600)
    page.context.storage_state(path=path)


@contextmanager
def session_request(path: Path) -> Generator[Request]:
    """HTTP requests carrying a stored session's cookies, without a browser."""
    with sync_playwright() as playwright:
        request = playwright.request.new_context(storage_state=path)
        try:
            yield request
        finally:
            request.dispose()


# What sits on top of a point (the element's centre by default), null when the element
# (or the shadow host holding it) is what a click there would reach.
_COVER = """(el, point) => {
    const r = el.getBoundingClientRect();
    const [x, y] = point ?? [r.left + r.width / 2, r.top + r.height / 2];
    const root = el.getRootNode();
    const hit = (root.elementFromPoint ? root : document).elementFromPoint(x, y);
    if (!hit || hit === el || el.contains(hit)) return null;
    for (let n = el; n; n = n.getRootNode().host) if (n === hit) return null;
    const id = hit.id ? `#${hit.id}` : "";
    const cls = typeof hit.className === "string" && hit.className.trim()
        ? "." + hit.className.trim().split(/\\s+/).join(".") : "";
    const text = (hit.innerText || "").trim().replace(/\\s+/g, " ").slice(0, 60);
    return `${hit.tagName.toLowerCase()}${id}${cls}${text ? ` "${text}"` : ""}`;
}"""


def covered_by(target: Locator) -> str | None:
    """What covers the target, in its own document and then at its point in each frame up."""
    target.scroll_into_view_if_needed()
    handle = target.element_handle()
    if cover := handle.evaluate(_COVER, None):
        return cover
    box = target.bounding_box()  # in the top page's viewport
    frame = handle.owner_frame()
    if box is None or frame is None:
        return None
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    while (parent := frame.parent_frame) is not None:
        origin = parent.frame_element().bounding_box() if parent.parent_frame else None
        point = [x - origin["x"], y - origin["y"]] if origin else [x, y]
        if cover := frame.frame_element().evaluate(_COVER, point):
            return cover
        frame = parent
    return None


def click(target: Locator) -> None:
    """Clicks the target, raising `Covered` when something else is on top of it."""
    if cover := covered_by(target):
        raise Covered(cover)
    target.click()
