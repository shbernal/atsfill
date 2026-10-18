import os
import platform
import tomllib
from pathlib import Path

LINUX = platform.system() == "Linux"


def data_dir(override: Path | None = None) -> Path:
    """The data directory (ADR 0007), created 0700."""
    if override is None and (env := os.environ.get("ATSFILL_DATA_DIR")):
        override = Path(env)
    if override is None:
        state = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state"
        override = Path(state) / "atsfill" if LINUX else Path.home() / ".atsfill"
    override.mkdir(mode=0o700, parents=True, exist_ok=True)
    return override


def config_file() -> Path:
    if xdg := os.environ.get("XDG_CONFIG_HOME"):
        return Path(xdg) / "atsfill/config.toml"
    return Path.home() / (".config/atsfill/config.toml" if LINUX else ".atsfill/config.toml")


def config() -> dict:
    """The user's config file (ADR 0006), empty when there is none."""
    path = config_file()
    return tomllib.loads(path.read_text()) if path.exists() else {}


def submit_enabled(adapter: str) -> bool:
    """Whether the config lists the adapter under `submit`: there is no other way to enable it."""
    return adapter in config().get("submit", [])


def acknowledged(adapter: str) -> bool:
    """Whether the user muted the adapter's side effects with `ack_side_effects` (ADR 0008)."""
    acked = config().get("ack_side_effects", [])
    return adapter in acked or "all" in acked
