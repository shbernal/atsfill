"""Where atsfill gets an account's password (ADR 0015). A password is never printed, logged,
or put in a reason or an exception."""

import contextlib
import json
import shlex
import subprocess
from pathlib import Path

import keyring
import keyring.errors
from keyring.backends import fail

from atsfill.core import paths

SERVICE = "atsfill"
COMMAND_TIMEOUT_S = 60  # a password manager may wait for its unlock
FILE = "passwords.json"  # in the user's directory, only without a secret store


class PasswordError(Exception):
    """A password source failed; the message never holds the password."""


def account_key(adapter: str, scope: str | None) -> str:
    return f"{adapter}/{scope}" if scope else adapter


def _entry(user_dir: Path, key: str) -> str:
    """The secret store entry `<user>/<adapter>[/<scope>]`; the user's directory bears their
    name."""
    return f"{user_dir.name}/{key}"


def secret_store() -> str:
    """The secret store backend's name, `file` when there is none."""
    return "file" if _no_backend() else keyring.get_keyring().name


def _no_backend() -> bool:
    return isinstance(keyring.get_keyring(), fail.Keyring)


def from_command(template: str, email: str, scope: str | None) -> str:
    """Runs a `password-commands` entry: split into arguments without a shell, `{email}` and
    `{scope}` filled in each, the password the first line of its output."""
    args = [
        a.replace("{email}", email).replace("{scope}", scope or "") for a in shlex.split(template)
    ]
    try:
        out = subprocess.run(
            args, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise PasswordError(
            f"the password command `{args[0]}` failed: {type(e).__name__}"
        ) from None
    if not (password := next(iter(out.splitlines()), "")):
        raise PasswordError(f"the password command `{args[0]}` printed no password")
    return password


def stored(user_dir: Path, adapter: str, scope: str | None) -> str | None:
    """A password atsfill generated, from the secret store."""
    key = account_key(adapter, scope)
    if _no_backend():
        return _file(user_dir).get(key)
    try:
        return keyring.get_password(SERVICE, _entry(user_dir, key))
    except keyring.errors.KeyringError as e:
        raise PasswordError(f"the secret store failed: {type(e).__name__}") from None


def store(user_dir: Path, adapter: str, scope: str | None, password: str) -> None:
    key = account_key(adapter, scope)
    if _no_backend():
        _write(user_dir, _file(user_dir) | {key: password})
        return
    try:
        keyring.set_password(SERVICE, _entry(user_dir, key), password)
    except keyring.errors.KeyringError as e:
        raise PasswordError(f"the secret store failed: {type(e).__name__}") from None


def delete(user_dir: Path, adapter: str, scope: str | None) -> None:
    key = account_key(adapter, scope)
    if _no_backend():
        _write(user_dir, {k: v for k, v in _file(user_dir).items() if k != key})
        return
    with contextlib.suppress(keyring.errors.PasswordDeleteError):
        keyring.delete_password(SERVICE, _entry(user_dir, key))


def password(
    user: str, user_dir: Path, adapter: str, scope: str | None, email: str, given: str | None
) -> str | None:
    """The first source found: the password given on stdin, the user's `password-commands`
    entry for the adapter, the secret store. None without one."""
    if given:
        return given
    if command := paths.config(user).get("password-commands", {}).get(adapter):
        return from_command(command, email, scope)
    return stored(user_dir, adapter, scope)


def _file(user_dir: Path) -> dict[str, str]:
    path = user_dir / FILE
    return json.loads(path.read_text()) if path.exists() else {}


def _write(user_dir: Path, passwords: dict[str, str]) -> None:
    path = user_dir / FILE
    path.touch(mode=0o600)
    path.write_text(json.dumps(passwords, indent=2))
