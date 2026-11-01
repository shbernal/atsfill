"""Accounts without a password, signed in and created through the user's OTP command, on a
fake system whose sign-in turns an unknown email into a sign-up."""

import io
import json
import sys

import pytest

from atsfill import core
from atsfill.cli import parser, run
from atsfill.core import commands, passwords
from atsfill.core.adapter import SignUpDetails
from atsfill.core.results import Status
from tests.fake import FakeOtpOnly

EMAIL = "jane@example.com"
JANE = SignUpDetails(name="Jane Doe")


@pytest.fixture
def account(server, monkeypatch) -> FakeOtpOnly:
    adapter = FakeOtpOnly(server)
    monkeypatch.setattr(commands, "adapters", lambda: [adapter])
    monkeypatch.setattr("atsfill.cli.adapters", lambda: [adapter])
    return adapter


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    path = tmp_path / "conf/atsfill/config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(f'[otp-commands]\nall = "{sys.executable} -c \\"print(123456)\\""\n')
    return path


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data"


def test_login_through_the_otp_command(account, config, data):
    account.accounts[EMAIL] = JANE
    report = core.login("fakeaccount", email=EMAIL, data_dir=data)
    assert (report.session, report.reason) == ("signed_in", None)
    assert passwords.stored(data / "users/default", "fakeaccount", None) is None


def test_an_email_without_an_account_stops_before_the_sign_up(account, config, data):
    report = core.login("fakeaccount", email=EMAIL, data_dir=data)
    assert (report.status, report.session) == (Status.REFUSED, "missing")
    assert report.reason == (
        f"no_account: {EMAIL} has no fakeaccount account;"
        f" run `atsfill login fakeaccount --email {EMAIL} --create`"
    )
    assert not account.accounts


def test_create_gives_the_details_and_no_password(account, config, data):
    refused = core.login("fakeaccount", email=EMAIL, create=True, data_dir=data)
    assert refused.reason == "sign_up_details: no name"
    report = core.login("fakeaccount", email=EMAIL, create=True, details=JANE, data_dir=data)
    assert (report.session, report.password) == ("signed_in", "none")
    assert account.accounts == {EMAIL: JANE}
    assert passwords.stored(data / "users/default", "fakeaccount", None) is None


def test_a_fill_signs_in_again_through_the_otp_command(account, config, data, server):
    url = f"{server}/form.html?account"
    core.login("fakeaccount", email=EMAIL, create=True, details=JANE, data_dir=data)
    session = data / "users/default/sessions/fakeaccount.json"
    session.write_text(json.dumps({"cookies": [], "origins": []}))
    result = core.fill(url, {"name": "Ada"}, data_dir=data)
    assert result.status is Status.READY, result.reason
    assert account.sign_ins == 2 and "session" in session.read_text()
    account.accounts.clear()
    session.unlink()
    result = core.fill(url, {"name": "Ada"}, data_dir=data)
    assert (result.status, (result.reason or "").split(":")[0]) == (Status.REFUSED, "no_account")


def test_without_an_otp_command_a_fill_is_login_required(account, config, data, server):
    core.login("fakeaccount", email=EMAIL, create=True, details=JANE, data_dir=data)
    (data / "users/default/sessions/fakeaccount.json").unlink()
    config.write_text("")
    result = core.fill(f"{server}/form.html?account", {"name": "Ada"}, data_dir=data)
    assert result.reason == "login_required: run `atsfill login fakeaccount`"


def test_a_password_is_a_usage_error(account, config, data, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("hunter2\n"))
    args = ["--data-dir", str(data), "login", "fakeaccount", "--email", EMAIL, "--password", "-"]
    assert run(parser().parse_args(args)) == 2
    assert "signs in with an emailed OTP" in capsys.readouterr().err


def test_a_session_the_system_ended_signs_in_again(account, config, data, server):
    url = f"{server}/form.html?account"
    core.login("fakeaccount", email=EMAIL, create=True, details=JANE, data_dir=data)
    account.ends_sessions = 1
    result = core.fill(url, {"name": "Ada"}, data_dir=data)
    assert result.status is Status.READY, result.reason
    assert account.sign_ins == 2
    account.ends_sessions = 2
    result = core.fill(url, {"name": "Ada"}, data_dir=data)
    assert result.reason == "login_required: the page asks to sign in"


def test_a_session_is_saved_again_after_a_command(account, config, data, server):
    core.login("fakeaccount", email=EMAIL, create=True, details=JANE, data_dir=data)
    session = data / "users/default/sessions/fakeaccount.json"
    assert "rotated" not in session.read_text()
    core.read(f"{server}/form.html?account", data_dir=data)
    assert "rotated" in session.read_text()
