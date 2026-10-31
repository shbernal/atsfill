"""The Greenhouse adapter on tests/pages/greenhouse, served as its hosted page and embedded."""

import sys
from pathlib import Path

import pytest

from atsfill.adapters import greenhouse
from atsfill.adapters.greenhouse import Greenhouse, _api_fields
from atsfill.core import browser, matching, otp
from atsfill.core.adapter import Blocked
from atsfill.core.results import Answer, FieldType, Is

PAGE = Path(__file__).parent / "pages/greenhouse/job.html"
JOB = "https://job-boards.greenhouse.io/acme/jobs/1234"
EMPLOYER_PAGE = "https://www.example.com/careers?gh_jid=1234&gh_src=abc"
SCHOOLS = ["Acme College", "Acme University", "Other College", "Other"]  # two per page


def education(url: str) -> dict:
    """The board's education endpoint: School's options over two pages, the menu shows one."""
    assert url.startswith("https://boards-api.greenhouse.io/v1/boards/acme/education/schools?")
    page = int(url.rsplit("=", 1)[1])
    return {
        "items": [{"text": s} for s in SCHOOLS[2 * page - 2 : 2 * page]],
        "meta": {"total_count": 4},
    }


def serve(route) -> None:
    url = route.request.url
    if url.startswith("https://www.example.com/"):
        src = "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=1234&b=https%3A%2F%2Fx"
        route.fulfill(content_type="text/html", body=f'<iframe id="grnhse_iframe" src="{src}">')
    elif url.endswith("/confirmation"):
        route.fulfill(content_type="text/html", body="<h1>Thank you for applying</h1>")
    else:
        route.fulfill(path=PAGE)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,  # the fixture's selects open at once
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr(greenhouse, "OPTIONS_TIMEOUT_MS", 500)
        mp.setattr(greenhouse, "_get_json", education)
        for host in ("https://job-boards.greenhouse.io/**", "https://www.example.com/**"):
            page.context.route(host, serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def opened(page, url: str = JOB) -> tuple[Greenhouse, list]:
    adapter = Greenhouse()
    page.goto(url)
    assert adapter.detect(url, page)
    return adapter, adapter.browser_read(page)


def test_browser_read_waits_for_the_live_page(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "first_name",
        "email",
        "phone",
        "candidate-location",
        "resume",
        "resume_text",
        "cover_letter",
        "cover_letter_text",
        "question_1",
        "question_3",
        "school--0",
        "start-year--0",
        "question_4",
        "question_2[]",
        "4000952002",
        "hispanic_ethnicity",
        "race",
        "gdpr_processing_consent_given",
    ]
    assert by_id["phone"].type is FieldType.PHONE
    assert by_id["candidate-location"].type is FieldType.TYPEAHEAD
    assert by_id["start-year--0"].type is FieldType.TEXT
    assert by_id["school--0"].options == SCHOOLS
    assert by_id["question_4"].type is FieldType.MULTI_CHOICE
    assert by_id["question_2[]"].type is FieldType.MULTI_CHOICE
    assert by_id["gdpr_processing_consent_given"].label == "I agree to the processing of my data."
    assert by_id["resume"].accept == [".pdf", ".doc"]
    text = by_id["resume_text"]
    assert (text.type, text.required, text.one_of) == (FieldType.TEXT, True, "resume")
    assert by_id["resume"].one_of == "resume" and not by_id["cover_letter_text"].required
    assert not by_id["4000952002"].required


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page)
    answers = {
        "first_name": "Ada",
        "phone": "+15145550100",
        "candidate-location": "Paris",
        "resume": resume,
        "cover_letter_text": "Dear Acme,",  # entered manually
        "question_1": "No",
        "question_3": "Placeholder answer.",
        "school--0": "Other",  # second of the options typing it shows, past the menu's first page
        "start-year--0": "2020",
        "question_4": ["Rust", "Go"],
        "question_2[]": ["French", "English"],
        "4000952002": "Decline To Self Identify",
        "race": "Asian",  # listed first: it waits for the answer that shows it
        "hispanic_ethnicity": "No",
        "gdpr_processing_consent_given": "Yes",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_race_is_a_follow_up_the_read_leaves_hidden(page):
    adapter, fields = opened(page)
    race = next(f for f in fields if f.id == "race")
    assert race.label == "Please identify your race"
    assert race.options == ["Asian", "White", "Decline To Self Identify"]
    assert race.shown_when == Is("hispanic_ethnicity", ["No"]) and not race.required
    assert "race" not in adapter.shown_fields(page)
    assert "hispanic_ethnicity" not in adapter.read_back(page)


@pytest.mark.parametrize(("answer", "shows"), [("No", True), ("Yes", False)])
def test_shown_fields_follow_the_page(page, answer, shows):
    adapter, _ = opened(page)
    adapter.fill(page, {"hispanic_ethnicity": answer})
    assert ("race" in adapter.shown_fields(page)) is shows


def test_shown_fields_name_a_field_the_read_did_not_list(page):
    adapter, _ = opened(page)
    page.evaluate(
        """() => document.getElementById("application-form").insertAdjacentHTML("beforeend",
        '<label for="extra">Extra</label><input id="extra" type="text">')"""
    )
    assert "extra" in adapter.shown_fields(page)


def test_a_resume_entered_manually_leaves_the_file_input(page):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {"resume_text": "Jane Doe, engineer"}
    adapter.fill(page, answers)
    assert matching.check(fields, answers, adapter.read_back(page))[0].match
    assert page.locator("input#resume").count() == 1


def test_education_options_from_the_menu_without_the_endpoint(page, monkeypatch):
    def down(url: str) -> dict:
        raise OSError(url)

    monkeypatch.setattr(greenhouse, "_get_json", down)
    _, fields = opened(page)
    assert next(f for f in fields if f.id == "school--0").options == SCHOOLS[:2]


def test_a_number_input_left_empty_for_text(page):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {"start-year--0": "twenty"}
    adapter.fill(page, answers)
    assert not matching.check(fields, answers, adapter.read_back(page))[0].match


def test_a_radio_is_listed_unhandled(page):
    page.goto(JOB)
    page.evaluate(
        """() => document.getElementById("application-form").insertAdjacentHTML("afterbegin",
        '<label for="r1">Remote</label><input type="radio" id="r1" name="remote">')"""
    )
    radio = next(f for f in Greenhouse().browser_read(page) if f.id == "r1")
    assert radio.unfillable == "unhandled_rendering: r1 is a radio"


def test_submit_returns_the_confirmation(page):
    adapter, _ = opened(page)
    assert adapter.submit(page) == JOB + "/confirmation"


@pytest.fixture
def otp_prints(tmp_path, monkeypatch):
    """Gives the default user an `otp-commands` entry printing the OTP given."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    monkeypatch.setattr(otp, "POLL_S", 0.1)

    def entry(printed: str) -> None:
        config = tmp_path / "conf/atsfill/config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        script = tmp_path / "otp.py"
        script.write_text(f"print({printed!r})")
        config.write_text(f'[otp-commands]\nall = "{sys.executable} {script}"\n')

    return entry


def test_an_otp_is_typed_in_and_submitted(page, otp_prints):
    otp_prints("Ab3dEf7h")
    adapter, _ = opened(page, JOB + "?otp")
    with otp.source("default", adapter.name, None):
        assert adapter.submit(page) == JOB + "/confirmation"


def test_an_otp_not_taken_is_blocked(page, otp_prints):
    otp_prints("Zz9zZz9z")
    adapter, _ = opened(page, JOB + "?otp")
    with (
        otp.source("default", adapter.name, None),
        pytest.raises(Blocked, match="^otp_rejected: the submit did not take the OTP: Incorrect"),
    ):
        adapter.submit(page)


def test_an_otp_without_an_otp_command_is_blocked(page, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    adapter, _ = opened(page, JOB + "?otp")
    with (
        otp.source("default", adapter.name, None),
        pytest.raises(Blocked, match="no_otp_command: the submit sent an OTP to jane@example.com"),
    ):
        adapter.submit(page)


def test_an_embedding_company_page(page):
    adapter, fields = opened(page, EMPLOYER_PAGE)
    assert (
        page.url == "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=1234&gh_src=abc"
    )
    assert adapter.posting(EMPLOYER_PAGE) == JOB
    assert fields


def test_board_urls():
    adapter = Greenhouse()
    for url in (
        JOB,
        "https://boards.greenhouse.io/acme/jobs/1234?gh_src=x",
        "https://job-boards.eu.greenhouse.io/acme/jobs/1234",
        "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=1234",
    ):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == JOB
    assert not adapter.detect("https://job-boards.greenhouse.io/acme", None)


def test_api_fields():
    job = {
        "questions": [
            {
                "label": "First Name",
                "required": True,
                "fields": [{"name": "first_name", "type": "input_text"}],
            },
            {
                "label": "Phone",
                "required": True,
                "fields": [{"name": "phone", "type": "input_text"}],
            },
            {
                "label": "Resume/CV",
                "required": True,
                "fields": [
                    {"name": "resume", "type": "input_file"},
                    {"name": "resume_text", "type": "textarea"},
                ],
            },
            {
                "label": "Sponsorship?",
                "required": True,
                "fields": [
                    {
                        "name": "question_1",
                        "type": "multi_value_single_select",
                        "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}],
                    }
                ],
            },
        ],
    }
    assert [(f.id, f.type, f.required, f.one_of) for f in _api_fields(job) or []] == [
        ("first_name", FieldType.TEXT, True, None),
        ("phone", FieldType.PHONE, True, None),
        ("resume", FieldType.FILE, True, "resume"),
        ("resume_text", FieldType.TEXT, True, "resume"),
        ("question_1", FieldType.CHOICE, True, None),
    ]
    location = [
        {
            "label": "Latitude",
            "required": True,
            "fields": [{"name": "latitude", "type": "input_hidden"}],
        },
        {
            "label": "Location",
            "required": True,
            "fields": [{"name": "location", "type": "input_text"}],
        },
    ]
    assert (_api_fields(job | {"location_questions": location}) or [])[
        -1
    ].id == "candidate-location"
    assert _api_fields(job | {"demographic_questions": {"questions": [{"id": 1}]}}) is None
    assert _api_fields(job | {"compliance": [{"type": "eeoc", "questions": []}]}) is None
    assert _api_fields(job | {"education": "education_optional"}) is None
    consent = [{"type": "gdpr", "requires_consent": True}]
    assert _api_fields(job | {"data_compliance": consent}) is None
