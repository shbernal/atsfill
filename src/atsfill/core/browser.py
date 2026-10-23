"""The browser launcher, the only module that imports camoufox and playwright (ADR 0002)."""

import asyncio
import fcntl
import json
import locale
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from concurrent.futures import Future
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request as HttpRequest
from urllib.request import urlopen

from camoufox.addons import ADDONS_DIR, DefaultAddons, get_addon_path, maybe_download_addons
from camoufox.exceptions import CamoufoxNotInstalled
from camoufox.fingerprints import Screen, generate_fingerprint
from camoufox.pkgman import installed_verstr
from camoufox.sync_api import Camoufox
from playwright.sync_api import APIRequestContext as Request
from playwright.sync_api import Browser, Locator, Page, Response, sync_playwright
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import TimeoutError as Timeout

__all__ = [
    "BUILD",
    "BrowserError",
    "Overlaid",
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
LOCK_POLL_S = 0.1
HTTP_TIMEOUT_S = 20  # per request to a hiring system's API
BUILD = "156.0.1-beta.36"  # the Camoufox browser build, pinned so results compare across machines
BLOCKER = ADDONS_DIR / "UBO-annoyances"  # Camoufox's uBlock Origin, its annoyance lists on
BLOCKER_LISTS = "annoyances"  # the group of uBlock Origin lists: cookie banners, pop-ups, widgets
# The adapters' filters for the banners the lists miss: a list of the blocker's, on like them.
FILTERS = "assets/atsfill/filters.txt"
INCLUDE = re.compile(r"^!#include (\S+)$", re.M)
# A page holding a banner the lists hide: until they do, the blocker is still starting.
PROBE = ("https://blocker.invalid/", '<div id="cookie-banner">probe</div>')
BLOCKER_TIMEOUT_MS = 30_000  # its lists take seconds to compile on each launch, more under load
OVERLAY_GRACE_MS = 1_000  # how long a cover may stay on a click target before it counts
OVERLAY_POLL_MS = 100


class OutOfTime(Exception):
    """The command's time budget ran out."""


class Overlaid(Exception):
    """An overlay is on top of the click target; the message describes it."""


def version() -> str:
    """The installed Camoufox build, or `not installed`."""
    try:
        return installed_verstr()
    except CamoufoxNotInstalled:
        return "not installed"


def install() -> int:
    """Fetches the pinned browser build unless it is installed, then the add-ons every launch
    loads, so that browsers started side by side do not each download them."""
    if version() != BUILD:
        subprocess.call([sys.executable, "-m", "camoufox", "fetch", f"official/{BUILD}"])
    if version() != BUILD:  # `camoufox fetch` exits 0 when it fails
        print(f"the Camoufox build {BUILD} is not installed", file=sys.stderr)
        return 1
    maybe_download_addons(list(DefaultAddons))
    try:
        _build_blocker()
    except OSError as e:
        print(f"the annoyance lists could not be fetched: {e}", file=sys.stderr)
        return 1
    return 0


def _build_blocker() -> None:
    """Copies Camoufox's uBlock Origin to `BLOCKER` with only its annoyance lists on, read
    from disk: fetched by each launch, they would hold its first page for seconds, and each
    list on is compiled by each launch before the blocker starts."""
    ADDONS_DIR.mkdir(parents=True, exist_ok=True)
    with (ADDONS_DIR / f"{BLOCKER.name}.lock").open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        build = BLOCKER.with_name(f"{BLOCKER.name}.build")
        shutil.rmtree(build, ignore_errors=True)
        shutil.copytree(get_addon_path(DefaultAddons.UBO.name), build)
        assets = build / "assets" / "assets.json"
        entries = json.loads(assets.read_text())
        for key, entry in entries.items():
            if entry.get("group") != BLOCKER_LISTS:
                if entry.get("content") == "filters":  # ads and trackers: compiled for nothing
                    entry["off"] = True
                continue
            urls = entry["contentURL"]
            urls = [urls] if isinstance(urls, str) else urls
            local = f"assets/{BLOCKER_LISTS}/{key}.txt"
            (build / local).parent.mkdir(exist_ok=True)
            (build / local).write_text(_filter_list([*urls, *entry.get("cdnURLs", [])]))
            entry.pop("off", None)  # on from the start, the way a fresh profile reads it
            entry["contentURL"] = [local, *urls]
        entries["atsfill"] = {"content": "filters", "group": "custom", "title": "atsfill",
                              "contentURL": [FILTERS]}  # fmt: skip
        (build / FILTERS).parent.mkdir()
        (build / FILTERS).touch()
        assets.write_text(json.dumps(entries))
        shutil.rmtree(BLOCKER, ignore_errors=True)
        build.rename(BLOCKER)


def _filter_list(urls: list[str]) -> str:
    """A filter list from the first of its sources that answers, its includes inlined."""
    for url in (u for u in urls if u.startswith("https://")):
        try:
            return INCLUDE.sub(lambda m, base=url: _fetch(urljoin(base, m[1])), _fetch(url))
        except OSError:
            continue
    raise OSError(f"no source answered for {urls[0]}")


def _fetch(url: str) -> str:
    request = HttpRequest(url, headers={"User-Agent": "Mozilla/5.0"})  # some refuse urllib's
    with urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return response.read().decode()


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
                time.sleep(LOCK_POLL_S)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


@contextmanager
def _launch(data_dir: Path, headless: bool) -> Generator[Browser]:
    if not (BLOCKER / FILTERS).exists():  # installed before the blocker, or its filters, were
        maybe_download_addons(list(DefaultAddons))
        _build_blocker()
    _write_filters()
    with Camoufox(
        headless=headless,
        addons=[str(BLOCKER)],
        exclude_addons=list(DefaultAddons),
        fingerprint=identity(data_dir),
        os=HOST_OS,
        locale=machine_locale(),
        i_know_what_im_doing=True,
    ) as browser:
        assert isinstance(browser, Browser)  # not a persistent context
        _wait_for_blocker(browser)
        yield browser


def _write_filters() -> None:
    """Writes the adapters' filters into the blocker when they changed, in one rename so that
    a launch beside it reads either version whole."""
    from atsfill.adapters import filters  # adapters import this module

    path = BLOCKER / FILTERS
    text = "".join(f"{line}\n" for line in filters())
    if path.read_text() != text:
        staged = path.with_suffix(f".{threading.get_native_id()}")
        staged.write_text(text)
        staged.replace(path)


def _wait_for_blocker(browser: Browser) -> None:
    """Returns once the blocker hides banners: until then a page shows them, and a fresh
    browser starts on the posting itself."""
    context = browser.new_context()
    url, html = PROBE
    context.route(url, lambda route: route.fulfill(content_type="text/html", body=html))
    page = context.new_page()
    page.goto(url)
    try:
        page.locator("#cookie-banner").wait_for(state="hidden", timeout=BLOCKER_TIMEOUT_MS)
    except Timeout:
        raise BrowserError("the blocker hid no banner: run `atsfill install-browser`") from None
    finally:
        context.close()


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
        closing: list[Future] = []

        def expire() -> None:  # on the timer's thread: hand the close to Playwright's loop
            expired.set()
            close = browser._impl_obj.close(reason="time budget ran out")
            closing.append(asyncio.run_coroutine_threadsafe(close, browser._loop))

        async def closed() -> None:
            await asyncio.wrap_future(closing[0])

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
            timer.join()
            if closing:  # Playwright stops on exit, so the close ends first
                browser._sync(closed())


@contextmanager
def login_window(data_dir: Path, url: str, headless: bool = False) -> Generator[Page]:
    """A visible window on `url`, the one browser a person drives (ADR 0007). It takes no
    lock: the session is written once, at the end, and commands meanwhile run as usual."""
    with _launch(data_dir, headless=headless) as browser:
        page = browser.new_context().new_page()
        page.goto(url)
        yield page


def save_session(page: Page, path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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


# What sits on top of a point, null when the element (or the shadow host holding it) is what
# a click there would reach. The point defaults to where Playwright clicks: the centre of the
# first box, which for wrapped inline text is its first line, not the bounding box's centre.
_OVERLAY = """(el, point) => {
    const r = [...el.getClientRects()].find(b => b.width && b.height)
        ?? el.getBoundingClientRect();
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


def overlay_on(target: Locator) -> str | None:
    """The overlay on the target, in its own document and then at its point in each frame up."""
    target.scroll_into_view_if_needed()
    handle = target.element_handle()
    if overlay := handle.evaluate(_OVERLAY, None):
        return overlay
    box = target.bounding_box()  # in the top page's viewport
    frame = handle.owner_frame()
    if box is None or frame is None:
        return None
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    while (parent := frame.parent_frame) is not None:
        origin = parent.frame_element().bounding_box() if parent.parent_frame else None
        point = [x - origin["x"], y - origin["y"]] if origin else [x, y]
        if overlay := frame.frame_element().evaluate(_OVERLAY, point):
            return overlay
        frame = parent
    return None


def click(target: Locator) -> None:
    """Clicks the target, raising `Overlaid` when an overlay stays on top of it for the grace
    period: a toast or a spinner that goes within it is waited out."""
    deadline = time.monotonic() + OVERLAY_GRACE_MS / 1000
    while overlay := overlay_on(target):
        if time.monotonic() >= deadline:
            raise Overlaid(overlay)
        target.page.wait_for_timeout(OVERLAY_POLL_MS)
    target.click()
