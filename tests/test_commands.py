from contextlib import contextmanager
from dataclasses import replace

import pytest

from atsfill import core
from atsfill.cli import parser, run
from atsfill.core import attempts, commands, registry, telemetry
from atsfill.core.adapter import Blocked, Held, NotTaken, Unsupported, known_gap, out_of_scope
from atsfill.core.browser import Timeout
from atsfill.core.results import Field, FieldType, Is, Status
from tests.fake import FIELDS, Fake, FakeOpens, FakeOpensForm, FakeProfile
from tests.test_accounts import storage_state


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


def test_an_adapter_that_opens_its_form_loads_it_in_place_of_the_posting(
    config, server, data, monkeypatch
):
    adapter, opened, loads = FakeOpensForm(), commands.open_page, []
    monkeypatch.setattr(commands, "adapters", lambda: [adapter])

    @contextmanager
    def counted(*args, **kwargs):
        with opened(*args, **kwargs) as page:
            page.on(
                "request", lambda r: loads.extend([r.url] if r.resource_type == "document" else [])
            )
            yield page

    monkeypatch.setattr(commands, "open_page", counted)
    result = core.read(f"{server}/button.html?fake", data_dir=data)
    assert (result.status, loads) == (Status.READY, [f"{server}/form.html"])


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


def test_a_knockout_answer_fills_with_a_warning(fake, config, url, answers, data):
    fake.fields = [replace(f, knockouts=["Fluent"]) if f.id == "level" else f for f in FIELDS]
    result = core.fill(url, answers, data_dir=data)
    assert result.status is Status.READY, result.reason
    assert result.warnings == ["level: 'Fluent' ends the application (Level)"]


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
        (Blocked("otp_rejected"), Status.BLOCKED, "otp_rejected"),
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


def test_a_required_group_stops_the_form_only_when_no_alternative_can_be_filled(
    fake, config, url, answers, data
):
    name = replace(FIELDS[0], one_of="intro")
    video = Field("video", "Video", FieldType.FILE, True, unfillable=UNFILLABLE[0], one_of="intro")
    fake.fields = [name, *FIELDS[1:], video]
    assert core.read(url, data_dir=data).status is Status.READY
    result = core.fill(url, answers, data_dir=data)
    assert result.status is Status.READY, result.reason
    fake.fields = [replace(name, unfillable=UNFILLABLE[2]), *FIELDS[1:], video]
    result = core.read(url, data_dir=data)
    assert (result.status, result.reason) == (Status.UNSUPPORTED, UNFILLABLE[2])  # the first


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


def test_a_held_send_is_submitted_and_guards(fake, config, url, answers, data):
    config('submit-allowed = ["fake"]')
    fake.submit_raises = Held("click the emailed link")
    result = core.send(url, answers, data_dir=data)
    assert (result.status, result.reason) == (Status.SUBMITTED, "held: click the emailed link")
    assert result.evidence
    again = core.send(url, answers, data_dir=data)
    assert again.reason == "already_sent: last attempt ended submitted; pass --resend"


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


@pytest.fixture
def opens(monkeypatch) -> FakeOpens:
    adapter = FakeOpens()
    monkeypatch.setattr(commands, "adapters", lambda: [adapter])
    return adapter


def test_a_form_that_sends_to_open_needs_the_adapter_in_the_config_even_to_read(
    opens, config, url, data
):
    result = core.read(url, data_dir=data)
    assert result.status is Status.REFUSED
    assert (result.reason or "").startswith("submit_not_enabled: add fake")
    assert (result.reason or "").endswith("opening its form sends the application")
    assert opens.opens == 0


def test_opening_sends_once_and_its_own_send_goes_through(opens, config, url, answers, data):
    config('submit-allowed = ["fake"]')
    mine = data / "users/default"
    assert core.fill(url, answers, data_dir=data).status is Status.READY
    assert attempts.last(mine, url) == attempts.OPENED
    assert core.fill(url, answers, data_dir=data).status is Status.READY  # continues it
    assert core.send(url, answers, data_dir=data).status is Status.SUBMITTED
    assert (core.send(url, answers, data_dir=data).reason or "").startswith("already_sent")


def test_an_unexpected_error_opening_may_have_sent(opens, config, url, answers, data):
    config('submit-allowed = ["fake"]')
    opens.open_raises = RuntimeError("no form")
    assert core.fill(url, answers, data_dir=data).status is Status.UNSUPPORTED
    opens.open_raises = None
    assert (core.send(url, answers, data_dir=data).reason or "").startswith(
        "already_sent: last attempt ended unknown"
    )


def test_a_stop_before_opening_records_nothing(opens, config, url, answers, data):
    config('submit-allowed = ["fake"]')
    opens.open_raises = Unsupported("posting_gone: closed")
    assert core.fill(url, answers, data_dir=data).reason == "posting_gone: closed"
    assert attempts.last(data / "users/default", url) is None


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


