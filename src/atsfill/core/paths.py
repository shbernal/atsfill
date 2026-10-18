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


def submit_enabled(adapter: str) -> bool:
    """Whether the user's config file lists the adapter under `submit` (ADR 0006)."""
    path = config_file()
    if not path.exists():
        return False
    return adapter in tomllib.loads(path.read_text()).get("submit", [])
