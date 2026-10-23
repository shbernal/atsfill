"""The BambooHR adapter on tests/pages/bamboohr, served as a job page and its JSON answers."""

import json
from pathlib import Path

import pytest

from atsfill.adapters.bamboohr import BambooHR, _form, _from_page, _to_page
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import Answer, FieldType
from tests import testuser

PAGES = Path(__file__).parent / "pages/bamboohr"
JOB = "https://acme.bamboohr.com/careers/1234"
DETAIL = json.loads((PAGES / "detail.json").read_text())
STATES = {
    "1": [("7", "DC", "District of Columbia"), ("19", "MD", "Maryland")],
    "2": [("52", "AB", "Alberta")],
    "3": [("70", "WA", "Western Australia")],
    "222": [("90", "Aberdeen City", "Aberdeen City")],
}
LISTS = {
    c: {"data": [{"id": i, "name": n, "full_name": f} for i, n, f in s]} for c, s in STATES.items()
}


def serve(route) -> None:
    url, method = route.request.url, route.request.method
    if url.endswith("/careers/1234/detail"):
        route.fulfill(json=DETAIL)
    elif "/detail" in url:
        route.fulfill(status=404, json={})
    elif "/ajax/get_states" in url:
        route.fulfill(
            json={"success": True, "data": LISTS.get(url.rsplit("=", 1)[1], {"data": []})["data"]}
        )
    elif "/ajax/files/" in url:
        route.fulfill(json={"fileId": "1-placeholder"})
    elif method == "POST":
        route.fulfill(status=200, json={"success": True})
    elif "/careers/4321" in url:
        route.fulfill(content_type="text/html", body="<h1>This job is no longer available</h1>")
    else:
        route.fulfill(path=PAGES / "job.html")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.bamboohr.FORM_TIMEOUT_MS", 5_000)
        page.context.route("https://acme.bamboohr.com/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    return str(testuser.resume(tmp_path / "CV Ada.pdf"))


def opened(page) -> tuple[BambooHR, list]:
    adapter = BambooHR()
    page.goto(JOB)
    assert adapter.detect(JOB, None)
    return adapter, adapter.read_form(page)


def test_read_form(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "firstName",
        "lastName",
        "email",
        "phone",
        "streetAddress",
        "city",
        "state",
        "zip",
        "countryId",
        "linkedinUrl",
        "dateAvailable",
        "desiredPay",
        "coverLetterFileId",
        "referredBy",
        "websiteUrl",
        "educationLevelId",
        "educationInstitutionName",
        "references",
        "resumeFileId",
        "question_11",
        "question_12",
        "question_13",
        "question_14",
        "question_14_other",
        "question_15",
        "question_16",
        "genderId",
        "veteranStatusId",
    ]
    assert by_id["phone"].type is FieldType.PHONE
    assert by_id["dateAvailable"].type is FieldType.DATE
    # every country's list, the default country's first: the page's list follows the answer
    assert by_id["state"].options == [
        "District of Columbia",
        "Maryland",
        "Alberta",
        "Western Australia",
        "Aberdeen City",
    ]
    assert by_id["countryId"].prefilled
    assert by_id["question_13"].options == ["Yes", "No"]
    assert by_id["question_14"].options == ["She/Her", "They/Them", "Other"]
    assert by_id["question_14_other"].label == (
        "Which pronouns do you use?: please specify"
        " (asked when 'Which pronouns do you use?' is Other)"
    )
    assert by_id["question_15"].required
    assert by_id["question_16"].type is FieldType.FILE
    assert by_id["veteranStatusId"].options == [
        "Decline to Answer",
        "Not a Veteran",
        "Disabled Veteran",
        "Recently Separated Veteran",
    ]


def test_read_api_lists_the_same_fields(page):
    _, fields = opened(page)
    shape = lambda fs: [(f.id, f.label, f.type, f.required, f.options) for f in fs]  # noqa: E731
    assert shape(f for f, _ in _form(DETAIL, LISTS, None)) == shape(fields)


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {
        "firstName": "Ada",
        "lastName": "Placeholder",
        "email": "ada@example.com",
        "phone": "+12025550143",
        "streetAddress": "1 Placeholder St",
        "city": "Washington",
        "state": "Alberta",
        "zip": "20001",
        "countryId": "Canada",
        "linkedinUrl": "https://www.linkedin.com/in/ada-placeholder",
        "dateAvailable": "2026-11-20",
        "desiredPay": "50000",
        "coverLetterFileId": resume,
        "referredBy": "Jane Doe",
        "websiteUrl": "https://example.com",
        "educationLevelId": "College - Bachelor of Science",
        "educationInstitutionName": "Acme University",
        "references": "Jane Doe, Acme",
        "resumeFileId": resume,
        "question_11": "3",
        "question_12": "Placeholder answer.",
        "question_13": "Yes",
        "question_14": "Other",
        "question_14_other": "Ze/Zir",
        "question_15": "Yes",
        "question_16": resume,
        "genderId": "Decline to answer",
        "veteranStatusId": "Recently Separated Veteran",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_a_country_without_a_list_takes_a_typed_state(page):
    adapter, _ = opened(page)
    adapter.fill(page, {"countryId": "Spain", "state": "Madrid"})
    assert adapter.read_back(page)["state"] == "Madrid"


def test_the_test_user_keeps_the_page_country(tmp_path):
    fields = [f for f, _ in _form(DETAIL, LISTS, None)]
    answers = testuser.answers(fields, tmp_path / "cv.pdf")
    assert "countryId" not in answers
    assert answers["state"] == "District of Columbia"
    assert answers["veteranStatusId"] == "Decline to Answer"
    assert "question_14_other" not in answers


def test_page_dates():
    assert _to_page("2026-11-02", "dd mon yyyy") == "02 Nov 2026"
    assert _to_page("2026-11-02", "mm/dd/yyyy") == "11/02/2026"
    assert _from_page("02 Nov 2026", "dd mon yyyy") == "2026-11-02"
    assert _from_page("02/11/2026", "dd/mm/yyyy") == "2026-11-02"
    assert _from_page("2026-11-02", "yyyy-mm-dd") == "2026-11-02"


def test_a_posting_gone(page):
    page.goto("https://acme.bamboohr.com/careers/4321")
    with pytest.raises(Unsupported, match="posting_gone"):
        BambooHR().read_form(page)


def test_an_unknown_question_type_or_field_is_unhandled():
    detail = json.loads(json.dumps(DETAIL))
    detail["result"]["formFields"]["customQuestions"][0]["type"] = "rating"
    detail["result"]["formFields"]["mood"] = {"label": "Mood", "isRequired": False}
    unfillable = {f.id: f.unfillable for f, _ in _form(detail, LISTS, None) if f.unfillable}
    assert unfillable == {
        "question_11": "unhandled_rendering: question 11 is a rating",
        "mood": "unhandled_rendering: mood is a field it does not know",
    }


def test_a_field_missing_from_the_page_is_unhandled(page):
    page.goto(JOB)
    page.click("#apply")
    page.wait_for_selector("form #firstName")
    page.evaluate("() => document.querySelector('[name=references]').remove()")
    fields = {f.id: f for f in BambooHR().read_form(page)}
    assert fields["references"].unfillable == "unhandled_rendering: references is not on the page"


def test_submit_returns_the_post_answer(page):
    adapter, _ = opened(page)
    assert adapter.submit(page) == "https://acme.bamboohr.com/careers/1234/apply 200"


def test_posting_urls():
    adapter = BambooHR()
    for url in (JOB, f"{JOB}?utm_source=x", f"{JOB}/"):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == JOB
    assert not adapter.detect("https://acme.bamboohr.com/careers", None)
    assert not adapter.detect("https://www.example.com/careers/1234", None)
