from contextlib import contextmanager

import pytest

from atsfill import core
from atsfill.cli import parser, run
from atsfill.core import attempts, commands, registry
from atsfill.core.adapter import Blocked, NotTaken, Unsupported, known_gap, out_of_scope
from atsfill.core.browser import Timeout
from atsfill.core.results import Field, FieldType, Is, Status
from tests.fake import FIELDS, Fake


@pytest.fixture
def fake(monkeypatch) -> Fake:
    adapter = Fake()
    monkeypatch.setattr(commands, "adapters", lambda: [adapter])
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


def test_read_detects_from_the_url_a_redirect_lands_on(fake, config, server, data):
    result = core.read(f"{server}/go/button.html%3Ffake", data_dir=data)
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


def test_muted_side_effects_are_left_out(fake, config, url, data):
    config('mute-side-effect-warnings = ["all"]')
    assert core.read(url, data_dir=data).side_effects == []


def test_fill_reads_every_answer_back(fake, config, url, answers, data):
    result = core.fill(url, answers, data_dir=data)
    assert result.status is Status.READY, result.reason
    assert [c.field_id for c in result.checks if c.match] == list(answers)
    assert not (data / "users/default/attempts.jsonl").exists()


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
        (Blocked("email_otp"), Status.BLOCKED, "email_otp"),
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


def test_a_field_a_fill_shows_that_read_did_not_list_stops_it(fake, config, url, answers, data):
    fake.shows = ["salary"]
    result = core.fill(url, answers, data_dir=data)
    assert (result.status, result.reason) == (
        Status.UNSUPPORTED,
        "unhandled_rendering: an answer showed salary, not listed",
    )


def test_a_follow_up_shown_though_its_condition_is_unmet_refuses(fake, config, url, answers, data):
    why = Field("why", "Why?", FieldType.TEXT, False, shown_when=Is("level", ["Native"]))
    fake.fields, fake.shows = [*FIELDS, why], ["why"]
    result = core.fill(url, answers, data_dir=data)
    assert (result.status, result.reason) == (
        Status.REFUSED,
        "mismatch: why shows though its condition is unmet",
    )


def test_a_condition_on_a_field_not_listed_is_an_adapter_error(fake, config, url, data):
    fake.fields = [*FIELDS, Field("why", "Why?", FieldType.TEXT, False, shown_when=Is("x"))]
    result = core.read(url, data_dir=data)
    assert (result.status, result.reason) == (
        Status.UNSUPPORTED,
        "adapter_error: ValueError: why: its condition names no field x",
    )


def test_a_known_gap_names_its_code_and_id():
    assert known_gap(Fake(), "ranking", "a ranked list") == (
        "unsupported_rendering: ranking: a ranked list"
    )


def test_send_needs_the_adapter_in_the_config(fake, config, url, answers, data):
    result = core.send(url, answers, data_dir=data)
    assert result.status is Status.REFUSED
    assert result.reason and result.reason.startswith("submit_not_enabled")


def test_send_once_then_resend(fake, config, url, answers, data):
    config('submit-allowed = ["fake"]')
    first = core.send(url, answers, data_dir=data)
    assert (first.status, first.evidence) == (Status.SUBMITTED, f"{url}?sent=1")
    again = core.send(url, answers, data_dir=data)
    assert again.status is Status.REFUSED
    assert again.reason == "already_sent: last attempt ended submitted; pass --resend"
    assert core.send(url, answers, resend=True, data_dir=data).status is Status.SUBMITTED


def test_a_refused_send_never_clicks(fake, config, url, answers, data):
    config('submit-allowed = ["fake"]')
    fake.typo = True
    assert core.send(url, answers, data_dir=data).status is Status.REFUSED
    assert attempts.last(data / "users/default", url) is None


def test_no_evidence_is_unknown(fake, config, url, answers, data):
    config('submit-allowed = ["fake"]')
    fake.evidence = False
    result = core.send(url, answers, data_dir=data)
    assert (result.status, result.reason) == (Status.UNKNOWN, "no_evidence")


def test_a_timeout_past_the_click_is_unknown_and_guards(fake, config, url, answers, data):
    config('submit-allowed = ["fake"]')
    fake.submit_raises = Timeout("no response")
    result = core.send(url, answers, data_dir=data)
    assert result.status is Status.UNKNOWN
    assert result.reason and result.reason.startswith("error_after_click: TimeoutError")
    assert (core.send(url, answers, data_dir=data).reason or "").startswith("already_sent")


def test_a_blocked_send_can_be_retried(fake, config, url, answers, data):
    config('submit-allowed = ["fake"]')
    fake.submit_raises = Blocked("captcha_score")
    result = core.send(url, answers, data_dir=data)
    assert (result.status, result.reason) == (Status.BLOCKED, "captcha_score")
    fake.submit_raises = None
    assert core.send(url, answers, data_dir=data).status is Status.SUBMITTED


