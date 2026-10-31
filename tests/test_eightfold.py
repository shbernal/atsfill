"""The Eightfold adapter on tests/pages/eightfold, served as a career site and its APIs."""

import json
from pathlib import Path

import pytest

from atsfill.adapters.eightfold import Eightfold, _form
from atsfill.core import browser, conditions, matching
from atsfill.core.adapter import Blocked, Unsupported
from atsfill.core.results import Answer, FieldType, Is
from tests import testuser

PAGES = Path(__file__).parent / "pages/eightfold"
SITE = "https://acme.eightfold.ai"
JOB = f"{SITE}/careers/job/1234567?utm_source=x"
QUESTIONS = json.loads((PAGES / "questions.json").read_text())
JOB_PAGE = '<meta name="_csrf" content="t"><script>window._EF_GROUP_ID = "acme.com";</script>'
ACTIONS = {
    "1234567": {"status": "allowed"},
    "7654321": {"status": "link_off", "applyUrl": "https://acme.wd1.myworkdayjobs.com/job/1"},
    "7777777": {"status": "log_in"},
}


def serve(route) -> None:
    url, method = route.request.url, route.request.method
    if "/api/pcsx/position_details" in url:
        pid = url.split("position_id=")[1].split("&")[0]
        actions = {"applyAction": ACTIONS[pid]}
        route.fulfill(json={"status": 200, "data": {"id": pid, "positionUserActions": actions}})
    elif "/api/application/v2/questions" in url and method == "POST":
        assert route.request.headers.get("x-csrf-token")
        route.fulfill(json=QUESTIONS)
    elif "/api/application/v2/" in url and method == "POST":
        route.fulfill(json={"status": 200, "data": {}})
    elif "/careers/apply" in url:
        route.fulfill(path=PAGES / "apply.html")
    else:
        route.fulfill(content_type="text/html", body=JOB_PAGE)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.eightfold.FORM_TIMEOUT_MS", 5_000)
        mp.setattr("atsfill.adapters.eightfold.PARSE_SETTLE_MS", 100)
        page.context.route(f"{SITE}/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    return str(testuser.resume(tmp_path / "CV Ada.pdf"))


def opened(page, url: str = JOB) -> tuple[Eightfold, list]:
    adapter = Eightfold()
    page.goto(url)
    assert adapter.detect(url, page)
    return adapter, adapter.browser_read(page)


def test_browser_read(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    signature = "I accept the <a href=https://example.com>privacy notice</a>"
    assert list(by_id) == [
        "position_location_1234567",
        "resume",
        "firstname",
        "email",
        "phone",
        "country",
        "state",
        "Q_HEAR_ABOUT_US",
        "Q_EVENTS",
        "app_Summary",
        "1443109865728",
        "636668-1",
        "q_relocate",
        "q_todayDate",
        "coverletter",
        signature,
        "q_signature",
    ]
    assert by_id["position_location_1234567"].type is FieldType.MULTI_CHOICE
    location = by_id["position_location_1234567"]
    assert location.default == ["Springfield,United States"] and location.prefilled is None
    assert [f.id for f in fields if f.default] == ["position_location_1234567"]
    assert by_id["resume"].accept == [".pdf", ".doc", ".docx"]
    assert by_id["phone"].type is FieldType.PHONE
    assert by_id["country"].options == ["Canada", "United States"]
    assert by_id["state"].type is FieldType.TYPEAHEAD
    assert by_id["state"].required
    assert by_id["Q_EVENTS"].label == "Which event?"
    assert by_id["Q_EVENTS"].shown_when == Is("Q_HEAR_ABOUT_US", ["Events"])
    assert by_id["Q_EVENTS"].required  # once shown
    assert by_id["app_Summary"].label == "Tell us about yourself"
    assert by_id["1443109865728"].label == "What's your overall experience? (a number)"
    assert by_id["636668-1"].options == ["Yes", "No"]
    assert by_id["q_todayDate"].type is FieldType.DATE
    assert by_id[signature].type is FieldType.MULTI_CHOICE
    assert by_id[signature].label == "I accept the privacy notice"
    assert by_id["q_signature"].unfillable == (
        "unhandled_rendering: q_signature is a question of type signature"
    )
    assert not any(f.unfillable for f in fields if f.id != "q_signature")


def test_request_read_lists_the_same_fields(page):
    _, fields = opened(page)
    shape = lambda fs: [(f.id, f.label, f.type, f.required, f.options) for f in fs]  # noqa: E731
    assert shape(c.field for c in _form(QUESTIONS["data"])) == shape(fields)


def test_every_answer_reads_back(page, resume, tmp_path):
    adapter, fields = opened(page)
    cover = testuser.resume(tmp_path / "letter.pdf")
    answers: dict[str, Answer] = {
        "position_location_1234567": ["Shelbyville,United States"],
        "resume": resume,
        "firstname": "Ada",
        "email": "ada@example.com",
        "phone": "+442025550143",
        "country": "United States",
        "state": "District of Columbia",
        "Q_HEAR_ABOUT_US": "Events",
        "Q_EVENTS": "Career Fair",
        "app_Summary": "Placeholder answer.",
        "1443109865728": "3",
        "636668-1": "No",
        "q_relocate": "Yes",
        "q_todayDate": "2027-02-03",
        "coverletter": str(cover),
        "I accept the <a href=https://example.com>privacy notice</a>": ["I accept"],
    }
    adapter.fill(page, answers)
    read_back = adapter.read_back(page)
    checks = matching.check(fields, answers, read_back)
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)
    assert read_back["phone"] == "+44 2025550143"  # the United Kingdom, not Guernsey


def test_the_country_code_shared_by_several_countries_is_its_main_one(page):
    adapter, _ = opened(page)
    adapter.fill(page, {"phone": "+12025550143"})
    assert page.locator("[aria-label='Country code']").input_value() == (
        "🇺🇸 (+1) United States of America"
    )
    assert adapter.read_back(page)["phone"] == "+1 2025550143"


def test_a_question_its_answer_hides_is_not_filled(page):
    adapter, _ = opened(page)
    adapter.fill(page, {"Q_HEAR_ABOUT_US": "Job Board", "Q_EVENTS": "Conference"})
    read_back = adapter.read_back(page)
    assert read_back["Q_HEAR_ABOUT_US"] == "Job Board"
    assert "Q_EVENTS" not in read_back


def unmet(adapter, page, fields, answers) -> list[str]:
    """The fields the filled page shows though the answers keep them hidden."""
    by_id, form = {f.id: f for f in fields}, conditions.Form(fields, answers)
    return [i for i in adapter.shown_fields(page) if not form.shown(by_id[i])]


def test_shown_fields_name_each_control_as_the_read_does(page):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {"country": "United States", "Q_HEAR_ABOUT_US": "Events"}
    adapter.fill(page, answers)
    shown = adapter.shown_fields(page)
    assert "Q_EVENTS" in shown and "1443109865728" in shown
    assert not set(shown) - {f.id for f in fields}
    assert unmet(adapter, page, fields, answers) == []


def test_a_follow_up_the_answers_keep_hidden_stays_hidden(page):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {"country": "United States", "Q_HEAR_ABOUT_US": "Job Board"}
    adapter.fill(page, answers)
    assert "Q_EVENTS" not in adapter.shown_fields(page)
    assert unmet(adapter, page, fields, answers) == []


def test_a_follow_up_shown_with_its_condition_unmet_is_caught(page):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {"country": "United States", "Q_HEAR_ABOUT_US": "Job Board"}
    adapter.fill(page, answers)
    page.evaluate("""document.getElementById("events").innerHTML =
        document.getElementById("events-field").innerHTML""")
    assert unmet(adapter, page, fields, answers) == ["Q_EVENTS"]


def test_a_control_no_question_ends_as_is_shown_by_its_element_id(page):
    adapter, _ = opened(page)
    adapter.fill(page, {"firstname": "Ada"})
    page.evaluate("""document.getElementById("events").innerHTML =
        '<div id="Application_questions_q_unlisted_error"></div>'""")
    assert "Application_questions_q_unlisted" in adapter.shown_fields(page)


def test_a_control_is_the_question_with_the_longest_id_it_ends_with(page):
    adapter = Eightfold()
    adapter._ends = {"town": "town", "q_town": "q_town"}
    page.set_content('<div id="Contact_q_town_error"></div>')
    assert adapter.shown_fields(page) == ["q_town"]


def test_the_test_user_leaves_out_what_its_answers_hide(tmp_path):
    fields = [c.field for c in _form(QUESTIONS["data"])]
    answers = testuser.answers(fields, tmp_path / "cv.pdf")
    assert answers["Q_HEAR_ABOUT_US"] == "Events"
    assert answers["Q_EVENTS"] == "Conference"
    assert answers["state"] == testuser.REGION


def test_a_question_rendered_by_a_hidden_one_is_left_out(tmp_path):
    def question(qid, label, parent=None, value=None):
        q = {"questionId": qid, "type": "radio", "label": label, "required": True}
        q["radioOptions"] = {
            "choices": [{"label": "Yes", "value": "y"}, {"label": "No", "value": "n"}]
        }
        if parent:
            q["parentDependencies"] = [{"questionId": parent, "values": [value]}]
        return q

    data = {"fields": [{"questions": [
        question("country_us", "Do you live in the US?"),
        question("name_us", "I have a preferred name", "country_us", "n"),
        question("name_fr", "I have a preferred name", "country_us", "y"),
        question("local_name", "Local name", "name_us", "y"),
    ]}]}  # fmt: skip
    fields = {f.field.id: f.field for f in _form(data)}
    assert fields["local_name"].shown_when == Is("name_us", ["Yes"])
    answers = testuser.answers(list(fields.values()), tmp_path / "cv.pdf")
    assert answers == {"country_us": "Yes", "name_fr": "No"}


def test_an_application_on_another_system(page):
    page.goto(f"{SITE}/careers/job/7654321")
    with pytest.raises(Unsupported, match="external_step: .* acme.wd1.myworkdayjobs.com"):
        Eightfold().browser_read(page)


def test_a_site_that_asks_to_sign_in(page):
    page.goto(f"{SITE}/careers/job/7777777")
    with pytest.raises(Blocked, match="login_required"):
        Eightfold().browser_read(page)


def test_submit_returns_the_api_answer(page):
    adapter, _ = opened(page)
    assert adapter.submit(page) == f"{SITE}/api/application/v2/apply 200"


def test_posting_urls():
    adapter = Eightfold()
    key = f"{SITE}/careers/job/1234567"
    for url in (
        JOB,
        f"{SITE}/careers/job/1234567-placeholder-role?domain=acme.com",
        f"{SITE}/careers/apply?pid=1234567&domain=acme.com",
    ):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == key
    assert adapter.detect("https://jobs.example.com/careers/job/1234567", None)
    assert not adapter.detect(f"{SITE}/careers", None)
    assert not adapter.detect("https://jobs.example.com/careers/jobs/search", None)
