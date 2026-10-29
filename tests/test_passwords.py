import io
import json
import sys

import pytest
from keyring.backends import fail

from atsfill import core
from atsfill.cli import parser, run
from atsfill.core import commands, passwords
from atsfill.core.adapter import Blocked, Refused, SignUpDetails
from atsfill.core.results import Status
from tests.fake import FakeSignIn, FakeSignUp

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


@pytest.fixture
def creates(server, monkeypatch) -> FakeSignUp:
    adapter = FakeSignUp(server)
    monkeypatch.setattr(commands, "adapters", lambda: [adapter])
    monkeypatch.setattr("atsfill.cli.adapters", lambda: [adapter])
    return adapter


def test_create_stores_the_password_before_the_click(creates, config, data, capsys, secret_store):
    stored_first = []
    sign_up = creates.sign_up

    def watched(page, email, password, details):
        stored_first.append(passwords.stored(data / "users/test", "fakeaccount", None) == password)
        sign_up(page, email, password, details)

    creates.sign_up = watched
    code, report = login(data, capsys, "--user", "test", "--create", "--email", EMAIL)
    assert (code, report["status"], report["password"]) == (0, "signed_in", "stored")
    assert stored_first == [True]
    assert creates.created and creates.created not in json.dumps(report)
    assert len(creates.created) >= passwords.GENERATED_LENGTH
    assert secret_store.passwords == {("atsfill", "test/fakeaccount"): creates.created}
    assert run(parser().parse_args(["--data-dir", str(data), "status"])) == 0
    assert creates.created not in capsys.readouterr().out


@pytest.mark.parametrize(
    "stop, status, kept",
    [
        (Refused("email_in_use"), "refused", False),
        (Refused("email_domain_rejected"), "refused", False),
        (Refused("password_rule: at most 16 characters"), "refused", False),
        (Blocked("no_otp_command"), "blocked", True),
        (Blocked("captcha"), "blocked", True),
        (RuntimeError("after the click"), "unsupported", True),
    ],
)
def test_create_keeps_the_password_while_the_account_may_exist(
    creates, config, data, capsys, stop, status, kept
):
    creates.stops = stop
    code, report = login(data, capsys, "--create", "--email", EMAIL)
    assert (code, report["status"]) == (1, status)
    assert report["password"] == ("stored" if kept else "deleted")
    stored = passwords.stored(data / "users/default", "fakeaccount", None)
    assert (stored is not None) == kept


def test_create_signs_in_with_a_password_already_stored(creates, config, data, capsys):
    login(data, capsys, "--create", "--email", EMAIL)
    first = creates.created
    code, report = login(data, capsys, "--create", "--email", "other@example.com")
    assert (code, report["status"], report["password"]) == (0, "signed_in", "kept")
    assert creates.created == first
    assert passwords.stored(data / "users/default", "fakeaccount", None) == first
    assert core.accounts.email(data / "users/default", "fakeaccount", None) == EMAIL


def test_create_signs_up_again_after_a_sign_up_blocked_before_the_account_existed(
    creates, config, data, capsys
):
    creates.stops = Blocked("email_otp")
    code, report = login(data, capsys, "--create", "--email", EMAIL)
    assert (code, report["status"], report["password"]) == (1, "blocked", "stored")
    first = passwords.stored(data / "users/default", "fakeaccount", None)
    creates.stops = None
    code, report = login(data, capsys, "--create", "--email", EMAIL)
    assert (code, report["status"], report["password"]) == (0, "signed_in", "kept")
    assert creates.created == first
    assert passwords.stored(data / "users/default", "fakeaccount", None) == first


