import functools
import json

import pytest

from atsfill import core
from atsfill.cli import parser, run
from atsfill.core import accounts, browser, commands
from atsfill.core.results import Status
from tests.fake import FakeAccount


@pytest.fixture
def account(server, monkeypatch) -> FakeAccount:
    adapter = FakeAccount(server)
    monkeypatch.setattr(commands, "adapters", lambda: [adapter])
    monkeypatch.setattr(accounts, "POLL_MS", 100)
    monkeypatch.setattr(
        browser, "login_window", functools.partial(browser.login_window, headless=True)
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent")
    return adapter


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def url(server) -> str:
    return f"{server}/form.html?account"


def storage_state(path, value: str):
    cookie = {"name": "session", "value": value, "domain": "localhost", "path": "/"} | {
        "expires": -1,
        "httpOnly": False,
        "secure": False,
        "sameSite": "Lax",
    }
    path.write_text(json.dumps({"cookies": [cookie], "origins": []}))
    return path


def test_login_stores_the_session(account, data):
    report = core.login("fakeaccount", data_dir=data)
    assert (report.session, report.reason) == ("signed_in", None)
    assert report.path == data / "sessions/fakeaccount.json"
    assert report.path.stat().st_mode & 0o777 == 0o600
    (state,) = core.status(data_dir=data).adapters
    assert state.session == "signed_in"


def test_login_gives_up_when_nobody_signs_in(account, data, monkeypatch):
    monkeypatch.setattr(accounts, "LOGIN_TIMEOUT_S", 0.2)
    report = core.login("fakeaccount", data_dir=data)
    assert (report.session, report.reason) == ("missing", "login_timeout")
    assert not report.path.exists()


def test_login_needs_an_adapter_with_accounts(account, data):
    with pytest.raises(ValueError):
        core.login("ashby", data_dir=data)


def test_status_and_logout(account, data, capsys):
    session = data / "sessions/fakeaccount.json"
    session.parent.mkdir(parents=True)
    storage_state(session, "expired")
    assert run(parser().parse_args(["--data-dir", str(data), "status"])) == 1
    assert json.loads(capsys.readouterr().out)["adapters"][0]["session"] == "signed_out"
    assert core.logout("fakeaccount", data_dir=data).session == "missing"
    assert not session.exists()
    assert run(parser().parse_args(["--data-dir", str(data), "status"])) == 0


def test_no_session_is_login_required(account, data, url, monkeypatch):
    monkeypatch.setattr(commands, "open_page", None)  # refused before any launch
    result = core.fill(url, {"name": "Ada"}, data_dir=data)
    assert (result.status, result.reason) == (
        Status.BLOCKED,
        "login_required: run `atsfill login fakeaccount`",
    )


def test_a_given_session_is_loaded(account, data, url, tmp_path):
    session = storage_state(tmp_path / "s.json", "ok")
    result = core.fill(url, {"name": "Ada"}, data_dir=data, sessions={"fakeaccount": session})
    assert result.status is Status.READY, result.reason


def test_an_expired_session_is_login_required(account, data, url, tmp_path):
    session = storage_state(tmp_path / "s.json", "expired")
    result = core.fill(url, {"name": "Ada"}, data_dir=data, sessions={"fakeaccount": session})
    assert (result.status, result.reason) == (
        Status.BLOCKED,
        "login_required: run `atsfill login fakeaccount`",
    )
