"""The Hireology adapter on tests/pages/hireology, served as a job page and its form API."""

import json
from pathlib import Path

import pytest

from atsfill.adapters.hireology import Hireology, _form
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import Answer, FieldType
from tests import persona

PAGES = Path(__file__).parent / "pages/hireology"
JOB = "https://careers.hireology.com/acme/1234567/description"
API = "https://api.hireology.com/v2/public/application_forms/1234567"
FORM = json.loads((PAGES / "form.json").read_text())


def serve(route) -> None:
    url = route.request.url
    if url == API:
        route.fulfill(json=FORM)
    elif "/application_forms/" in url:
        route.fulfill(status=404, json={"error": "not found"})
    elif route.request.method == "POST":
        route.fulfill(status=201, json={})
    elif "/7654321/" in url:
        route.fulfill(content_type="text/html", body="<h1>This job is no longer available</h1>")
    else:
        route.fulfill(path=PAGES / "description.html")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.hireology.FORM_TIMEOUT_MS", 5_000)
        page.context.route("https://careers.hireology.com/**", serve)
        page.context.route("https://api.hireology.com/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    return str(persona.resume(tmp_path / "CV Ada.pdf"))


def opened(page) -> tuple[Hireology, list]:
    adapter = Hireology()
    page.goto(JOB)
    assert adapter.detect(JOB, None)
    return adapter, adapter.read_form(page)


def test_read_form(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "first_name",
        "last_name",
        "email_address",
        "home_phone",
        "sms_opt_in",
        "street_address",
        "state_id",
        "zip_code",
        "candidate_referred",
        "referred_by",
        "resume",
        "desired_compensation",
        "desired_compensation_unit",
        "earliest_availability",
        "company_name",
        "start_date",
        "current",
        "highest_grade",
        "institute_name",
        "custom_why_do_you_want_this_role",
        "legal",
        "signature",
    ]
    assert by_id["home_phone"].type is FieldType.PHONE
    assert by_id["state_id"].options == ["DC", "MD"]
    assert by_id["sms_opt_in"].options == ["Yes", "No"]
    assert not by_id["sms_opt_in"].required  # a box left clear is an answer
    assert by_id["referred_by"].label == (
        "Name of person who referred you (asked when 'Were you referred by anyone?' is Yes)"
    )
    assert not by_id["referred_by"].required
    assert by_id["resume"].accept == [".pdf", ".doc", ".docx"]
    assert by_id["earliest_availability"].type is FieldType.DATE
    assert by_id["start_date"].label == "Work history: Start date (MM/YYYY)"
    assert by_id["institute_name"].label.endswith(
        "(asked when 'Education: Highest level of education completed'"
        " is not Did Not Complete High School)"
    )
    assert by_id["custom_why_do_you_want_this_role"].label == "Why do you want this role?"
    assert by_id["custom_why_do_you_want_this_role"].max_length == 255
    assert by_id["legal"].label == "Do you have a legal right to work in the United States?"
    assert by_id["signature"].label == "Signature"


def test_read_api_lists_the_same_fields(page):
    _, fields = opened(page)
    shape = lambda fs: [(f.id, f.label, f.type, f.required, f.options) for f in fs]  # noqa: E731
    assert shape(f for f, _ in _form(FORM)) == shape(fields)


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {
        "first_name": "Ada",
        "last_name": "Placeholder",
        "email_address": "ada@example.com",
        "home_phone": "+12025550143",
        "sms_opt_in": "No",
        "street_address": "1 Placeholder St",
        "state_id": "DC",
        "zip_code": "20001",
        "candidate_referred": "Yes",
        "referred_by": "Jane Doe",
        "resume": resume,
        "desired_compensation": "50000",
        "desired_compensation_unit": "Hourly",
        "earliest_availability": "2026-11-20",
        "company_name": "Acme",
        "start_date": "01/2020",
        "current": "Yes",
        "highest_grade": "High School/GED",
        "institute_name": "Acme High School",
        "custom_why_do_you_want_this_role": "Placeholder answer.",
        "legal": "Yes",
        "signature": "Ada Placeholder",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_a_field_its_answer_hides_is_not_read_back(page):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {"highest_grade": "Did Not Complete High School"}
    adapter.fill(page, answers)
    read_back = adapter.read_back(page)
    assert read_back["highest_grade"] == "Did Not Complete High School"
    assert "institute_name" not in read_back


def test_the_persona_leaves_out_what_its_answers_hide(tmp_path):
    fields = [f for f, _ in _form(FORM)]
    answers = persona.answers(fields, tmp_path / "cv.pdf")
    assert answers["highest_grade"] == "Did Not Complete High School"
    assert "institute_name" not in answers
    assert answers["candidate_referred"] == "Yes"
    assert "referred_by" in answers


def test_a_posting_gone(page):
    page.goto("https://careers.hireology.com/acme/7654321/description")
    with pytest.raises(Unsupported, match="posting_gone"):
        Hireology().read_form(page)


def test_an_unknown_field_type_is_unsupported():
    form = json.loads(json.dumps(FORM))
    form["template"]["sections"][0]["fieldsets"][0]["fields"][0]["attributes"]["type"] = "rating"
    with pytest.raises(Unsupported, match="unsupported_rendering: first_name is a rating"):
        _form(form)


def test_a_field_missing_from_the_page_is_unsupported(page):
    page.goto(JOB)
    page.evaluate("() => document.getElementById('company_name-0').remove()")
    with pytest.raises(Unsupported, match="unsupported_flow: company_name is not on the page"):
        Hireology().read_form(page)


def test_submit_returns_the_api_answer(page):
    adapter, _ = opened(page)
    assert adapter.submit(page) == "https://api.hireology.com/v2/public/applications 201"


def test_posting_urls():
    adapter = Hireology()
    for url in (
        JOB,
        "https://careers.hireology.com/acme/1234567/description?utm_source=x",
        "https://careers.hireology.com/acme-west/1234567",
    ):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == API
    assert not adapter.detect("https://careers.hireology.com/acme", None)
    assert not adapter.detect("https://www.example.com/acme/1234567/description", None)
