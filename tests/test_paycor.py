"""The Paycor adapter on tests/pages/paycor, served as recruitingbypaycor.com: the
self-identification pages, the qualifying questions and the form, each posting to the next."""

import json
import re
from html import escape
from pathlib import Path
from urllib.parse import parse_qsl

import pytest

from atsfill.adapters.paycor import Paycor, _field
from atsfill.core import browser, matching
from atsfill.core.adapter import NotTaken, Unsupported
from atsfill.core.results import FieldType
from tests import testuser

PAGES = Path(__file__).parent / "pages/paycor"
JOB = "https://recruitingbypaycor.com/career/JobIntroduction.action?clientId=c1&id=j1&source=x"
CARRIED = ["gender", "race", "veteran", "q1", "q2"]
submitted: list[str] = []


def _eeo(page: str, action: str, posted: dict[str, str]) -> str:
    carried = "".join(
        f'<input type="hidden" name="{k}" value="{escape(v)}" />' for k, v in posted.items()
    )
    html = (PAGES / "eeo.html").read_text()
    return html.replace("{page}", page).replace("{action}", action).replace("{carried}", carried)


def serve(route, posted: dict[str, str], gone: bool) -> None:
    request, url = route.request, route.request.url
    if request.method == "POST" and "resume-fields" in url:
        name = re.search(rb'filename="([^"]+)"', request.post_data_buffer or b"")
        fields = [{"id": "firstName", "value": "Parsed"}]
        route.fulfill(json={"originalFileName": name and name[1].decode(), "fields": fields})
        return
    if request.method == "POST":
        posted.update((k, v) for k, v in parse_qsl(request.post_data or "") if k in CARRIED and v)
    html = None
    if gone:
        html = "<p>Sorry, this job is no longer active.</p>"
    elif "ApplyJob.action" in url:
        submitted.append(url)
        html = "<p>Thank you for applying.</p>"
    elif request.method == "GET":
        posted.clear()
        html = _eeo("intro", "ShowVevraaForm.action?step=gender", posted)
    elif "ShowVevraaForm" in url:
        html = _eeo("gender", "SubmitVevraaForm.action?step=veteran", posted)
    elif "SubmitVevraaForm" in url:
        html = _eeo("veteran", "SubmitResume.action?step=questions", posted)
    elif "SubmitResume" in url:
        html = (PAGES / "questions.html").read_text()
    elif "SubmitQuestionnaire" in url:
        html = (PAGES / "apply.html").read_text()
        for key in CARRIED:
            html = html.replace("{" + key + "}", posted.get(key, ""))
    route.fulfill(content_type="text/html", body=html or "")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        yield page


def opened(page, gone: bool = False) -> tuple[Paycor, list]:
    posted: dict[str, str] = {}
    page.context.unroute("https://recruitingbypaycor.com/**")
    page.context.route("https://recruitingbypaycor.com/**", lambda r: serve(r, posted, gone))
    adapter = Paycor()
    assert adapter.detect(JOB, None)
    page.goto(JOB)
    return adapter, adapter.browser_read(page)


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def test_detection_and_posting():
    adapter = Paycor()
    apply = "https://recruitingbypaycor.com/career/SubmitResume.action?clientId=c1&id=j1"
    framed = "https://www.example.com/careers/?gnk=job&gni=j1"
    assert adapter.posting(apply) == adapter.posting(JOB) == adapter.posting(framed)
    assert not adapter.detect("https://recruitingbypaycor.com/career/CareerHome.action", None)
    assert not adapter.detect(framed, None)


