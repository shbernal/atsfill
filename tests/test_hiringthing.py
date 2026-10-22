"""The HiringThing adapter on tests/pages/hiringthing, served as a career site's job page."""

from pathlib import Path

import pytest

from atsfill.adapters.hiringthing import HiringThing
from atsfill.core import browser, matching
from atsfill.core.adapter import NotTaken, Unsupported
from atsfill.core.results import Answer, FieldType
from tests import persona

PAGES = Path(__file__).parent / "pages/hiringthing"
HOST = "https://acme.hiringthing.com"
JOB = f"{HOST}/job/1234567/acme-role"
REGIONS = ["Alabama", "District of Columbia", "Virginia", "West Virginia"]
uploads: list[str] = []


def serve(route) -> None:
    url, method = route.request.url, route.request.method
    if method == "POST" and url.endswith("/client_api/add_file"):
        uploads.append(url)
        route.fulfill(status=200, json={"id": 1})
    elif method == "POST":
        route.fulfill(status=200, json={"success": True})
    elif "/regions" in url:
        route.fulfill(json=REGIONS)
    elif "/7654321" in url:
        route.fulfill(status=404, content_type="text/html", body="<h1>Not found</h1>")
    else:
        route.fulfill(path=PAGES / "job.html")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.hiringthing.PAGE_TIMEOUT_MS", 3_000)
        mp.setattr("atsfill.adapters.hiringthing.MENU_TIMEOUT_MS", 1_000)
        page.context.route(f"{HOST}/**", serve)
        yield page


def opened(page, url: str = JOB) -> tuple[HiringThing, list]:
    adapter = HiringThing()
    page.goto(url)
    assert adapter.detect(url, None)
    return adapter, adapter.read_form(page)


def test_read_form(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "user.first_name",
        "user.email",
        "user.phone",
        "user.state",
        "user.country",
        "user.contact_preference",
        "user.linkedin_url",
        "job_assessment.question_1001.response",
        "job_assessment.question_1002.response",
        "job_assessment.question_1003.response",
        "job_assessment.question_1004.response",
        "job_assessment.question_1005.response.0.name",
        "job_assessment.question_1005.response.0.phone",
        "job_assessment.question_1005.response.0.st_date",
        "disclosure_agree",
        "subscribe",
        "files.Resume",
        "files.Other",
    ]
    assert not any(f.unfillable for f in fields)
    assert by_id["user.first_name"].label == "First Name"
    assert by_id["user.first_name"].required
    assert not by_id["user.linkedin_url"].required
    assert by_id["user.phone"].type is FieldType.PHONE
    assert by_id["user.state"].type is FieldType.TYPEAHEAD  # once the state list loaded
    assert by_id["user.state"].required
    country = by_id["user.country"]
    assert country.type is FieldType.CHOICE
    assert country.options == ["Canada", "United States", "United States Minor Outlying Islands"]
    assert by_id["user.contact_preference"].options == ["Yes", "No"]
    assert not by_id["user.contact_preference"].required
    assert by_id["disclosure_agree"].required
    assert by_id["job_assessment.question_1001.response"].type is FieldType.DATE
    assert by_id["job_assessment.question_1003.response"].options == ["Yes", "No"]
    days = by_id["job_assessment.question_1004.response"]
    assert days.type is FieldType.MULTI_CHOICE
    assert days.options == ["Monday", "Tuesday", "None of these", "No"]
    entry = by_id["job_assessment.question_1005.response.0.st_date"]
    assert entry.label == "Please enter your employment history: Start"
    assert entry.type is FieldType.DATE
    assert by_id["files.Resume"].type is FieldType.FILE
    assert by_id["files.Resume"].label == "Resume: (Word/PDF)"


def test_every_answer_reads_back(page, tmp_path):
    adapter, fields = opened(page)
    cv = str(persona.resume(tmp_path / "CV Ada.pdf"))
    answers: dict[str, Answer] = {
        "user.first_name": "Ada",
        "user.email": "ada@example.com",
        "user.phone": "+12025550143",
        "user.state": "District",
        "user.country": "United States",
        "user.contact_preference": "No",
        "user.linkedin_url": "https://example.com",
        "job_assessment.question_1001.response": "2026-12-01",
        "job_assessment.question_1002.response": "Placeholder",
        "job_assessment.question_1003.response": "Yes",
        "job_assessment.question_1004.response": ["Tuesday", "No"],
        "job_assessment.question_1005.response.0.name": "Acme",
        "job_assessment.question_1005.response.0.phone": "+12025550143",
        "job_assessment.question_1005.response.0.st_date": "2020-01-31",
        "disclosure_agree": "Yes",
        "subscribe": "No",
        "files.Resume": cv,
        "files.Other": cv,
    }
    uploads.clear()
    adapter.fill(page, answers)
    assert len(uploads) == 2  # each file is uploaded once attached
    date = page.locator("[name='job_assessment.question_1001.response']")
    assert date.input_value() == "12/01/2026"
    read_back = adapter.read_back(page)
    assert read_back["user.state"] == "District of Columbia"
    assert read_back["job_assessment.question_1004.response"] == ["Tuesday", "No"]
    checks = matching.check(fields, answers, read_back)
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_the_persona_answers_every_field(page, tmp_path):
    adapter, fields = opened(page)
    answers = persona.answers(fields, persona.resume(tmp_path / "cv.pdf"))
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []


def test_an_option_the_list_does_not_hold(page):
    adapter, _ = opened(page)
    with pytest.raises(NotTaken, match="Atlantis"):
        adapter.fill(page, {"user.country": "Atlantis"})


def test_an_unknown_control_is_unhandled(page):
    adapter, _ = opened(page)
    page.evaluate("() => document.getElementById('user.email').setAttribute('type', 'number')")
    fields = {f.id: f for f in adapter.read_form(page)}
    assert fields["user.email"].unfillable == "unhandled_rendering: user.email is a number"


def test_a_posting_gone(page):
    with pytest.raises(Unsupported, match="posting_gone"):
        opened(page, f"{HOST}/job/7654321/gone")


def test_submit_returns_the_answer(page):
    adapter, _ = opened(page)
    assert adapter.submit(page) == f"{HOST}/client_api/apply 200"


def test_posting_urls():
    adapter = HiringThing()
    for url in (
        JOB,
        f"{JOB}?utm_source=x",
        f"{HOST}/job/1234567",
    ):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == f"{HOST}/job/1234567"
    assert adapter.detect("https://acme.oasisrecruit.com/job/1234567/role", None)
    assert not adapter.detect(f"{HOST}/", None)
    assert not adapter.detect("https://www.example.com/job/1234567/role", None)
