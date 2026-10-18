"""The browser launcher, the only module that imports camoufox and playwright (ADR 0002)."""

import json
import platform
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from camoufox.fingerprints import Screen, generate_fingerprint
from camoufox.sync_api import Camoufox
from playwright.sync_api import Browser, Page, Response
from playwright.sync_api import TimeoutError as Timeout

__all__ = ["Page", "Response", "Timeout", "open_page"]

MIN_SCREEN_HEIGHT = 1050  # leaves the window the 900px Greenhouse and Teamtailor need
HOST_OS = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}[platform.system()]


def identity(data_dir: Path) -> dict:
    """The device identity every launch reuses, generated for the host OS on the first one."""
    path = data_dir / "identity.json"
    if path.exists():
        return json.loads(path.read_text())
    fingerprint = generate_fingerprint(os=HOST_OS, screen=Screen(min_height=MIN_SCREEN_HEIGHT))
    path.write_text(json.dumps(fingerprint, default=str))
    path.chmod(0o600)
    return fingerprint


@contextmanager
def open_page(data_dir: Path) -> Generator[Page]:
    """A page in a fresh context of a fresh logged-out headless browser, closed on exit."""
    with Camoufox(
        headless=True,
        fingerprint=identity(data_dir),
        os=HOST_OS,
        i_know_what_im_doing=True,
    ) as browser:
        assert isinstance(browser, Browser)  # not a persistent context
        context = browser.new_context()
        try:
            yield context.new_page()
        finally:
            context.close()
