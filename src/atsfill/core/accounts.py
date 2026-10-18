"""Stored sessions for the systems that need an account (ADR 0007)."""

import time
from pathlib import Path

from atsfill.core import browser
from atsfill.core.adapter import NeedsAccount

LOGIN_TIMEOUT_S = 600
POLL_MS = 1000


def session_file(data_dir: Path, adapter: str) -> Path:
    return data_dir / "sessions" / f"{adapter}.json"


def state(adapter: NeedsAccount, session: Path) -> str:
    """`signed_in` or `signed_out` from the system itself, `missing` with no session stored."""
    if not session.exists():
        return "missing"
    with browser.session_request(session) as request:
        return "signed_in" if adapter.signed_in(request) else "signed_out"


def sign_in(adapter: NeedsAccount, data_dir: Path, session: Path) -> str | None:
    """Waits for a person to sign in in the login window, then stores the session.

    Returns the reason it did not: `window_closed` or `login_timeout`; None once stored.
    """
    with browser.login_window(data_dir, adapter.login_url) as page:
        deadline = time.monotonic() + LOGIN_TIMEOUT_S
        try:
            while time.monotonic() < deadline:
                if adapter.signed_in(page.request):
                    browser.save_session(page, session)
                    return None
                page.wait_for_timeout(POLL_MS)
        except browser.BrowserError:
            if page.is_closed():
                return "window_closed"
            raise
    return "login_timeout"
