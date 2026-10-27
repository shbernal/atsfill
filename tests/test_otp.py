"""An OTP from the user's `otp-commands` entry, typed in by the core, through a fake system's
sign-in on tests/pages/otp.html."""

import sys
import time
from datetime import UTC, datetime

import pytest

from atsfill.core import browser, commands, otp
from atsfill.core.results import Status
from tests.fake import FakeOtp

EMAIL = "jane@example.com"
OTP = "123456"


@pytest.fixture
def account(server, monkeypatch) -> FakeOtp:
    adapter = FakeOtp(server)
    monkeypatch.setattr(commands, "adapters", lambda: [adapter])
    monkeypatch.setattr(otp, "POLL_S", 0.1)
    return adapter


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    path = tmp_path / "conf/atsfill/config.toml"
    path.parent.mkdir(parents=True)

    def write(entry: str, user: str | None = None) -> None:
        table = f"[users.{user}.otp-commands]" if user else "[otp-commands]"
        path.write_text(f"{table}\n{entry}\n")

    return write


def command(tmp_path, code: str) -> str:
    """A command line running `code` as a script."""
    script = tmp_path / f"otp{len(list(tmp_path.glob('otp*.py')))}.py"
    script.write_text(code)
    return f"{sys.executable} {script}"


def login(tmp_path, user: str = "default"):
    return commands.login(
        "fakeaccount",
        user=user,
        email=EMAIL,
        password=FakeOtp.password,
        data_dir=tmp_path / "data",
    )


def test_the_otp_is_typed_in(account, config, tmp_path):
    config(f'fakeaccount = "{command(tmp_path, f"print({OTP!r})")}"')
    assert login(tmp_path).status == "signed_in"


def test_one_character_per_box(account, config, tmp_path):
    account.inputs = "#boxes input"
    config(f'all = "{command(tmp_path, f"print({OTP!r})")}"', user="jane")
    assert login(tmp_path, user="jane").status == "signed_in"


def test_a_late_otp_is_polled_for(account, config, tmp_path):
    runs = tmp_path / "runs"
    late = f"""import pathlib, sys
runs = pathlib.Path({str(runs)!r})
runs.write_text((runs.read_text() if runs.exists() else "") + "x")
if len(runs.read_text()) < 3:
    sys.exit(1)
print({OTP!r})
"""
    config(f'fakeaccount = "{command(tmp_path, late)}"')
    assert login(tmp_path).status == "signed_in"
    assert runs.read_text() == "xxx"


def test_no_otp_in_time_blocks(account, config, tmp_path, monkeypatch):
    monkeypatch.setattr(otp, "OTP_TIMEOUT_S", 1)
    config(f'fakeaccount = "{command(tmp_path, "import sys; sys.exit(1)")}"')
    report = login(tmp_path)
    assert report.status is Status.BLOCKED
    assert report.reason == (
        f"otp_not_received: the sign-in sent an OTP to {EMAIL}; the OTP command"
        f" `{sys.executable}` printed none within 1s"
    )


def test_a_command_that_waits_is_stopped_at_the_timeout(account, config, tmp_path, monkeypatch):
    monkeypatch.setattr(otp, "OTP_TIMEOUT_S", 1)
    config(f'fakeaccount = "{command(tmp_path, "import time; time.sleep(30)")}"')
    started = datetime.now(UTC)
    report = login(tmp_path)
    assert report.status is Status.BLOCKED and "printed none within 1s" in (report.reason or "")
    assert (datetime.now(UTC) - started).total_seconds() < 20


def test_without_an_entry_the_otp_blocks(account, config, tmp_path):
    config('other = "true"')
    report = login(tmp_path)
    assert report.status is Status.BLOCKED
    assert report.reason == (
        f"no_otp_command: the sign-in sent an OTP to {EMAIL};"
        " no `otp-commands` entry for fakeaccount"
    )


def test_a_missing_command_blocks_at_once(account, config, tmp_path):
    config('fakeaccount = "no-such-command-anywhere"')
    report = login(tmp_path)
    assert report.status is Status.BLOCKED
    assert report.reason and report.reason.endswith(
        "the OTP command `no-such-command-anywhere` failed: FileNotFoundError"
    )


def test_the_placeholders_are_filled_per_argument(account, config, tmp_path):
    seen = tmp_path / "seen"
    started = datetime.now(UTC).replace(microsecond=0)
    record = f"""import pathlib, sys
pathlib.Path({str(seen)!r}).write_text(chr(10).join(sys.argv[1:]))
print({OTP!r})
"""
    config(f'fakeaccount = "{command(tmp_path, record)} {{email}} since={{since}} \\"{{scope}}\\""')
    assert login(tmp_path).status == "signed_in"
    email, since, scope = seen.read_text().split("\n")
    assert email == EMAIL and scope == ""
    assert since.endswith("Z")
    assert datetime.fromisoformat(since.removeprefix("since=")) >= started


def test_an_otp_typed_moves_since_on(server, config, tmp_path):
    """A second OTP in one command is not the first again: `since` is when the last was typed."""
    config(f'fake = "{command(tmp_path, "import sys; print(sys.argv[1])")} {{since}}"')
    with browser.open_page(tmp_path / "data") as page, otp.source("default", "fake", None):
        page.goto(f"{server}/otp.html")
        current = otp._source.get()
        assert current is not None
        opened = current.since
        time.sleep(1)
        otp.enter(page.locator("#otp"), EMAIL, "the step")
        assert datetime.fromisoformat(page.locator("#otp").input_value()) == opened
        assert current.since > opened
