from pathlib import Path

import pytest

from atsfill.core import paths


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    for var in ("ATSFILL_DATA_DIR", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(paths, "LINUX", True)
    return tmp_path


def test_data_dir_defaults_to_local_state(home):
    path = paths.data_dir()
    assert path == home / ".local/state/atsfill"
    assert path.stat().st_mode & 0o777 == 0o700


def test_data_dir_follows_xdg_state_home(home, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    assert paths.data_dir() == home / "state/atsfill"


def test_data_dir_flag_over_env(home, monkeypatch):
    monkeypatch.setenv("ATSFILL_DATA_DIR", str(home / "env"))
    assert paths.data_dir() == home / "env"
    assert paths.data_dir(home / "flag") == home / "flag"


def test_data_dir_outside_linux(home, monkeypatch):
    monkeypatch.setattr(paths, "LINUX", False)
    assert paths.data_dir() == home / ".atsfill"


def test_cache_dir(home, monkeypatch):
    assert paths.cache_dir() == home / ".cache/atsfill"
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))
    assert paths.cache_dir() == home / "cache/atsfill"
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setattr(paths, "LINUX", False)
    assert paths.cache_dir() == home / ".atsfill/cache"


def test_config_file(home, monkeypatch):
    assert paths.config_file() == home / ".config/atsfill/config.toml"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "conf"))
    assert paths.config_file() == home / "conf/atsfill/config.toml"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setattr(paths, "LINUX", False)
    assert paths.config_file() == home / ".atsfill/config.toml"
