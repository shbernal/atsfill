from contextlib import contextmanager

import pytest

from atsfill import core
from atsfill.core import attempts, commands, registry
from atsfill.core.adapter import Blocked, NotTaken, Unsupported, known_gap, out_of_scope
from atsfill.core.browser import Timeout
from atsfill.core.results import Field, FieldType, Status
from tests.fake import FIELDS, Fake


@pytest.fixture
def fake(monkeypatch) -> Fake:
    adapter = Fake()
    monkeypatch.setattr(commands, "ADAPTERS", [adapter])
    return adapter


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Writes the user's config file."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    path = tmp_path / "conf/atsfill/config.toml"
    path.parent.mkdir(parents=True)

    def write(text: str) -> None:
        path.write_text(text)

    write("")
    return write


@pytest.fixture
def url(server) -> str:
    return f"{server}/form.html"


@pytest.fixture
def answers(tmp_path) -> dict:
    cv = tmp_path / "resume.pdf"
    cv.write_bytes(b"%PDF-1.4")
    return {"name": "Ada", "level": "Fluent", "langs": ["French", "English"], "cv": str(cv)}


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data"


def test_read_over_http_skips_the_browser(fake, config, data, monkeypatch):
    monkeypatch.setattr(commands, "open_page", None)  # any launch would fail
    result = core.read("https://example.com/form?api", data_dir=data)
    assert (result.status, result.system, result.fields) == (Status.READY, "fake", FIELDS)


def test_read_detects_from_the_page(fake, config, url, data):
    result = core.read(url, data_dir=data)
    assert (result.status, result.fields) == (Status.READY, FIELDS)
    assert result.side_effects == fake.side_effects


def test_read_detects_from_the_url(fake, config, server, data):
    result = core.read(f"{server}/button.html?fake", data_dir=data)  # a page no adapter claims
    assert (result.status, result.fields) == (Status.READY, FIELDS)


def test_a_page_that_never_loads_is_left_to_the_adapter_of_its_url(
    fake, config, server, data, monkeypatch
):
    opened = commands.open_page

    @contextmanager
    def stalled(*args, **kwargs):
        with opened(*args, **kwargs) as page:

            def goto(url, **_):
                raise Timeout("Page.goto: Timeout 30000ms exceeded.")

            monkeypatch.setattr(page, "goto", goto)
            yield page

    monkeypatch.setattr(commands, "open_page", stalled)
    result = core.read(f"{server}/button.html?fake", data_dir=data)
    assert (result.status, result.fields) == (Status.READY, FIELDS)


def test_no_adapter_is_unsupported(fake, config, server, data):
    result = core.read(f"{server}/button.html", data_dir=data)
    assert result.status is Status.UNSUPPORTED
    assert result.reason and result.reason.startswith("no_adapter")


def test_acknowledged_side_effects_are_left_out(fake, config, url, data):
    config('ack_side_effects = ["all"]')
    assert core.read(url, data_dir=data).side_effects == []


def test_fill_reads_every_answer_back(fake, config, url, answers, data):
    result = core.fill(url, answers, data_dir=data)
    assert result.status is Status.READY, result.reason
    assert [c.field_id for c in result.checks if c.match] == list(answers)
    assert not (data / "attempts.jsonl").exists()


def test_invalid_answers_refuse_before_filling(fake, config, url, answers, data):
    result = core.fill(url, answers | {"level": "Basic"}, data_dir=data)
    assert result.status is Status.REFUSED
    assert result.reason and result.reason.startswith("invalid_answers: level: 'Basic'")
    assert result.checks == []


def test_a_mismatch_refuses(fake, config, url, answers, data):
    fake.typo = True
    result = core.fill(url, answers, data_dir=data)
    assert (result.status, result.reason) == (Status.REFUSED, "mismatch: name")
    assert next(c for c in result.checks if c.field_id == "name").read_back == "Adax"


@pytest.mark.parametrize(
    "error, status, reason",
    [
        (Blocked("email_code"), Status.BLOCKED, "email_code"),
        (Unsupported("unsupported_rendering: x"), Status.UNSUPPORTED, "unsupported_rendering: x"),
        (
            NotTaken("no suggestion for 'x'"),
            Status.REFUSED,
            "answer_not_taken: no suggestion for 'x'",
        ),
    ],
)
def test_the_adapter_stops_a_fill(fake, config, url, answers, data, error, status, reason):
    fake.fill_raises = error
    result = core.fill(url, answers, data_dir=data)
    assert (result.status, result.reason) == (status, reason)


def test_an_unexpected_error_keeps_the_detected_adapter(fake, config, url, answers, data):
    fake.fill_raises = RuntimeError("boom")
    result = core.fill(url, answers, data_dir=data)
    assert (result.status, result.system, result.reason) == (
        Status.UNSUPPORTED,
        "fake",
        "adapter_error: RuntimeError: boom",
    )


def _unfillable(fake: Fake, reason: str, required: bool) -> Field:
    field = Field("extra", "Extra", FieldType.FILE, required, unfillable=reason)
    fake.fields = [*FIELDS, field]
    return field


UNFILLABLE = [
    out_of_scope("extra", "a video recorded live"),
    known_gap(Fake(), "ranking", "a ranked list"),
    "unhandled_rendering: extra is a slider",
]


@pytest.mark.parametrize("reason", UNFILLABLE)
def test_a_required_unfillable_field_stops_the_form(fake, config, url, answers, data, reason):
    field = _unfillable(fake, reason, required=True)
    for result in core.read(url, data_dir=data), core.fill(url, answers, data_dir=data):
        assert (result.status, result.reason) == (Status.UNSUPPORTED, reason)
        assert field in result.fields
        assert result.checks == []