SUPPORTED = registry.parse("""
[fake]
kind = "ats"
account = "none"
adapter = "fake"
scope = { is = "supported", date = 2026-10-18, commit = "aaaaaaa" }
""")


def test_status_shows_each_adapters_scope(fake, config, data, monkeypatch):
    (state,) = core.status(data_dir=data).adapters
    assert state.scope is None
    monkeypatch.setattr(registry, "sources", lambda: SUPPORTED)
    (state,) = core.status(data_dir=data).adapters
    assert state.scope == "supported"


class Remembers(Fake):
    """Keeps the page it reads, as an adapter keeps what it found on its posting's page."""

    def __init__(self) -> None:
        super().__init__()
        self.read_on: str | None = None

    def browser_read(self, page):
        if self.read_on:
            raise Unsupported(f"carried: {self.read_on}")
        self.read_on = page.url
        return self.fields


def test_no_state_carries_from_one_posting_to_the_next(config, server, data, monkeypatch):
    monkeypatch.setattr(commands, "adapters", lambda: [Remembers()])
    first = core.read(f"{server}/form.html", data_dir=data)
    second = core.read(f"{server}/form.html?second", data_dir=data)
    assert (first.status, second.status, second.reason) == (Status.READY, Status.READY, None)


@pytest.fixture
def keeper(server, config, data, monkeypatch) -> FakeProfile:
    """A system that keeps an account profile, the default user signed in to it."""
    adapter = FakeProfile(server)
    monkeypatch.setattr(commands, "adapters", lambda: [adapter])
    monkeypatch.setattr("atsfill.cli.adapters", lambda: [adapter])
    session = data / "users/default/sessions/fakeprofile.json"
    session.parent.mkdir(parents=True)
    storage_state(session, "ok")
    return adapter


PROFILE = {"name": "Ada", "level": "Fluent"}


def test_a_profile_read_lists_the_steps_it_can_load_and_names_the_rest(keeper, data):
    result = core.profile("fakeprofile", data_dir=data)
    assert (result.status, [f.id for f in result.fields], result.unloaded) == (
        Status.READY,
        ["name"],
        ["level"],
    )
    assert keeper.saved == {}


def test_a_profile_saves_each_step_then_reads_back_as_saved(keeper, data):
    result = core.profile("fakeprofile", PROFILE, data_dir=data)
    assert (result.status, result.reason, result.saved) == (Status.READY, None, ["about", "level"])
    assert keeper.saved == {"about": {"name": "Ada"}, "level": {"level": "Fluent"}}
    read = core.profile("fakeprofile", data_dir=data)
    assert [(f.id, f.prefilled) for f in read.fields] == [("name", "Ada"), ("level", "Fluent")]


def test_a_profile_run_again_saves_nothing(keeper, data):
    core.profile("fakeprofile", PROFILE, data_dir=data)
    result = core.profile("fakeprofile", PROFILE, data_dir=data)
    assert (result.status, result.saved) == (Status.READY, [])


def test_a_profile_mismatch_stops_after_the_steps_saved(keeper, data, monkeypatch):
    read_back = keeper.profile_read_back
    monkeypatch.setattr(
        keeper,
        "profile_read_back",
        lambda page: {"level": "Native"} if keeper.step == "level" else read_back(page),
    )
    result = core.profile("fakeprofile", PROFILE, data_dir=data)
    assert (result.status, result.reason, result.saved) == (
        Status.REFUSED,
        "mismatch: level",
        ["about"],
    )
    assert keeper.saved == {"about": {"name": "Ada"}}


def test_a_step_the_page_refuses_stops_the_profile(keeper, data):
    keeper.refuses = "phone_rule: a phone is required"
    result = core.profile("fakeprofile", PROFILE, data_dir=data)
    assert (result.status, result.reason, result.saved) == (
        Status.REFUSED,
        "phone_rule: a phone is required",
        [],
    )


def test_an_answer_no_step_lists_refuses(keeper, data):
    result = core.profile("fakeprofile", {**PROFILE, "phone": "1"}, data_dir=data)
    assert (result.status, result.reason) == (Status.REFUSED, "invalid_answers: no field phone")


def test_a_form_command_on_an_incomplete_profile_names_the_profile_command(
    keeper, server, answers, data
):
    result = core.fill(f"{server}/form.html?account", answers, data_dir=data)
    assert (result.status, result.reason) == (
        Status.BLOCKED,
        "profile_incomplete: no about, level, run `atsfill profile fakeprofile`",
    )
    assert telemetry.verdict(result) == "fail"


def test_profile_needs_an_adapter_that_keeps_one(fake, config, data):
    with pytest.raises(ValueError):
        core.profile("fake", data_dir=data)
