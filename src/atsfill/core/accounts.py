"""Stored sessions for the systems that need an account (ADRs 0007, 0015)."""

import json
import re
import time
from pathlib import Path

from atsfill.core import browser
from atsfill.core.adapter import CreatesAccount, NeedsAccount, SignsIn, Unsupported
from atsfill.core.browser import Page
from atsfill.core.passwords import account_key

LOGIN_TIMEOUT_S = 600
POLL_MS = 1000
SCOPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")  # one path segment
EMAILS = "accounts.json"  # in the user's directory, each account's email


def session_file(user_dir: Path, adapter: str, scope: str | None) -> Path:
    """`sessions/<adapter>.json`, or `sessions/<adapter>/<scope>.json` for a scoped account."""
    if scope is None:
        return user_dir / "sessions" / f"{adapter}.json"
    if not SCOPE.fullmatch(scope):
        raise ValueError(f"{scope!r} is not an account scope")
    return user_dir / "sessions" / adapter / f"{scope}.json"


def stored(user_dir: Path, adapter: str) -> list[tuple[str | None, Path]]:
    """Every session the user stored for the adapter, with its scope."""
    sessions = user_dir / "sessions"
    whole = (
        [(None, sessions / f"{adapter}.json")] if (sessions / f"{adapter}.json").exists() else []
    )
    return whole + [(p.stem, p) for p in sorted((sessions / adapter).glob("*.json"))]


def email(user_dir: Path, adapter: str, scope: str | None) -> str | None:
    path = user_dir / EMAILS
    emails = json.loads(path.read_text()) if path.exists() else {}
    return emails.get(account_key(adapter, scope))


def remember(user_dir: Path, adapter: str, scope: str | None, address: str) -> None:
    path = user_dir / EMAILS
    emails = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps(emails | {account_key(adapter, scope): address}, indent=2))


def sign_in(
    page: Page,
    adapter: SignsIn,
    scope: str | None,
    address: str,
    password: str,
    session: Path,
    create: bool = False,
    name: str | None = None,
) -> None:
    """Signs in, or creates the account (with the user's `name` where it asks one), from the
    system's login page and stores the session.
    Raises what the adapter raises, without the password in it."""
    page.goto(adapter.login_url(scope))
    try:
        if create:
            assert isinstance(adapter, CreatesAccount)
            adapter.sign_up(page, address, password, name)
        else:
            adapter.sign_in(page, address, password)
    except Exception as e:
        if password in str(e):
            raise redacted(e, password) from None
        raise
    if not adapter.signed_in(page.request, scope):
        raise Unsupported("unhandled_rendering: still signed out after signing in")
    browser.save_session(page, session)


def redacted(error: Exception, password: str) -> Exception:
    message = str(error).replace(password, "<password>")
    try:
        return type(error)(message)
    except TypeError:
        return RuntimeError(f"{type(error).__name__}: {message}")


def state(adapter: NeedsAccount, session: Path, scope: str | None) -> str:
    """`signed_in` or `signed_out` from the system itself, `missing` with no session stored."""
    if not session.exists():
        return "missing"
    with browser.session_request(session) as request:
        return "signed_in" if adapter.signed_in(request, scope) else "signed_out"


def wait_for_person(
    adapter: NeedsAccount, scope: str | None, data_dir: Path, session: Path
) -> str | None:
    """Waits for a person to sign in in the login window, then stores the session.

    Returns the reason it did not: `window_closed` or `login_timeout`; None once stored.
    """
    with browser.login_window(data_dir, adapter.login_url(scope)) as page:
        deadline = time.monotonic() + LOGIN_TIMEOUT_S
        try:
            while time.monotonic() < deadline:
                if adapter.signed_in(page.request, scope):
                    browser.save_session(page, session)
                    return None
                page.wait_for_timeout(POLL_MS)
        except browser.BrowserError:
            if page.is_closed():
                return "window_closed"
            raise
    return "login_timeout"
