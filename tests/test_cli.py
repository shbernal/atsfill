import json
from pathlib import Path

import pytest

from atsfill.adapters import adapters
from atsfill.cli import parser, run
from atsfill.core import browser


def test_send_requires_answers():
    with pytest.raises(SystemExit) as exit:
        parser().parse_args(["send", "https://example.com"])
    assert exit.value.code == 2


def test_sessions_are_repeatable():
    args = parser().parse_args(
        ["read", "https://example.com", "--session", "a=x.json", "--session", "b=y.json"]
    )
    assert dict(args.session) == {"a": Path("x.json"), "b": Path("y.json")}


def test_malformed_session_is_a_usage_error():
    with pytest.raises(SystemExit) as exit:
        parser().parse_args(["read", "https://example.com", "--session", "x.json"])
    assert exit.value.code == 2


def test_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    assert run(parser().parse_args(["--data-dir", str(tmp_path / "data"), "status"])) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["data_dir"] == str(tmp_path / "data")
    assert report["config"] == str(tmp_path / "conf/atsfill/config.toml")
    assert report["version"] and report["browser"]
    assert [a["name"] for a in report["adapters"]] == [a.name for a in adapters()]


def test_status_fails_on_another_browser_build(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    monkeypatch.setattr(browser, "version", lambda: "135.0-beta.25")
    assert run(parser().parse_args(["--data-dir", str(tmp_path / "data"), "status"])) == 1
    assert json.loads(capsys.readouterr().out)["browser_pinned"] == browser.BUILD