def test_read_over_http_shows_a_required_unfillable_field(fake, config, data):
    _unfillable(fake, UNFILLABLE[0], required=True)
    result = core.read("https://example.com/form?api", data_dir=data)
    assert (result.status, result.reason) == (Status.UNSUPPORTED, UNFILLABLE[0])


@pytest.mark.parametrize("reason", UNFILLABLE)
def test_an_optional_unfillable_field_is_left_empty(fake, config, url, answers, data, reason):
    field = _unfillable(fake, reason, required=False)
    result = core.fill(url, answers, data_dir=data)
    assert result.status is Status.READY, result.reason
    assert field in result.fields
    assert "extra" not in [c.field_id for c in result.checks]


def test_an_answer_to_an_unfillable_field_refuses(fake, config, url, answers, data):
    _unfillable(fake, UNFILLABLE[0], required=False)
    result = core.fill(url, answers | {"extra": answers["cv"]}, data_dir=data)
    assert result.status is Status.REFUSED
    assert result.reason == f"invalid_answers: extra: cannot be filled ({UNFILLABLE[0]})"


def test_a_known_gap_names_its_code_and_id():
    assert known_gap(Fake(), "ranking", "a ranked list") == (
        "unsupported_rendering: ranking: a ranked list"
    )


def test_send_needs_the_adapter_in_the_config(fake, config, url, answers, data):
    result = core.send(url, answers, data_dir=data)
    assert result.status is Status.REFUSED
    assert result.reason and result.reason.startswith("submit_not_enabled")


def test_send_once_then_resend(fake, config, url, answers, data):
    config('submit = ["fake"]')
    first = core.send(url, answers, data_dir=data)
    assert (first.status, first.evidence) == (Status.SUBMITTED, f"{url}?sent=1")
    again = core.send(url, answers, data_dir=data)
    assert again.status is Status.REFUSED
    assert again.reason == "already_sent: last attempt ended submitted; pass --resend"
    assert core.send(url, answers, resend=True, data_dir=data).status is Status.SUBMITTED


def test_a_refused_send_never_clicks(fake, config, url, answers, data):
    config('submit = ["fake"]')
    fake.typo = True
    assert core.send(url, answers, data_dir=data).status is Status.REFUSED
    assert attempts.last(data, url) is None


def test_no_evidence_is_unknown(fake, config, url, answers, data):
    config('submit = ["fake"]')
    fake.evidence = False
    result = core.send(url, answers, data_dir=data)
    assert (result.status, result.reason) == (Status.UNKNOWN, "no_evidence")


def test_a_timeout_past_the_click_is_unknown_and_guards(fake, config, url, answers, data):
    config('submit = ["fake"]')
    fake.submit_raises = Timeout("no response")
    result = core.send(url, answers, data_dir=data)
    assert result.status is Status.UNKNOWN
    assert result.reason and result.reason.startswith("error_after_click: TimeoutError")
    assert (core.send(url, answers, data_dir=data).reason or "").startswith("already_sent")


def test_a_blocked_send_can_be_retried(fake, config, url, answers, data):
    config('submit = ["fake"]')
    fake.submit_raises = Blocked("captcha_score")
    result = core.send(url, answers, data_dir=data)
    assert (result.status, result.reason) == (Status.BLOCKED, "captcha_score")
    fake.submit_raises = None
    assert core.send(url, answers, data_dir=data).status is Status.SUBMITTED


def test_status_lists_each_adapter(fake, config, data):
    config('submit = ["fake"]\nack_side_effects = ["fake"]')
    (state,) = core.status(data_dir=data).adapters
    assert (state.name, state.submit_enabled, state.side_effects_acknowledged) == (
        "fake",
        True,
        True,
    )
    assert state.side_effects == fake.side_effects


VERIFIED = registry.parse("""
[fake]
kind = "ats"
account = "none"
adapter = "fake"
send_verified = { date = 2026-10-18, version = "0.0.2", by = "user" }
""")


def test_a_send_warns_on_an_adapter_not_send_verified(fake, config, url, answers, data):
    config('submit = ["fake"]')
    result = core.send(url, answers, data_dir=data)
    assert (result.status, result.warning) == (
        Status.SUBMITTED,
        "not_send_verified: fake is not in the registry",
    )


def test_a_send_verified_adapter_sends_without_warning(
    fake, config, url, answers, data, monkeypatch
):
    monkeypatch.setattr(registry, "sources", lambda: VERIFIED)
    config('submit = ["fake"]')
    assert core.send(url, answers, data_dir=data).warning is None
    (state,) = core.status(data_dir=data).adapters
    assert (state.verified, state.verified_version) == ("send_verified", "0.0.2")


BROKEN = registry.parse("""
[fake]
kind = "ats"
account = "none"
adapter = "fake"
send_verified = { date = 2026-10-18, version = "0.0.1", by = "user", commit = "aaaaaaa" }

[fake.send_broken]
date = 2026-10-19
version = "0.0.2"
commit = "bbbbbbb"
by = "user"
reason = "resume empty"
""")


def test_a_broken_send_warns_and_shows_in_status(fake, config, url, answers, data, monkeypatch):
    monkeypatch.setattr(registry, "sources", lambda: BROKEN)
    monkeypatch.setattr(commands, "version", lambda _: "0.0.2")
    config('submit = ["fake"]')
    broken = "send broken since 2026-10-19 (0.0.2), resume empty; last verified 2026-10-18 (0.0.1)"
    assert core.send(url, answers, data_dir=data).warning == f"send_broken: {broken}"
    (state,) = core.status(data_dir=data).adapters
    assert (state.verified, state.verified_release, state.send_broken) == (
        "send_verified",
        "older",
        broken,
    )
