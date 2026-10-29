import os
import platform
import re
import tomllib
from pathlib import Path

from filelock import FileLock

LINUX = platform.system() == "Linux"
DEFAULT_USER = "default"
TEST_USER = "test"
USER_NAME = re.compile(r"[a-z0-9-]+")
RENAMED = {"submit": "submit-allowed", "ack_side_effects": "mute-side-effect-warnings"}
MOVED = ("sessions", "attempts.jsonl")  # a data directory from before users, the default's
TELEMETRY = ("off", "basic", "detailed")  # the first is no telemetry (ADR 0016)


class ConfigError(Exception):
    """The config file cannot be used as written; the message says what to change."""


def data_dir(override: Path | None = None) -> Path:
    """The data directory (ADR 0007), created 0700."""
    if override is None and (env := os.environ.get("ATSFILL_DATA_DIR")):
        override = Path(env)
    if override is None:
        state = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state"
        override = Path(state) / "atsfill" if LINUX else Path.home() / ".atsfill"
    override.mkdir(mode=0o700, parents=True, exist_ok=True)
    return override


def user_dir(data: Path, user: str) -> Path:
    """`users/<user>/` in the data directory (ADR 0015), created 0700."""
    if not USER_NAME.fullmatch(user):
        raise ValueError(f"a user name is lowercase letters, digits and -, not {user!r}")
    _move_to_default(data)
    path = data / "users" / user
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def users(data: Path) -> list[str]:
    """The default user, every user the config names and every user with a directory."""
    stored = {p.name for p in (data / "users").iterdir()} if (data / "users").is_dir() else set()
    named = set(_config().get("users", {}))
    return [DEFAULT_USER, *sorted((stored | named) - {DEFAULT_USER})]


def _move_to_default(data: Path) -> None:
    """Moves the sessions and attempts of a data directory from before users to the default
    user, once, under the lock."""
    if not any((data / name).exists() for name in MOVED):
        return
    with FileLock(data / "lock"):
        default = data / "users" / DEFAULT_USER
        default.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name in MOVED:
            if (data / name).exists() and not (default / name).exists():
                (data / name).rename(default / name)


def cache_dir() -> Path:
    """Public data atsfill can fetch again, kept to spare a hiring system the requests:
    `$XDG_CACHE_HOME/atsfill` on Linux (`~/.cache/atsfill`), `~/.atsfill/cache` elsewhere."""
    if xdg := os.environ.get("XDG_CACHE_HOME"):
        return Path(xdg) / "atsfill"
    return Path.home() / (".cache/atsfill" if LINUX else ".atsfill/cache")


def config_file() -> Path:
    if xdg := os.environ.get("XDG_CONFIG_HOME"):
        return Path(xdg) / "atsfill/config.toml"
    return Path.home() / (".config/atsfill/config.toml" if LINUX else ".atsfill/config.toml")


def _config() -> dict:
    path = config_file()
    whole = tomllib.loads(path.read_text()) if path.exists() else {}
    for section in [whole, *whole.get("users", {}).values()]:
        for old, new in RENAMED.items():
            if old in section:
                raise ConfigError(f"`{old}` in {path} is now `{new}`")
    if (level := whole.get("telemetry", "basic")) not in TELEMETRY:
        raise ConfigError(f"telemetry = {level!r} in {path}: one of {', '.join(TELEMETRY)}")
    return whole


def config(user: str) -> dict:
    """The user's keys in the config file (ADR 0015): the top level's for the default user,
    `[users.<name>]` for another, nothing inherited. Empty when there are none."""
    whole = _config()
    if user == DEFAULT_USER:
        return {k: v for k, v in whole.items() if k != "users"}
    return whole.get("users", {}).get(user, {})


def telemetry() -> str:
    """The home's telemetry level, a top-level key: `basic` unless the config says otherwise."""
    return _config().get("telemetry", "basic")


def submit_enabled(adapter: str, user: str) -> bool:
    """Whether the user's `submit-allowed` lists the adapter: there is no other way to enable
    it."""
    return adapter in config(user).get("submit-allowed", [])


def muted(adapter: str, user: str) -> bool:
    """Whether the user muted the adapter's side effects with `mute-side-effect-warnings`
    (ADR 0008)."""
    names = config(user).get("mute-side-effect-warnings", [])
    return adapter in names or "all" in names