def test_a_sign_up_again_that_finds_the_email_in_use_keeps_the_password(
    creates, config, data, capsys
):
    creates.stops = Blocked("email_otp")
    login(data, capsys, "--create", "--email", EMAIL)
    first = passwords.stored(data / "users/default", "fakeaccount", None)

    def in_use(page, email, password, details):
        raise Refused("email_in_use")

    creates.stops, creates.sign_up = None, in_use
    code, report = login(data, capsys, "--create", "--email", EMAIL)
    assert (code, report["status"], report["password"]) == (1, "refused", "kept")
    assert report["reason"] == "email_in_use"
    assert creates.sign_ins == 1
    assert passwords.stored(data / "users/default", "fakeaccount", None) == first


def test_create_with_a_password_stored_finishes_the_sign_up_with_the_details(
    creates, config, data, capsys, tmp_path
):
    login(data, capsys, "--create", "--email", EMAIL)
    creates.asks, creates.details = {"name", "phone"}, None
    details = tmp_path / "details.json"
    details.write_text(json.dumps({"name": "Jane Doe"}))
    code, report = login(data, capsys, "--create", "--email", EMAIL, "--details", str(details))
    assert (code, report["status"], report["password"]) == (1, "refused", "kept")
    assert report["reason"] == "sign_up_details: no phone"
    assert creates.details is None and creates.sign_ins == 0
    details.write_text(json.dumps({"name": "Jane Doe", "phone": "+12025550143"}))
    code, report = login(data, capsys, "--create", "--email", EMAIL, "--details", str(details))
    assert (code, report["status"], report["password"]) == (0, "signed_in", "kept")
    assert creates.details == SignUpDetails(name="Jane Doe", phone="+12025550143")
    assert passwords.stored(data / "users/default", "fakeaccount", None) == creates.created


def test_a_verification_link_names_the_address_and_the_command(creates, config, data, capsys):
    creates.stops = Blocked("email_link")
    code, report = login(data, capsys, "--user", "test", "--create", "--email", EMAIL)
    assert report["reason"] == (
        f"email_link: click the link sent to {EMAIL}, then run "
        "`atsfill login fakeaccount --user test`"
    )
    assert report["password"] == "stored"


def test_a_sign_up_refuses_without_the_details_it_asks(creates, config, data, capsys, tmp_path):
    creates.asks = {"name", "phone", "zip"}
    details = tmp_path / "details.json"
    details.write_text(json.dumps({"name": "Jane Doe"}))
    code, report = login(data, capsys, "--create", "--email", EMAIL, "--details", str(details))
    assert (code, report["status"]) == (1, "refused")
    assert report["reason"] == "sign_up_details: no phone, zip"
    assert creates.created is None
    assert passwords.stored(data / "users/default", "fakeaccount", None) is None


def test_a_sign_up_gets_the_details(creates, config, data, capsys, tmp_path, secret_store):
    creates.asks = {"name", "country"}
    details = tmp_path / "details.json"
    details.write_text(json.dumps({"name": "Jane Doe", "address": {"country": "US"}}))
    code, _ = login(data, capsys, "--create", "--email", EMAIL, "--details", str(details))
    assert code == 0
    assert creates.details == SignUpDetails(name="Jane Doe", country="US")


@pytest.mark.parametrize(
    "content",
    ["[]", "{", '{"age": "30"}', '{"phone": "555"}', '{"address": {"country": "USA"}}'],
)
def test_a_bad_details_file_is_a_usage_error(data, capsys, tmp_path, content):
    details = tmp_path / "details.json"
    details.write_text(content)
    args = ["--data-dir", str(data), "login", "fakeaccount", "--create", "--details", str(details)]
    with pytest.raises(SystemExit) as exit:
        parser().parse_args(args)
    assert exit.value.code == 2
    assert "--details" in capsys.readouterr().err


def test_create_needs_an_adapter_with_sign_up(account, config, data, capsys):
    code, report = login(data, capsys, "--create", "--email", EMAIL)
    assert (code, report["status"]) == (1, "unsupported")
    assert report["reason"].startswith("no_sign_up")


def test_sign_up_is_a_declared_side_effect(creates):
    assert [e.step for e in creates.side_effects] == ["fill", "sign_up"]
