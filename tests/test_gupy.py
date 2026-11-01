"""The Gupy adapter on tests/pages/gupy: the register step's screens, an Acme posting's."""

from dataclasses import replace
from pathlib import Path

import pytest

from atsfill.adapters import gupy
from atsfill.adapters.gupy import Gupy
from atsfill.core import browser, conditions, matching
from atsfill.core.adapter import NotTaken, Unsupported
from atsfill.core.browser import BrowserError
from atsfill.core.results import Field, FieldType, Is

PAGES = Path(__file__).parent / "pages/gupy"
JOB = "https://acme.gupy.io/jobs/12345?jobBoardSource=gupy_public_page"
REFERRER = "guiddedApplicationAdditionalDataIndicatedByNameInput"
REFERRER_EMAIL = "guiddedApplicationAdditionalDataIndicatedByEmailInput"
CORPORATE_EMAIL = "guiddedApplicationAdditionalDataCorporateEmailInput"
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
        REFERRER,
        REFERRER_EMAIL,
        "radioGroupIsCompanyEmployeeTitle",
        CORPORATE_EMAIL,
        "howDidYouHearAboutUs",
        "question-101",
        "question-102",
        "question-103",
        "question-104",
        "question-105",
        "question-106",
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
    assert by_id[REFERRER].shown_when == Is("radioGroupIsIndicatedTitle", ["Yes"])
    assert by_id[REFERRER].required  # whenever shown
    assert by_id[CORPORATE_EMAIL].shown_when == Is("radioGroupIsCompanyEmployeeTitle", ["Yes"])
    assert by_id["question-105"].type is FieldType.CHOICE and not by_id["question-105"].unfillable
    assert by_id["question-106"].shown_when == Is("question-105", ["Yes"])


def test_every_answer_reads_back_and_submit_saves_the_answers(opened, page):
    adapter, fields = opened
    answers = {
        "radioGroupIsIndicatedTitle": "No",
        "radioGroupIsCompanyEmployeeTitle": "Yes",
        CORPORATE_EMAIL: "jane.doe@acme.example",
        "question-101": "5000",
        "question-102": "Washington",
        "question-103": "Yes",
        "question-104": ["Spreadsheets", "Databases"],
        "question-105": "Yes",
        "question-106": "Two years",
    }
    adapter.fill(page, answers)
    assert saved == ["PUT additional-info"]  # the questions are left for submit
    read_back = adapter.read_back(page)
    checks = matching.check(fields, answers, read_back)
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    answered = "PUT /question-forms/candidates/applications/11/steps/22/answers 200"
    assert adapter.submit(page) == answered


QUESTIONS = {
    "question-101": "5000",
    "question-102": "Washington",
    "question-103": "Yes",
    "question-104": ["Spreadsheets"],
}


def unmet(adapter, page, fields, answers) -> list[str]:
    """The fields the filled page shows though the answers keep them hidden."""
    by_id, form = {f.id: f for f in fields}, conditions.Form(fields, answers)
    return [i for i in adapter.shown_fields(page) if not form.shown(by_id[i])]


def test_shown_fields_follow_the_answers(opened, page):
    adapter, fields = opened
    answers = {
        "radioGroupIsIndicatedTitle": "Yes",
        REFERRER: "John Doe",
        REFERRER_EMAIL: "john.doe@acme.example",
        **QUESTIONS,
        "question-105": "Yes",
        "question-106": "Two years",
    }
    adapter.fill(page, answers)
    shown = adapter.shown_fields(page)
    assert {REFERRER, REFERRER_EMAIL, "question-104", "question-106"} <= set(shown)
    assert CORPORATE_EMAIL not in shown and not set(shown) - {f.id for f in fields}
    assert unmet(adapter, page, fields, answers) == []
    read_back = adapter.read_back(page)
    assert read_back[REFERRER] == "John Doe" and read_back["question-106"] == "Two years"


