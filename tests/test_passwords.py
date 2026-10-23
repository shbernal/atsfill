import io
import json
import sys

import pytest
from keyring.backends import fail

from atsfill import core
from atsfill.cli import parser, run
from atsfill.core import commands, passwords
from atsfill.core.adapter import Blocked
from atsfill.core.results import Status
from tests.fake import FakeSignIn

PASSWORD = FakeSignIn.password
EMAIL = "jane@example.com"


@pytest.fixture
def account(server, monkeypatch) -> FakeSignIn:
    adapter = FakeSignIn(server)
    monkeypatch.setattr(commands, "adapters", lambda: [adapter])
    monkeypatch.setattr("atsfill.cli.adapters", lambda: [adapter])
    return adapter


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    path = tmp_path / "conf/atsfill/config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("")
    return path.write_text


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data"


def login(data, capsys, *args: str, stdin: str = "") -> tuple[int, dict]:
    sys.stdin = io.StringIO(stdin)
    try:
        code = run(parser().parse_args(["--data-dir", str(data), "login", "fakeaccount", *args]))
    finally:
        sys.stdin = sys.__stdin__
    out = capsys.readouterr().out
    assert PASSWORD not in out
    return code, json.loads(out)


def test_a_command_gets_one_argument_per_placeholder():
    count = f'{sys.executable} -c "import sys; print(len(sys.argv) - 1, sys.argv[1])"'
    assert passwords.from_command(f"{count} {{email}}", EMAIL, None) == f"1 {EMAIL}"
    assert passwords.from_command(f"{count} {{scope}}", EMAIL, "acme; rm -rf x") == (
        "1 acme; rm -rf x"
    )
    assert passwords.from_command(f"{count} id={{email}}/{{scope}}", EMAIL, "acme") == (
        f"1 id={EMAIL}/acme"
    )


def test_a_failing_command_keeps_its_output_out_of_the_error():
    with pytest.raises(passwords.PasswordError) as error:
        passwords.from_command(f'{sys.executable} -c "print(1); exit(3)"', EMAIL, None)
    assert "1" not in str(error.value).replace(sys.executable, "")


def test_the_secret_store_keeps_a_password_per_user_and_account(secret_store, data):
    jane, test = data / "users/jane", data / "users/test"
    passwords.store(jane, "fakeaccount", "acme", PASSWORD)
    assert secret_store.passwords == {("atsfill", "jane/fakeaccount/acme"): PASSWORD}
    assert passwords.stored(jane, "fakeaccount", "acme") == PASSWORD
    assert passwords.stored(test, "fakeaccount", "acme") is None
    passwords.delete(jane, "fakeaccount", "acme")
    assert passwords.stored(jane, "fakeaccount", "acme") is None


def test_without_a_secret_store_a_0600_file(data, monkeypatch):
    monkeypatch.setattr(passwords.keyring, "get_keyring", fail.Keyring)
    jane = data / "users/jane"
    jane.mkdir(parents=True)
    passwords.store(jane, "fakeaccount", None, PASSWORD)
    assert passwords.secret_store() == "file"
    assert (jane / "passwords.json").stat().st_mode & 0o777 == 0o600
    assert passwords.stored(jane, "fakeaccount", None) == PASSWORD
    passwords.delete(jane, "fakeaccount", None)
    assert passwords.stored(jane, "fakeaccount", None) is None


def test_login_with_the_password_on_stdin(account, config, data, capsys):
    code, report = login(data, capsys, "--email", EMAIL, "--password", "-", stdin=PASSWORD + "\n")
    assert (code, report["status"], report["session"]) == (0, "signed_in", "signed_in")
    assert (data / "users/default/sessions/fakeaccount.json").exists()
    assert json.loads((data / "users/default/accounts.json").read_text()) == {"fakeaccount": EMAIL}


def test_login_with_a_password_command(account, config, data, capsys):
    config(f'[password-commands]\nfakeaccount = "{sys.executable} -c \'print(\\"{PASSWORD}\\")\'"')
    code, report = login(data, capsys, "--email", EMAIL)
    assert (code, report["status"]) == (0, "signed_in")


def test_a_wrong_password_is_refused(account, config, data, capsys):
    code, report = login(data, capsys, "--email", EMAIL, "--password", "-", stdin="nope\n")
    assert (code, report["status"], report["reason"]) == (1, "refused", "wrong_password")
    assert not (data / "users/default/sessions/fakeaccount.json").exists()


def test_a_verification_link_blocks(account, config, data, capsys):
    account.stops = Blocked(f"email_link: click the link sent to {EMAIL}")
    code, report = login(data, capsys, "--email", EMAIL, "--password", "-", stdin=PASSWORD)
    assert (code, report["status"]) == (1, "blocked")
    assert report["reason"].startswith("email_link")


def test_a_password_in_an_adapter_error_is_never_shown(account, config, data, capsys):
    account.stops = RuntimeError(f"could not type {PASSWORD}")
    code, report = login(data, capsys, "--email", EMAIL, "--password", "-", stdin=PASSWORD)
    assert (code, report["status"]) == (1, "unsupported")
    assert report["reason"] == "adapter_error: RuntimeError: could not type <password>"


def test_a_fill_signs_in_again_with_the_secret_store(account, config, data, server, capsys):
    url = f"{server}/form.html?account"
    assert core.fill(url, {"name": "Ada"}, data_dir=data).reason == (
        "login_required: run `atsfill login fakeaccount`"
    )
    mine = data / "users/default"
    passwords.store(mine, "fakeaccount", None, PASSWORD)
    core.login("fakeaccount", email=EMAIL, password=PASSWORD, data_dir=data)
    (mine / "sessions/fakeaccount.json").unlink()
    result = core.fill(url, {"name": "Ada"}, data_dir=data)
    assert result.status is Status.READY, result.reason
    assert (mine / "sessions/fakeaccount.json").exists()
    assert account.sign_ins == 2
    assert run(parser().parse_args(["--data-dir", str(data), "status"])) == 0
    assert PASSWORD not in capsys.readouterr().out


def test_a_signed_out_session_signs_in_again(account, config, data, server, tmp_path):
    passwords.store(data / "users/default", "fakeaccount", None, PASSWORD)
    core.login("fakeaccount", email=EMAIL, password=PASSWORD, data_dir=data)
    session = data / "users/default/sessions/fakeaccount.json"
    session.write_text(json.dumps({"cookies": [], "origins": []}))
    result = core.fill(f"{server}/form.html?account", {"name": "Ada"}, data_dir=data)
    assert result.status is Status.READY, result.reason
    assert "session" in session.read_text()
