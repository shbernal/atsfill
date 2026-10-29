"""Stored sessions for the systems that need an account (ADRs 0007, 0015)."""

import json
import re
import time
from pathlib import Path

from atsfill.core import browser
from atsfill.core.adapter import (
    CreatesAccount,
    CreatesAccountByOtp,
    FinishesSignUp,
    NeedsAccount,
    SignsIn,
    SignsInByOtp,
    SignUpDetails,
    Unsupported,
)
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


def emails(user_dir: Path) -> list[str]:
    """Every account's email, for a snapshot to scrub."""
    path = user_dir / EMAILS
    return list(json.loads(path.read_text()).values()) if path.exists() else []


def remember(user_dir: Path, adapter: str, scope: str | None, address: str) -> None:
    path = user_dir / EMAILS
    emails = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps(emails | {account_key(adapter, scope): address}, indent=2))


def sign_in(
    page: Page,
    adapter: SignsIn | SignsInByOtp,
    scope: str | None,
    address: str,
    password: str | None,
    session: Path,
    details: SignUpDetails | None = None,
    create: bool = False,
) -> None:
    """Signs in from the system's login page and stores the session: with `create`, creates
    the account from the user's sign-up `details`; without, `details` finish a sign-up the
    account was left in, where the adapter knows that step. An account without a password
    (`password` None) signs in by OTP, and creates the same way.
    Raises what the adapter raises, without the password in it."""
    page.goto(adapter.login_url(scope))
    try:
        if isinstance(adapter, SignsInByOtp):
            if create:
                assert isinstance(adapter, CreatesAccountByOtp) and details is not None
                adapter.sign_up_by_otp(page, address, details)
            else:
                adapter.sign_in_by_otp(page, address)
        elif password is None:
            raise AssertionError(f"{adapter.name} signs in with a password")
        elif create:
            assert isinstance(adapter, CreatesAccount) and details is not None
            adapter.sign_up(page, address, password, details)
        elif details is not None and isinstance(adapter, FinishesSignUp):
            adapter.finish_sign_up(page, address, password, details)
        else:
            adapter.sign_in(page, address, password)
    except Exception as e:
        if password and password in str(e):
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
    """`signed_in` or `signed_out` from the system itself, `unknown` when its answer cannot
    tell, `missing` with no session stored."""
    if not session.exists():
        return "missing"
    with browser.session_request(session) as request:
        signed_in = adapter.signed_in(request, scope)
    return {True: "signed_in", False: "signed_out", None: "unknown"}[signed_in]


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
