"""The BambooHR adapter on tests/pages/bamboohr, served as a job page and its JSON answers."""

import json
from pathlib import Path

import pytest

from atsfill.adapters.bamboohr import BambooHR, _form, _from_page, _to_page
from atsfill.core import browser, conditions, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import Answer, FieldType, Is, OptionSet
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
    return adapter, adapter.browser_read(page)


def test_browser_read(page):
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
    # each list under its country the form lists; any other country's state is typed
    assert by_id["state"].options is None
    assert by_id["state"].option_sets == [
        OptionSet(Is("countryId", ["United States"]), ["District of Columbia", "Maryland"]),
        OptionSet(Is("countryId", ["Canada"]), ["Alberta"]),
    ]
    assert by_id["countryId"].default == "United States" and by_id["countryId"].prefilled is None
    assert by_id["question_13"].options == ["Yes", "No"]
    assert by_id["question_14"].options == ["She/Her", "They/Them", "Other"]
    assert by_id["question_14_other"].label == "Which pronouns do you use?: please specify"
    assert by_id["question_14_other"].shown_when == Is("question_14", ["Other"])
    assert by_id["question_15"].required
    assert by_id["question_16"].type is FieldType.FILE
    assert by_id["veteranStatusId"].options == [
        "Decline to Answer",
        "Not a Veteran",
        "Disabled Veteran",
        "Recently Separated Veteran",
    ]


def test_request_read_lists_the_same_fields(page):
    _, fields = opened(page)
    shape = lambda fs: [(f.id, f.label, f.type, f.required, f.options, f.option_sets) for f in fs]  # noqa: E731
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


def unmet(adapter, page, fields, answers) -> list[str]:
    """The fields the filled page shows though the answers keep them hidden."""
    by_id, form = {f.id: f for f in fields}, conditions.Form(fields, answers)
    return [i for i in adapter.shown_fields(page) if not form.shown(by_id[i])]


def test_shown_fields_name_every_control_as_the_read_does(page):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {"question_14": "Other", "veteranStatusId": "Not a Veteran"}
    adapter.fill(page, answers)
    shown = adapter.shown_fields(page)
    assert set(shown) == {f.id for f in fields}
    assert unmet(adapter, page, fields, answers) == []


def test_a_follow_up_the_answers_keep_hidden_stays_hidden(page):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {"question_14": "She/Her"}
    adapter.fill(page, answers)
    assert "question_14_other" not in adapter.shown_fields(page)
    assert unmet(adapter, page, fields, answers) == []


def test_a_follow_up_shown_with_its_condition_unmet_is_caught(page):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {"question_14": "She/Her"}
    adapter.fill(page, answers)
    page.locator("form").first.evaluate(
        """form => {
          const other = document.createElement("input");
          other.name = "customQuestionAnswers.multi_14_other";
          form.append(other);
        }"""
    )
    assert unmet(adapter, page, fields, answers) == ["question_14_other"]


def test_a_control_the_read_did_not_list_is_shown_by_its_name(page):
    adapter, _ = opened(page)
    adapter.fill(page, {"firstName": "Ada"})
    page.locator("form").first.evaluate(
        """form => {
          const added = document.createElement("input");
          added.name = "addedByAnAnswer";
          form.append(added);
        }"""
    )
    assert "addedByAnAnswer" in adapter.shown_fields(page)


def test_a_country_without_a_list_takes_a_typed_state(page):
    adapter, _ = opened(page)
    adapter.fill(page, {"countryId": "Spain", "state": "Madrid"})
    assert adapter.read_back(page)["state"] == "Madrid"


def test_a_state_is_one_of_its_country_list():
    fields = [f for f, _ in _form(DETAIL, LISTS, None)]

    def state(country: str, state: str) -> list[str]:
        given = {"countryId": country, "state": state}
        return [p for p in matching.problems(fields, given) if p.startswith("state:")]

    assert state("Canada", "Alberta") == []
    assert state("United States", "Alberta") == [
        "state: 'Alberta' is not one of ['District of Columbia', 'Maryland'], "
        'the options when "Country" is "United States"'
    ]
    assert state("Spain", "Madrid") == []  # typed, left to the read-back


def test_the_test_user_keeps_the_page_country(tmp_path):
    fields = [f for f, _ in _form(DETAIL, LISTS, None)]
    answers = testuser.answers(fields, tmp_path / "cv.pdf")
    assert answers["countryId"] == "United States"  # the page's, which the state list follows
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
        BambooHR().browser_read(page)


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
    fields = {f.id: f for f in BambooHR().browser_read(page)}
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