def test_browser_read_lists_each_rendering(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "eeo.gender",
        "eeo.race",
        "eeo.veteran",
        "question.q1",
        "question.q2",
        "resume",
        "firstName",
        "lastName",
        "email",
        "attachmentB",
        "mobile",
        "address1",
        "state",
        "zipCode",
        "gnewton_section_50_question_10_answer_date",
        *(f"gnewton_section_50_question_{n}_answer" for n in (20, 30, 40, 50, 60)),
        "gnewton_section_50_question_70_answer_email",
        "gnewton_section_60_question_10_answer",
        "gnewton_section_60_question_20_answer_country",
        "gnewton_section_60_question_30_answer_state",
        "gnewton_section_60_question_40_answer_date",
        "gnewton_section_60_question_50_answer",
        "gnewton_section_70_question_20_answer",
        "originalComments",
        "candidateTextMessagingOptIn",
        "veteran",
        "ccpaDisclaimerAgree",
    ]
    assert by_id["eeo.race"].options == ["Hispanic or Latino", "I decline to identify"]
    assert by_id["question.q2"].label == "Do you have a valid driver's license?"
    assert by_id["question.q2"].options == ["Yes", "No"]
    assert by_id["resume"].type is FieldType.FILE and by_id["resume"].required
    assert by_id["attachmentB"].label == "Cover Letter" and not by_id["attachmentB"].required
    assert by_id["mobile"].type is FieldType.PHONE
    assert by_id["state"].options == ["DC", "NY"] and not by_id["state"].required
    assert by_id["firstName"].max_length == 30
    date = by_id["gnewton_section_50_question_10_answer_date"]
    assert date.type is FieldType.DATE and date.required
    assert by_id["gnewton_section_50_question_30_answer"].label == "Amount (a number)"
    shifts = by_id["gnewton_section_50_question_40_answer"]
    assert shifts.type is FieldType.MULTI_CHOICE and shifts.options == ["1st", "2nd"]
    assert by_id["gnewton_section_50_question_50_answer"].options == ["Yes", "No"]
    assert by_id["gnewton_section_50_question_70_answer_email"].label == "Personal Email"
    assert by_id["gnewton_section_60_question_20_answer_country"].options == [
        "Afghanistan",
        "United States",
    ]
    assert by_id["gnewton_section_60_question_50_answer"].type is FieldType.PHONE
    assert by_id["candidateTextMessagingOptIn"].options == ["Yes", "No"]
    assert by_id["veteran"].label == "Protected veteran status"
    assert by_id["ccpaDisclaimerAgree"].required
    assert "fax_number" not in by_id and "attachmentA" not in by_id


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page)
    answers = {
        "eeo.gender": "I decline to identify",
        "eeo.race": "Hispanic or Latino",
        "eeo.veteran": "I DO NOT WISH TO IDENTIFY AT THIS TIME",
        "question.q1": "Yes",
        "question.q2": "No",
        "resume": resume,
        "firstName": "Ada",
        "lastName": "Placeholder",
        "email": "ada@example.com",
        "attachmentB": resume,
        "mobile": "+12025550143",
        "address1": "1 Main St",
        "state": "NY",
        "zipCode": "20001",
        "gnewton_section_50_question_10_answer_date": "2026-11-19",
        "gnewton_section_50_question_20_answer": "EUR - €",
        "gnewton_section_50_question_30_answer": "50000",
        "gnewton_section_50_question_40_answer": ["1st", "2nd"],
        "gnewton_section_50_question_50_answer": "Yes",
        "gnewton_section_50_question_60_answer": "Placeholder answer.",
        "gnewton_section_50_question_70_answer_email": "ada@example.com",
        "gnewton_section_60_question_10_answer": "Acme",
        "gnewton_section_60_question_20_answer_country": "United States",
        "gnewton_section_60_question_30_answer_state": "DC",
        "gnewton_section_60_question_40_answer_date": "2020-01-31",
        "gnewton_section_60_question_50_answer": "+12025550143",
        "gnewton_section_70_question_20_answer": ["I have read and agree to this statement"],
        "originalComments": "Hello.",
        "candidateTextMessagingOptIn": "No",
        "veteran": "I DO NOT WISH TO IDENTIFY AT THIS TIME",
        "ccpaDisclaimerAgree": ["I have read and agree to this statement"],
    }
    assert set(answers) == {f.id for f in fields}
    assert not matching.problems(fields, answers)
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [c.field_id for c in checks if not c.match] == []
    assert page.locator("#firstName").input_value() == "Ada"  # over what the resume parsed


def test_submit_posts_the_form(page, resume):
    adapter, fields = opened(page)
    adapter.fill(page, testuser.answers(fields, Path(resume)))
    submitted.clear()
    assert "ApplyJob.action" in (adapter.submit(page) or "")
    assert submitted


def test_a_country_without_states_closes_the_state(page):
    adapter, _ = opened(page)
    with pytest.raises(NotTaken, match="no options for that country"):
        adapter.fill(
            page,
            {
                "eeo.gender": "Male",
                "eeo.race": "I decline to identify",
                "eeo.veteran": "I DO NOT WISH TO IDENTIFY AT THIS TIME",
                "question.q1": "Yes",
                "question.q2": "Yes",
                "gnewton_section_60_question_20_answer_country": "Afghanistan",
                "gnewton_section_60_question_30_answer_state": "DC",
            },
        )


def test_a_closed_posting_is_gone(page):
    with pytest.raises(Unsupported, match="posting_gone"):
        opened(page, gone=True)


def test_side_effects_are_declared():
    assert json.dumps([e.step for e in Paycor().side_effects]) == '["upload", "submit"]'


def test_an_unknown_control_is_listed_unhandled():
    control = {"id": "mood", "label": "Mood", "kind": "range", "required": False}
    assert _field(control).unfillable == "unhandled_rendering: mood is a range input"