def test_a_follow_up_the_answers_keep_hidden_stays_hidden(opened, page):
    adapter, fields = opened
    answers = {**QUESTIONS, "question-105": "No"}
    adapter.fill(page, answers)
    shown = adapter.shown_fields(page)
    assert "question-105" in shown and "question-106" not in shown
    assert REFERRER not in shown and CORPORATE_EMAIL not in shown
    assert unmet(adapter, page, fields, answers) == []


def test_a_follow_up_shown_with_its_condition_unmet_is_caught(opened, page):
    adapter, fields = opened
    answers = {**QUESTIONS, "question-105": "No"}
    adapter.fill(page, answers)
    page.evaluate("""document.getElementById("q-106").innerHTML =
        '<textarea name="Tell us more"></textarea>'""")
    assert unmet(adapter, page, fields, answers) == ["question-106"]


def test_a_control_no_read_listed_is_shown_as_the_page_names_it(opened, page):
    adapter, _ = opened
    adapter.fill(page, {**QUESTIONS, "question-105": "No"})
    page.evaluate("""document.getElementById("q-101").innerHTML +=
        '<textarea name="Not in the form"></textarea>'""")
    assert "Not in the form" in adapter.shown_fields(page)


def test_shown_fields_tell_questions_sharing_a_title_apart(page):
    adapter = Gupy()
    adapter._questions = {
        "question-1": {"title": "Comment"},
        "question-2": {"title": "Comment"},
    }
    adapter._given = {"question-2"}
    page.set_content('<textarea name="Comment"></textarea>')
    assert adapter.shown_fields(page) == ["question-2"]


def test_questions_sharing_a_title_each_get_their_own_control(page):
    adapter = Gupy()
    field = Field("question-1", "Comment", FieldType.TEXT, False)
    adapter._questions = {
        "question-1": {"questionId": 1, "title": "Comment"},
        "question-2": {"questionId": 2, "title": "Comment"},
    }
    adapter._fields = {"question-1": field, "question-2": replace(field, id="question-2")}
    adapter._given = set(adapter._questions)
    page.set_content('<textarea name="Comment"></textarea><textarea name="Comment"></textarea>')
    for field_id, value in (("question-1", "first"), ("question-2", "second")):
        gupy._answer(
            page,
            adapter._fields[field_id],
            adapter._questions[field_id],
            value,
            adapter._nth(field_id),
        )
    values = page.evaluate(gupy._READ_QUESTIONS, [[1, "Comment", 0], [2, "Comment", 1]])
    assert values == {"question-1": "first", "question-2": "second"}


def test_a_saving_the_page_refuses_is_not_taken(opened, page, monkeypatch):
    monkeypatch.setattr(gupy, "SAVE_TIMEOUT_MS", 1000)
    adapter, _ = opened
    with pytest.raises(NotTaken, match="would not save Additional information"):
        adapter.fill(
            page,
            {"radioGroupIsCompanyEmployeeTitle": "Yes", CORPORATE_EMAIL: "not an email"},
        )


def test_an_incomplete_resume_is_the_known_gap(page, monkeypatch):
    monkeypatch.setattr(gupy, "RESUME_WAIT_S", 1)
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


def test_a_page_rendering_again_does_not_lose_a_click(page, monkeypatch):
    detached = [BrowserError("Element is not attached to the DOM")]
    clicks = []

    def click(button):
        if detached:
            raise detached.pop()
        clicks.append(button)

    monkeypatch.setattr(gupy.browser, "click", click)
    button = page.locator("body")
    gupy._click(lambda: button)
    assert clicks == [button]


def test_detection_and_posting():
    adapter = Gupy()
    assert adapter.detect(JOB, None)
    assert adapter.detect("https://acme.gupy.io/candidates/jobs/12345/apply", None)
    assert not adapter.detect("https://acme.gupy.io/", None)
    assert not adapter.detect("https://acme.example.com/jobs/12345", None)
    assert adapter.posting(JOB) == "https://acme.gupy.io/jobs/12345"
    assert adapter.account_scope(JOB) is None
