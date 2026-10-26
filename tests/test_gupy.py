"""The Gupy adapter on tests/pages/gupy: the register step's screens, an Acme posting's."""

from pathlib import Path

import pytest

from atsfill.adapters import gupy
from atsfill.adapters.gupy import Gupy
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import FieldType

PAGES = Path(__file__).parent / "pages/gupy"
JOB = "https://acme.gupy.io/jobs/12345?jobBoardSource=gupy_public_page"
saved: list[str] = []
incomplete: list[bool] = []  # the next apply page's resume is incomplete


def serve(route) -> None:
    request = route.request
    path = request.url.split("?")[0]
    if path.endswith("/forms"):
        route.fulfill(path=PAGES / "forms.json", headers={"access-control-allow-origin": "*"})
    elif "private-api" in path:
        saved.append(f"{request.method} {path.rsplit('/', 1)[-1]}")
        route.fulfill(json={}, headers={"access-control-allow-origin": "*"})
    elif "/candidates/jobs/" in path:
        html = (PAGES / "apply.html").read_text()
        if incomplete:
            html = html.replace("const incomplete = false", "const incomplete = true")
        route.fulfill(body=html, content_type="text/html")
    else:
        route.fulfill(body="<!doctype html><title>Acme</title>", content_type="text/html")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://*.gupy.io/**", serve)
        yield page


@pytest.fixture
def opened(page) -> tuple[Gupy, list]:
    saved.clear()
    adapter = Gupy()
    page.goto(JOB)
    return adapter, adapter.browser_read(page)


def test_browser_read_lists_additional_information_and_the_questions(opened):
    _, fields = opened
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "radioGroupIsIndicatedTitle",
        "radioGroupIsCompanyEmployeeTitle",
        "howDidYouHearAboutUs",
        "question-101",
        "question-102",
        "question-103",
        "question-104",
    ]
    indicated = by_id["radioGroupIsIndicatedTitle"]
    assert indicated.label.startswith("Someone who works at this company referred you")
    assert (indicated.type, indicated.options, indicated.prefilled) == (
        FieldType.CHOICE,
        ["Yes", "No"],
        "No",
    )
    source = by_id["howDidYouHearAboutUs"]
    assert not source.required and source.unfillable
    assert [by_id[f"question-{n}"].type for n in (101, 102, 103, 104)] == [
        FieldType.TEXT,
        FieldType.TEXT,
        FieldType.CHOICE,
        FieldType.MULTI_CHOICE,
    ]
    assert by_id["question-104"].options == ["Spreadsheets", "Databases", "None of these"]
    assert saved == []  # reading saves nothing


def test_every_answer_reads_back_and_submit_saves_the_answers(opened, page):
    adapter, fields = opened
    answers = {
        "radioGroupIsIndicatedTitle": "No",
        "radioGroupIsCompanyEmployeeTitle": "Yes",
        "question-101": "5000",
        "question-102": "Washington",
        "question-103": "Yes",
        "question-104": ["Spreadsheets", "Databases"],
    }
    adapter.fill(page, answers)
    assert saved == ["PUT additional-info"]  # the questions are left for submit
    read_back = adapter.read_back(page)
    checks = matching.check(fields, answers, read_back)
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    answered = "PUT /question-forms/candidates/applications/11/steps/22/answers 200"
    assert adapter.submit(page) == answered


def test_an_incomplete_resume_is_the_known_gap(page):
    page.goto(JOB)
    incomplete.append(True)
    try:
        with pytest.raises(Unsupported, match="^unsupported_flow: resume: "):
            Gupy().browser_read(page)
    finally:
        incomplete.clear()


def test_an_unknown_question_type_is_listed_unhandled():
    field = gupy._question(
        {"questionId": 7, "title": "Portfolio", "type": "FILE", "required": False, "options": []}
    )
    assert field.unfillable == "unhandled_rendering: 'Portfolio' is a FILE question"


def test_detection_and_posting():
    adapter = Gupy()
    assert adapter.detect(JOB, None)
    assert adapter.detect("https://acme.gupy.io/candidates/jobs/12345/apply", None)
    assert not adapter.detect("https://acme.gupy.io/", None)
    assert not adapter.detect("https://acme.example.com/jobs/12345", None)
    assert adapter.posting(JOB) == "https://acme.gupy.io/jobs/12345"
    assert adapter.account_scope(JOB) is None
