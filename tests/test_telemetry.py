import pytest

from atsfill import core
from atsfill.core import commands, paths, telemetry
from atsfill.core.results import Result, Status

URL = "https://Jobs.Example.com/acme/1234?utm_source=x#apply"


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    path = tmp_path / "conf/atsfill/config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("")
    return path


@pytest.fixture
def sent(config, monkeypatch) -> list[dict]:
    """Telemetry as outside the test suite, the events kept instead of sent."""
    events: list[dict] = []
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(telemetry, "_in_tests_or_ci", lambda: False)
    monkeypatch.setattr(telemetry, "_capture", events.append)
    return events


@pytest.mark.parametrize(
    "status, reason, verdict",
    [
        (Status.READY, None, "pass"),
        (Status.SUBMITTED, None, "pass"),
        (Status.REFUSED, "invalid_answers: level is not an option", "excluded"),
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
    assert [e["verdict"] for e in sent] == ["pass", "pass"]
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


def test_a_profile_event_has_no_posting(monkeypatch):
    monkeypatch.setattr(telemetry, "origin", lambda: ("user", None))
    event = telemetry.event("profile", Result(Status.READY, "https://example.com/profile", "x"))
    assert (event["command"], event["verdict"], "posting" in event) == ("profile", "pass", False)
