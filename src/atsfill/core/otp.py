"""OTPs a hiring system emails before it goes on (ADR 0015): the user's `otp-commands` entry
gives each one, and atsfill types it where the adapter found the OTP step's input. The core
sets the source for the command; an adapter only calls `enter`."""

import shlex
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

from atsfill.core import paths
from atsfill.core.adapter import Blocked
from atsfill.core.browser import Locator

OTP_TIMEOUT_S = 90  # from the step showing to the OTP typed in: a mail takes seconds
POLL_S = 3  # between runs of a command that has no OTP yet
EVERY_ADAPTER = "all"  # the `otp-commands` key for the adapters without their own


@dataclass
class Source:
    """The user's `otp-commands` entry for the adapter, None without one, and when the last
    OTP was asked for: an OTP from before is not this one."""

    adapter: str
    command: str | None
    scope: str | None
    since: datetime


_source: ContextVar[Source | None] = ContextVar("otp_source", default=None)


def command(user: str, adapter: str) -> str | None:
    """The user's `otp-commands` entry for the adapter, else for every adapter."""
    entries = paths.config(user).get("otp-commands", {})
    return entries.get(adapter) or entries.get(EVERY_ADAPTER)


@contextmanager
def source(user: str, adapter: str, scope: str | None) -> Generator[None]:
    """Makes the user's entry the source of every OTP the adapter meets within, the first not
    older than now."""
    token = _source.set(Source(adapter, command(user, adapter), scope, _now()))
    try:
        yield
    finally:
        _source.reset(token)


def enter(inputs: Locator, email: str, step: str) -> None:
    """Types the OTP emailed to `email` into the step's input, or one character into each of
    its inputs. Raises `Blocked`: `no_otp_command` without an entry, `otp_not_received` when the
    command prints none within `OTP_TIMEOUT_S`, `otp_rejected` when what it prints does not fit
    the inputs; `step` names where the OTP was asked, for the reason."""
    current = _source.get()
    asked = f"{step} sent an OTP to {email}"
    if current is None or current.command is None:
        adapter = current.adapter if current else "the adapter"
        raise Blocked(f"no_otp_command: {asked}; no `otp-commands` entry for {adapter}")
    otp = _wait(current, email, asked)
    count = inputs.count()
    if count > 1 and count != len(otp):
        raise Blocked(
            f"otp_rejected: {asked}; the OTP command printed {len(otp)} characters"
            f" for {count} inputs"
        )
    if count > 1:
        for box, character in zip(inputs.all(), otp, strict=True):
            box.press_sequentially(character)
    else:
        inputs.fill("")
        inputs.press_sequentially(otp)
    current.since = _now()


def _wait(current: Source, email: str, asked: str) -> str:
    """Runs the command until it prints an OTP, each run within the time left: one that waits
    for the mail itself runs once."""
    assert current.command is not None
    since = current.since.isoformat(timespec="seconds").replace("+00:00", "Z")
    filled = {"{email}": email, "{scope}": current.scope or "", "{since}": since}
    args = shlex.split(current.command)
    for placeholder, value in filled.items():
        args = [a.replace(placeholder, value) for a in args]
    deadline = time.monotonic() + OTP_TIMEOUT_S
    while (left := deadline - time.monotonic()) > 0:
        try:
            run = subprocess.run(args, capture_output=True, text=True, timeout=left)
        except subprocess.TimeoutExpired:
            break
        except OSError as e:
            raise Blocked(
                f"otp_not_received: {asked}; the OTP command `{args[0]}` failed: {type(e).__name__}"
            ) from None
        if run.returncode == 0 and (otp := next(iter(run.stdout.split()), "")):
            return otp
        time.sleep(min(POLL_S, max(0.0, deadline - time.monotonic())))
    raise Blocked(
        f"otp_not_received: {asked}; the OTP command `{args[0]}` printed none"
        f" within {OTP_TIMEOUT_S}s"
    )


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)
