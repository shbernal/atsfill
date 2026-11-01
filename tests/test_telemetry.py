import json

import pytest

from atsfill import core
from atsfill.core import accounts, commands, matching, paths, snapshot, telemetry
from atsfill.core.results import Field, FieldType, Result, Status
from tests.fake import Fake

URL = "https://Jobs.Example.com/acme/1234?utm_source=x#apply"


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    path = tmp_path / "conf/atsfill/config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("")
    return path


@pytest.fixture
def sent(config, monkeypatch) -> list[tuple[str, dict]]:
    """Telemetry as outside the test suite, the events kept instead of sent."""
    events: list[tuple[str, dict]] = []
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(telemetry, "_in_tests_or_ci", lambda: False)
    monkeypatch.setattr(telemetry, "_capture", events.extend)
    return events


@pytest.mark.parametrize(
    "status, reason, verdict",
    [
        (Status.READY, None, "pass"),
        (Status.SUBMITTED, None, "pass"),
        (Status.REFUSED, "invalid_answers: level is not an option", "excluded"),
        (Status.REFUSED, "unknown_field: visa", "fail"),
        (Status.REFUSED, "answer_not_taken: city", "excluded"),
        (Status.REFUSED, "already_sent: last attempt ended submitted", "excluded"),
        (Status.REFUSED, "submit_not_enabled: add x", "excluded"),
        (Status.UNSUPPORTED, "out_of_scope: v is a video recorded live", "excluded"),
        (Status.UNSUPPORTED, "posting_gone", "excluded"),
        (Status.UNSUPPORTED, "external_step: another site", "excluded"),
        (Status.UNSUPPORTED, "not_qualified: the form stays locked", "excluded"),
        (Status.UNSUPPORTED, "no_adapter: no adapter for this hiring system", "unsupported"),
        (Status.REFUSED, "mismatch: level", "fail"),
        (Status.BLOCKED, "login_wall", "fail"),
        (Status.BLOCKED, "profile_incomplete: no about, run `atsfill profile x`", "fail"),
        (Status.UNSUPPORTED, "unsupported_rendering: g: x", "fail"),
        (Status.UNSUPPORTED, "adapter_error: KeyError: x", "fail"),
        (Status.UNKNOWN, "no_evidence", "fail"),
    ],
)
def test_a_verdict_from_the_status_and_reason(status, reason, verdict):
    assert telemetry.verdict(Result(status, URL, "x", reason)) == verdict


def test_the_posting_hash_ignores_fragment_tracking_and_host_case():
    same = "https://jobs.example.com/acme/1234?ref=x"
    assert telemetry.posting_hash(URL.replace("utm_source=x", "ref=x")) == telemetry.posting_hash(
        same
    )
    assert telemetry.posting_hash(same) != telemetry.posting_hash("https://jobs.example.com/acme/5")


def test_every_url_of_a_posting_hashes_the_same(monkeypatch):
    monkeypatch.setattr(telemetry, "origin", lambda: ("user", None))
    urls = [
        "https://job-boards.greenhouse.io/acme/jobs/123?utm_source=x",
        "https://boards.greenhouse.io/embed/job_app?for=acme&token=123",
    ]
    hashes = {
        telemetry.event("read", Result(Status.READY, u, "greenhouse"))["posting"] for u in urls
    }
    assert len(hashes) == 1


def test_the_event_holds_codes_and_a_hash_never_the_url_or_answers(monkeypatch):
    monkeypatch.setattr(telemetry, "origin", lambda: ("dev", "abc1234"))
    result = Result(Status.REFUSED, URL, "fake", "mismatch: level (Fluent read back as Basic)")
    event = telemetry.event("fill", result)
    assert event == {
        "system": "fake",
        "command": "fill",
        "verdict": "fail",
        "status": "refused",
        "reason": "mismatch",
        "version": event["version"],
        "posting": telemetry.posting_hash(URL),
        "source": "dev",
        "commit": "abc1234",
    }


def test_a_refusal_names_its_fields_and_rules_never_the_answer(monkeypatch):
    monkeypatch.setattr(telemetry, "origin", lambda: ("user", None))
    field = Field("level", "Level", FieldType.CHOICE, True, ["Basic"])
    problems = matching.problems([field], {"level": "my secret"})
    result = Result(Status.REFUSED, URL, "fake", "invalid_answers: " + "; ".join(problems))
    result.refused = [f"{p.field_id}: {p.rule}" for p in problems]
    event = telemetry.event("fill", result)
    assert event["refused"] == ["level: not_an_option"]
    assert "my secret" not in json.dumps(event)


def test_no_adapter_names_the_source_its_host_is_on(monkeypatch):
    monkeypatch.setattr(telemetry, "origin", lambda: ("user", None))
    result = Result(Status.UNSUPPORTED, "https://acme.icims.com/jobs/1/job", reason="no_adapter")
    assert telemetry.event("read", result)["system"] == "icims"
    result.url = "https://careers.acme.example/jobs/1"
    event = telemetry.event("read", result)
    assert (event["system"], "commit" in event) == ("unknown", False)


def test_a_command_sends_its_event_and_a_notice_once(sent, tmp_path, monkeypatch):
    monkeypatch.setattr(commands, "_read", lambda *a: Result(Status.READY, URL, "fake"))
    first = core.read(URL, data_dir=tmp_path / "data")
    second = core.read(URL, data_dir=tmp_path / "data")
    assert [e["verdict"] for _, e in sent] == ["pass", "pass"]
    assert "DO_NOT_TRACK=1" in (first.notice or "")
    assert second.notice is None


