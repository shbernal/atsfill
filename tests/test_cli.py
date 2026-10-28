import dataclasses
import json
from pathlib import Path

import pytest

from atsfill.adapters import adapters
from atsfill.cli import emit, parser, run
from atsfill.core import browser, commands
from atsfill.core.results import (
    Check,
    Field,
    FieldType,
    Is,
    OptionSet,
    Result,
    SideEffect,
    Status,
)
from tests.fake import FakeProfile
from tests.test_accounts import storage_state


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


def test_a_user_name_is_lowercase_letters_digits_and_dashes():
    assert parser().parse_args(["read", "https://example.com", "--user", "jane-2"]).user == "jane-2"
    with pytest.raises(SystemExit) as exit:
        parser().parse_args(["read", "https://example.com", "--user", "../jane"])
    assert exit.value.code == 2


def test_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    assert run(parser().parse_args(["--data-dir", str(tmp_path / "data"), "status"])) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["data_dir"] == str(tmp_path / "data")
    assert report["config"] == str(tmp_path / "conf/atsfill/config.toml")
    assert report["version"] and report["browser"] and report["run_history"]
    assert [a["name"] for a in report["adapters"]] == [a.name for a in adapters()]


def test_status_fails_on_another_browser_build(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    monkeypatch.setattr(browser, "version", lambda: "135.0-beta.25")
    assert run(parser().parse_args(["--data-dir", str(tmp_path / "data"), "status"])) == 1
    assert json.loads(capsys.readouterr().out)["browser_pinned"] == browser.BUILD


FIELDS = [
    Field("name", "Name", FieldType.TEXT, True),
    Field("level", "Level", FieldType.CHOICE, False, options=["Basic", "Fluent"]),
]
EFFECTS = [SideEffect("fill", "hiring_system", "answers are saved as typed")]
URL = "https://example.com/form"


def emitted(capsys, result: Result, command: str, verbose: bool = False) -> dict:
    emit(result, verbose, command)
    out = capsys.readouterr().out
    assert verbose or out.count("\n") == 1
    return json.loads(out)


def test_a_read_keeps_the_fields_and_side_effects_without_empty_keys(capsys):
    result = Result(Status.READY, URL, "fake", fields=FIELDS, side_effects=EFFECTS)
    assert emitted(capsys, result, "read") == {
        "status": "ready",
        "system": "fake",
        "fields": [
            {"id": "name", "label": "Name", "type": "text", "required": True},
            {"id": "level", "label": "Level", "type": "choice", "options": ["Basic", "Fluent"]},
        ],
        "side_effects": [dataclasses.asdict(EFFECTS[0])],
    }


def test_a_read_keeps_empty_options_nothing_to_choose(capsys):
    state = Field(
        "state",
        "State",
        FieldType.CHOICE,
        False,
        [],
        option_sets=[OptionSet(Is("country", ["Canada"]), ["Ontario"])],
    )
    result = Result(Status.READY, URL, "fake", fields=[state])
    assert emitted(capsys, result, "read")["fields"] == [
        {
            "id": "state",
            "label": "State",
            "type": "choice",
            "options": [],
            "option_sets": [
                {"when": {"field_id": "country", "values": ["Canada"]}, "options": ["Ontario"]}
            ],
        }
    ]


def test_a_ready_fill_is_its_status_and_system(capsys):
    checks = [Check("name", "Ada", "Ada", True)]
    result = Result(Status.READY, URL, "fake", fields=FIELDS, checks=checks, side_effects=EFFECTS)
    assert emitted(capsys, result, "fill") == {
        "status": "ready",
        "system": "fake",
    }


def test_a_mismatch_keeps_only_the_failing_checks_read_back(capsys):
    checks = [Check("name", "Ada", "Ada", True), Check("level", "Fluent", "Basic", False)]
    result = Result(Status.REFUSED, URL, "fake", "mismatch: level", FIELDS, checks, EFFECTS)
    assert emitted(capsys, result, "send") == {
        "status": "refused",
        "system": "fake",
        "reason": "mismatch: level",
        "checks": [{"field_id": "level", "read_back": "Basic"}],
    }


def test_verbose_prints_the_whole_result(capsys):
    checks = [Check("name", "Ada", "Ada", True)]
    result = Result(Status.SUBMITTED, URL, "fake", fields=FIELDS, checks=checks, evidence="e")
    assert emitted(capsys, result, "send", verbose=True) == json.loads(
        json.dumps(dataclasses.asdict(result))
    )


def test_a_profile_read_prints_its_fields_and_a_save_its_saved_steps(
    server, tmp_path, monkeypatch, capsys
):
    adapter = FakeProfile(server)
    monkeypatch.setattr(commands, "adapters", lambda: [adapter])
    monkeypatch.setattr("atsfill.cli.adapters", lambda: [adapter])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    data = tmp_path / "data"
    session = data / "users/default/sessions/fakeprofile.json"
    session.parent.mkdir(parents=True)
    storage_state(session, "ok")
    base = ["--data-dir", str(data), "profile", "fakeprofile"]
    assert run(parser().parse_args(base)) == 0
    out = json.loads(capsys.readouterr().out)
    assert ([f["id"] for f in out["fields"]], out["unloaded"]) == (["name"], ["level"])
    answers = tmp_path / "profile.json"
    answers.write_text(json.dumps({"name": "Ada", "level": "Fluent"}))
    assert run(parser().parse_args([*base, "--answers", str(answers)])) == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "ready",
        "system": "fakeprofile",
        "saved": ["about", "level"],
    }