def test_status_lists_each_adapter_per_user(fake, config, data):
    config(
        'submit-allowed = ["fake"]\nmute-side-effect-warnings = ["fake"]\n'
        '[users.test]\nmute-side-effect-warnings = ["all"]'
    )
    report = core.status(data_dir=data)
    (state,) = report.adapters
    assert report.users == ["default", "test"]
    assert (state.name, state.submit_allowed, state.side_effects_muted) == (
        "fake",
        ["default"],
        ["default", "test"],
    )
    assert state.side_effects == fake.side_effects


def test_each_user_has_their_own_double_send_guard(fake, config, url, answers, data):
    config('submit-allowed = ["fake"]\n[users.other]\nsubmit-allowed = ["fake"]')
    assert core.send(url, answers, data_dir=data).status is Status.SUBMITTED
    assert core.send(url, answers, user="other", data_dir=data).status is Status.SUBMITTED
    assert attempts.last(data / "users/other", url) == Status.SUBMITTED


def test_the_test_user_cannot_send_without_its_own_config(fake, config, url, answers, data):
    config('submit-allowed = ["fake"]\n[users.test]\nmute-side-effect-warnings = ["all"]')
    result = core.send(url, answers, user="test", data_dir=data)
    assert (result.status, result.side_effects) == (Status.REFUSED, [])
    assert result.reason and result.reason.startswith(
        "submit_not_enabled: add fake to `submit-allowed` under [users.test]"
    )


@pytest.mark.parametrize(
    "text, new",
    [
        ('submit = ["fake"]', "submit-allowed"),
        ('[users.test]\nack_side_effects = ["all"]', "mute-side-effect-warnings"),
    ],
)
def test_an_old_config_key_names_the_new_one(fake, config, data, capsys, text, new):
    config(text)
    assert run(parser().parse_args(["--data-dir", str(data), "status"])) == 2
    assert f"is now `{new}`" in capsys.readouterr().err


def test_a_data_directory_from_before_users_moves_to_the_default_user(fake, config, url, data):
    data.mkdir()
    (data / "sessions").mkdir()
    (data / "sessions/fake.json").write_text("{}")
    attempts.record(data, url, Status.SUBMITTED)
    for _ in range(2):  # idempotent
        assert core.status(data_dir=data).users == ["default"]
    assert (data / "users/default/sessions/fake.json").exists()
    assert attempts.last(data / "users/default", url) == Status.SUBMITTED
    assert not (data / "sessions").exists() and not (data / "attempts.jsonl").exists()


VERIFIED = registry.parse("""
[fake]
kind = "ats"
account = "none"
adapter = "fake"
send_verified = { date = 2026-10-18, version = "0.0.2", by = "user" }
""")


def test_a_send_warns_on_an_adapter_not_send_verified(fake, config, url, answers, data):
    config('submit-allowed = ["fake"]')
    result = core.send(url, answers, data_dir=data)
    assert (result.status, result.warning) == (
        Status.SUBMITTED,
        "not_send_verified: fake is not in the registry",
    )


def test_a_ready_dry_run_warns_before_the_send(fake, config, url, answers, data):
    result = core.fill(url, answers, data_dir=data)
    assert (result.status, result.warning) == (
        Status.READY,
        "not_send_verified: fake is not in the registry",
    )


def test_a_send_verified_adapter_sends_without_warning(
    fake, config, url, answers, data, monkeypatch
):
    monkeypatch.setattr(registry, "sources", lambda: VERIFIED)
    config('submit-allowed = ["fake"]')
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
    config('submit-allowed = ["fake"]')
    broken = "send broken since 2026-10-19 (0.0.2), resume empty; last verified 2026-10-18 (0.0.1)"
    assert core.send(url, answers, data_dir=data).warning == f"send_broken: {broken}"
    (state,) = core.status(data_dir=data).adapters
    assert (state.verified, state.verified_release, state.send_broken) == (
        "send_verified",
        "older",
        broken,
    )


class Remembers(Fake):
    """Keeps the page it reads, as an adapter keeps what it found on its posting's page."""

    def __init__(self) -> None:
        super().__init__()
        self.read_on: str | None = None

    def read_form(self, page):
        if self.read_on:
            raise Unsupported(f"carried: {self.read_on}")
        self.read_on = page.url
        return self.fields


def test_no_state_carries_from_one_posting_to_the_next(config, server, data, monkeypatch):
    monkeypatch.setattr(commands, "adapters", lambda: [Remembers()])
    first = core.read(f"{server}/form.html", data_dir=data)
    second = core.read(f"{server}/form.html?second", data_dir=data)
    assert (first.status, second.status, second.reason) == (Status.READY, Status.READY, None)