@pytest.mark.parametrize("off", ["config", "do_not_track"])
def test_off_sends_nothing(sent, config, tmp_path, monkeypatch, off):
    if off == "config":
        config.write_text('telemetry = "off"\n')
    else:
        monkeypatch.setenv("DO_NOT_TRACK", "1")
    result = telemetry.sent("read", Result(Status.READY, URL, "fake"), tmp_path)
    assert (sent, result.notice) == ([], None)


def test_the_test_suite_never_sends(config, tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, "_capture", lambda _: pytest.fail("sent from a test"))
    assert telemetry.level() == "off"
    telemetry.sent("read", Result(Status.READY, URL, "fake"), tmp_path)


def test_a_failed_send_leaves_the_result_as_it_was(sent, tmp_path, monkeypatch):
    def down(_):
        raise OSError("no route to host")

    monkeypatch.setattr(telemetry, "_capture", down)
    result = Result(Status.REFUSED, URL, "fake", "mismatch: level")
    assert telemetry.sent("fill", result, tmp_path) is result
    assert (result.status, result.reason) == (Status.REFUSED, "mismatch: level")


def test_an_unknown_level_is_a_config_error(config):
    config.write_text('telemetry = "full"\n')
    with pytest.raises(paths.ConfigError, match="telemetry"):
        paths.telemetry()


@pytest.fixture
def answers(tmp_path) -> dict:
    cv = tmp_path / "Jane-Doe-resume.pdf"
    cv.write_bytes(b"%PDF-1.4")
    return {"name": "Jane Doe", "level": "Fluent", "langs": ["French"], "cv": str(cv)}


@pytest.fixture
def fake(monkeypatch) -> Fake:
    adapter = Fake()
    monkeypatch.setattr(commands, "adapters", lambda: [adapter])
    return adapter


def test_a_failed_fill_keeps_its_page_with_no_answer_left(
    sent, fake, server, answers, tmp_path, monkeypatch
):
    data = tmp_path / "data"
    accounts.remember(paths.user_dir(data, "default"), "fake", None, "jane@example.com")
    fake.shows = ["ranking"]
    result = core.fill(f"{server}/form.html", answers, data_dir=data)
    assert result.reason.startswith("unhandled_rendering")
    assert "atsfill report" in (result.report or "")
    kept = json.loads((data / telemetry.FAILED_RUN).read_text())
    page = kept["event"]["snapshot"]
    assert "<form" in page and "Hello" in page and "<script" not in page
    for secret in ["Jane", "Doe", "Fluent", "French", "resume", "jane@example.com"]:
        assert secret.lower() not in page.lower(), secret
    assert [name for name, _ in sent] == ["run"]
    assert "url" not in sent[0][1]


def test_scrub_replaces_whole_words_escaped_forms_and_emails():
    text = '<p title="Smith &amp; Co">Ann Smith & Co, Annex; write to a.b@c.example</p>'
    scrubbed = snapshot.scrub(text, {"Smith & Co", "Ann"})
    assert scrubbed == (
        '<p title="[scrubbed]">[scrubbed] [scrubbed], Annex; write to [scrubbed]</p>'
    )


def test_a_page_over_the_limit_keeps_its_step(monkeypatch):
    monkeypatch.setattr(snapshot, "MAX_BYTES", 100)
    step = "<form><input></form>"
    assert snapshot.kept(("<html>" + "x" * 200 + step + "</html>", step), set()) == step
    cut = snapshot.kept(("<html>" + "x" * 200, "<form>" + "x" * 200), set())
    assert len(cut.encode()) <= 100 and cut.endswith(snapshot.TRUNCATED)


def test_detailed_sends_the_report_with_the_run(sent, config, tmp_path):
    config.write_text('telemetry = "detailed"\n')
    result = telemetry.sent("fill", Result(Status.REFUSED, URL, "fake", "mismatch: x"), tmp_path)
    assert [name for name, _ in sent] == ["run", "report"]
    assert sent[0][1]["url"] == sent[1][1]["url"] == URL
    assert result.report is None
    assert core.report(data_dir=tmp_path).reason.startswith("already_reported")


def test_report_sends_the_last_failed_run_once(sent, tmp_path):
    telemetry.sent("fill", Result(Status.REFUSED, URL, "fake", "mismatch: x"), tmp_path)
    telemetry.sent("fill", Result(Status.READY, URL, "fake"), tmp_path)
    first, second = core.report(data_dir=tmp_path), core.report(data_dir=tmp_path)
    assert (first.status, first.url) == ("sent", URL)
    assert [name for name, _ in sent] == ["run", "run", "report"]
    assert sent[-1][1]["detail"] == "x"
    assert (second.status, second.reason.split(":")[0]) == ("refused", "already_reported")


def test_report_refuses_when_off_or_nothing_failed(sent, config, tmp_path, monkeypatch):
    assert core.report(data_dir=tmp_path).reason.startswith("no_failed_run")
    telemetry.sent("fill", Result(Status.REFUSED, URL, "fake", "mismatch: x"), tmp_path)
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    assert core.report(data_dir=tmp_path).reason.startswith("telemetry_off")
    assert [name for name, _ in sent] == ["run"]


def test_a_profile_event_has_no_posting(monkeypatch):
    monkeypatch.setattr(telemetry, "origin", lambda: ("user", None))
    event = telemetry.event("profile", Result(Status.READY, "https://example.com/profile", "x"))
    assert (event["command"], event["verdict"], "posting" in event) == ("profile", "pass", False)
