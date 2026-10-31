"""The Taleo adapter on tests/pages/taleo, served as an Acme zone's job submission wizard."""

from pathlib import Path

import pytest

from atsfill.adapters.taleo import Taleo, _masked
from atsfill.core import browser
from atsfill.core.adapter import NotTaken, Unsupported
from atsfill.core.results import FieldType, Is

PAGES = Path(__file__).parent / "pages/taleo"
BASE = "https://acme.taleo.net/careersection/ex"
POSTING = f"{BASE}/jobdetail.ftl?job=12345&lang=en"
FRESH_DRAFT = f"{BASE}/jobdetail.ftl?job=777&lang=en"
PAGE = "personal_information/"
COUNTRY = f"{PAGE}cpi-cfrmsub-frm-dv_cs_candidate_personal_info_ResidenceLocation-0"
STATE = f"{PAGE}cpi-cfrmsub-frm-dv_cs_candidate_personal_info_ResidenceLocation-1"
FIRST_NAME = f"{PAGE}cpi-cfrmsub-frm-dv_cs_candidate_personal_info_FirstName"
QUESTION = f"{PAGE}preq-q-0-qr"


def serve(route) -> None:
    page = "review.html" if "review.ftl" in route.request.url else "apply.html"
    route.fulfill(path=PAGES / page)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://acme.taleo.net/**", serve)
        yield page


def opened(page, url: str = POSTING) -> tuple[Taleo, dict]:
    adapter = Taleo()
    assert adapter.account_scope(url) == "acme"
    page.goto(url)
    return adapter, {f.id: f for f in adapter.browser_read(page)}


def test_detect_posting_key_and_login_url():
    adapter = Taleo()
    assert adapter.detect(POSTING, None)
    assert adapter.detect(POSTING.replace("jobdetail", "jobapply"), None)
    assert not adapter.detect(f"{BASE}/jobsearch.ftl", None)
    assert (
        adapter.posting(f"{BASE}/jobapply.ftl?lang=fr&job=12345")
        == f"{BASE}/jobdetail.ftl?job=12345"
    )
    with pytest.raises(ValueError):
        adapter.login_url("acme")
    adapter.account_scope(POSTING)
    url = adapter.login_url("acme")
    assert url.startswith("https://acme.taleo.net/careersection/iam/accessmanagement/login.jsf")
    assert "jobapply.ftl" in url


def test_browser_read_lists_each_rendering(page):
    _, fields = opened(page)
    first = fields[f"{PAGE}cpi-cfrmsub-frm-dv_cs_candidate_personal_info_FirstName"]
    assert first.label == "Legal First Name" and first.required and first.type is FieldType.TEXT
    phone = fields[f"{PAGE}cpi-cfrmsub-frm-dv_cs_candidate_personal_info_HomePhone"]
    assert phone.type is FieldType.PHONE
    assert fields[COUNTRY].options == ["Canada", "United States"] and fields[COUNTRY].required
    assert "location_levels" in (fields[STATE].unfillable or "")  # loaded once a country is
    assert fields[f"{PAGE}sourceTrackingBlock-recruitmentSourceType"].options == [
        "Job Board",
        "Website",
    ]
    availability = fields[f"{PAGE}cfrm-cfrmsub-frm-dv_cs_basicprofile_AvailabilityDate"]
    assert "date_picker" in (availability.unfillable or "")
    current = fields[f"{PAGE}we-wei-0-frm-dv_cs_experience_CurrentEmployer"]
    assert current.options == ["Yes", "No"] and current.default == "No"
    start = fields[f"{PAGE}we-wei-0-frm-dv_cs_experience_BeginDate"]
    assert start.label == "Start Date (MM/YYYY)" and start.required
    question = fields[QUESTION]
    assert question.label == "Have you worked for Acme before?" and question.required
    assert question.options == ["Yes", "No"]
    explanation = fields[f"{PAGE}preq-q-0-questionExplanationText"]
    assert explanation.shown_when == Is(QUESTION, ["Yes"])
    settings = fields[f"{PAGE}preq-q-1-qr"]
    assert settings.type is FieldType.MULTI_CHOICE
    assert settings.options == ["Office", "None of the Above"]
    assert not any("sendMeJobs" in f for f in fields)  # job alerts are no part of applying


def test_fill_saves_the_page_and_reads_it_back(page):
    adapter, _ = opened(page)
    answers = {
        f"{PAGE}cpi-cfrmsub-frm-dv_cs_candidate_personal_info_FirstName": "Jane",
        f"{PAGE}cpi-cfrmsub-frm-dv_cs_candidate_personal_info_HomePhone": "+12025550143",
        COUNTRY: "United States",
        f"{PAGE}we-wei-0-frm-dv_cs_experience_CurrentEmployer": "Yes",
        f"{PAGE}we-wei-0-frm-dv_cs_experience_BeginDate": "02/2020",
        QUESTION: "Yes",
        f"{PAGE}preq-q-0-questionExplanationText": "2019",
        f"{PAGE}preq-q-1-qr": ["Office"],
    }
    adapter.fill(page, answers)
    assert "review.ftl" in page.url  # saved, on Review and Submit
    back = adapter.read_back(page)
    assert {k: back.get(k) for k in answers} == {
        **answers,
        f"{PAGE}cpi-cfrmsub-frm-dv_cs_candidate_personal_info_HomePhone": "+1 (202)555-0143",
    }


def test_an_answer_that_shows_an_optional_field_stops_the_fill(page):
    adapter, _ = opened(page)
    with pytest.raises(
        Unsupported, match="^unhandled_rendering: an answer showed Tell us why not, not listed$"
    ):
        adapter.fill(page, {QUESTION: "No"})
    assert "jobapply.ftl" in page.url  # stopped before saving


def test_a_page_the_read_did_not_reach_is_read_once_the_one_before_saves(page):
    page.goto(FRESH_DRAFT)
    page.evaluate("sessionStorage.clear()")
    adapter, fields = opened(page, FRESH_DRAFT)
    assert FIRST_NAME in fields and "attachments/portfolio" not in fields
    with pytest.raises(NotTaken, match="^Personal Information was not saved: .*first name.$"):
        adapter.fill(page, {})
    with pytest.raises(
        NotTaken, match="^Attachments shows fields the read did not list: Portfolio; read again$"
    ):
        adapter.fill(page, {FIRST_NAME: "Jane"})
    adapter, fields = opened(page, FRESH_DRAFT)
    assert "attachments/portfolio" in fields
    adapter.fill(page, {FIRST_NAME: "Jane", "attachments/portfolio": "https://example.com"})
    assert "review.ftl" in page.url
    assert adapter.read_back(page)["attachments/portfolio"] == "https://example.com"


def test_a_north_american_number_takes_the_mask():
    assert _masked("+12025550143") == "(202)555-0143"
    assert _masked("2025550143") == "(202)555-0143"
    assert _masked("+447700900123") == "+447700900123"
