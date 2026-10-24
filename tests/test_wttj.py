"""The Welcome to the Jungle adapter on tests/pages/wttj, served as a signed-in job page."""

from pathlib import Path

import pytest

from atsfill.adapters import wttj
from atsfill.adapters.wttj import WelcomeToTheJungle
from atsfill.core import browser, matching

PAGE = Path(__file__).parent / "pages/wttj/job.html"
JOB = "https://www.welcometothejungle.com/en/companies/acme/jobs/ai-engineer_paris"


def serve(route) -> None:
    if route.request.url.endswith("/api/v1/applications"):
        route.fulfill(status=201, json={"application": {"reference": "REF-1"}})
    else:
        route.fulfill(path=PAGE)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://www.welcometothejungle.com/**", serve)
        page.context.route("https://api.welcometothejungle.com/**", serve)
        yield page


@pytest.fixture
def opened(page) -> tuple[WelcomeToTheJungle, list]:
    adapter = WelcomeToTheJungle()
    page.goto(JOB)
    return adapter, adapter.read_form(page)


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def test_read_form_waits_for_the_account(opened):
    _, fields = opened
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "firstname",
        "phone",
        "location",
        "resume",
        "portfolio",
        "questions.ab12",
        "consent",
    ]  # the account's email is not the form's to change
    assert by_id["questions.ab12"].label == "Why this role?"
    assert by_id["resume"].required and by_id["resume"].accept == ["application/pdf"]
    prefilled = [f.id for f in fields if f.prefilled]
    assert prefilled == ["firstname", "phone", "location", "resume"]  # from the account profile


def test_every_answer_reads_back(opened, page, resume, monkeypatch):
    monkeypatch.setattr(wttj, "SUGGEST_TIMEOUT_S", 0.5)  # the location is typed twice
    adapter, fields = opened
    answers = {
        "firstname": "Ada",
        "location": "Paris, France",
        "resume": resume,
        "portfolio": resume,
        "questions.ab12": "Because.",
        "consent": "Yes",
    }
    adapter.fill(page, answers)
    read_back = adapter.read_back(page)
    checks = matching.check(fields, answers, read_back)
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert read_back["phone"] == "+33 6 00 00 00 00"  # unanswered, left as the account has it


def test_submit_returns_the_reference(opened, page):
    adapter, _ = opened
    assert adapter.submit(page) == "POST /api/v1/applications 201, reference REF-1"


def test_detection_and_posting():
    adapter = WelcomeToTheJungle()
    assert adapter.detect(JOB + "?fromApply=true", None)
    assert adapter.detect("https://easyapply.jobs/r/abc", None)
    assert not adapter.detect("https://www.welcometothejungle.com/en/companies/acme", None)
    expected = "https://www.welcometothejungle.com/companies/acme/jobs/ai-engineer_paris"
    assert adapter.posting(JOB + "?igbTracker=1") == expected


def test_an_unknown_input_is_listed_unhandled():
    raw = {"id": "questions.cd34", "type": "radio", "label": "Remote?", "required": True}
    field = wttj._field(raw)
    assert field and field.unfillable == "unhandled_rendering: 'Remote?' is a radio input"
    assert wttj._field({**raw, "type": "hidden"}) is None
