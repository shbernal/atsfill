"""The Jobstreet adapter on tests/pages/jobstreet: a posting, its Quick apply wizard, SEEK's
sign-in."""

import json
from pathlib import Path
from typing import cast

import pytest

from atsfill.adapters.jobstreet import Jobstreet
from atsfill.core import browser, otp
from atsfill.core.adapter import NotTaken, Refused, SignedOut, SignUpDetails, Unsupported
from atsfill.core.browser import Request
from atsfill.core.results import FieldType

PAGES = Path(__file__).parent / "pages/jobstreet"
SITE = "https://ph.jobstreet.com"
JOB = f"{SITE}/job/101"
VIEWER = {
    "personalDetails": {"firstName": "Ada", "lastName": "Placeholder", "phoneNumber": ""},
    "currentLocation2": {"description": "Makati City Metro Manila"},
}
QUESTIONS = [
    {"id": "PH_Q_1", "text": "Your expected salary?", "__typename": "SingleChoiceQuestion",
     "options": [{"id": "PH_Q_1_A_1", "text": "₱20K", "uri": "u1"},
                 {"id": "PH_Q_1_A_2", "text": "₱30K", "uri": "u2"}]},
    {"id": "PH_Q_2", "text": "Which tools have you used?", "__typename": "MultipleChoiceQuestion",
     "options": [{"id": "PH_Q_2_A_1", "text": "Excel", "uri": "u3"},
                 {"id": "PH_Q_2_A_2", "text": "SAP", "uri": "u4"}]},
    {"id": "PH_Q_3", "text": "Do you have a work permit?", "__typename": "SingleChoiceQuestion",
     "options": [{"id": "PH_Q_3_A_1", "text": "Yes", "uri": "u5"},
                 {"id": "PH_Q_3_A_2", "text": "No", "uri": "u6"}]},
]  # fmt: skip
ANSWERS = {
    "GetUser": {"viewer": {"profileVisibility2": None}},
    "GetPersonalDetails": {"viewer": VIEWER},
    "GetJobApplicationProcess": {
        "jobApplicationProcess": {"linkOut": False, "questionnaire": {"questions": QUESTIONS}}
    },
}


def serve(route) -> None:
    url = route.request.url
    if url.startswith(f"{SITE}/graphql"):
        sent = route.request.post_data_json
        route.fulfill(json=[{"data": ANSWERS[op["operationName"]]} for op in sent])
    elif "login.seek.com" in url or "/oauth/" in url:  # the site's /oauth/login redirects there
        route.fulfill(path=PAGES / "login.html")
    else:
        route.fulfill(path=PAGES / "job.html")


@pytest.fixture
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://*.jobstreet.com/**", serve)
        page.context.route("https://login.seek.com/**", serve)
        yield page


@pytest.fixture
def opened(page) -> tuple[Jobstreet, list]:
    adapter = Jobstreet()
    page.goto(JOB)
    return adapter, adapter.browser_read(page)


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def test_a_posting_is_scoped_to_its_site():
    adapter = Jobstreet()
    assert adapter.detect("https://hk.jobsdb.com/job/95555465?utm_source=x", None)
    assert adapter.detect("https://th.jobsdb.com/th/job/95559540", None)
    assert not adapter.detect("https://www.seek.com.au/job/1", None)
    assert (
        adapter.posting("https://my.jobstreet.com/job/7?ref=x") == "https://my.jobstreet.com/job/7"
    )
    assert adapter.account_scope(JOB) == "ph.jobstreet.com"


def test_browser_read_lists_the_details_documents_questions_and_visibility(opened):
    _, fields = opened
    assert [(f.id, f.type, f.required, f.prefilled) for f in fields] == [
        ("firstName", FieldType.TEXT, True, "Ada"),
        ("lastName", FieldType.TEXT, True, "Placeholder"),
        ("currentLocation", FieldType.TYPEAHEAD, True, "Makati City Metro Manila"),
        ("phone", FieldType.PHONE, False, None),
        ("resume", FieldType.FILE, False, None),
        ("coverLetter", FieldType.TEXT, False, None),
        ("question.PH_Q_1", FieldType.CHOICE, True, None),
        ("question.PH_Q_2", FieldType.MULTI_CHOICE, True, None),
        ("question.PH_Q_3", FieldType.CHOICE, True, None),
        ("profileVisibility", FieldType.CHOICE, True, None),
    ]
    assert fields[7].options == ["Excel", "SAP"]


def test_an_apply_that_links_out_is_an_external_step(page):
    page.goto(f"{JOB}?linkout")
    with pytest.raises(Unsupported, match="external_step"):
        Jobstreet().browser_read(page)


def test_a_session_the_site_ended_is_signed_out(page):
    page.goto(f"{JOB}?ended")
    with pytest.raises(SignedOut, match="login_required"):
        Jobstreet().browser_read(page)


class Answered:
    """A request answered `status` with `headers`, whatever it asks."""

    def __init__(self, status: int, headers: dict[str, str]) -> None:
        self.status, self.headers = status, headers

    def get(self, url: str, **kwargs) -> Answered:
        return self


@pytest.mark.parametrize(
    ("status", "headers", "signed_in"),
    [
        (307, {"location": "/profile/me"}, True),
        (200, {}, False),
        (403, {}, None),  # Cloudflare outside the browser
        (200, {"cf-mitigated": "challenge"}, None),
    ],
)
def test_the_session_check_reads_profile(status, headers, signed_in):
    request = cast(Request, Answered(status, headers))
    assert Jobstreet().signed_in(request, "ph.jobstreet.com") is signed_in


def test_fill_walks_the_steps_and_reads_back_each(opened, page, resume):
    adapter, _ = opened
    answers = {
        "currentLocation": "Manila City Metro Manila",
        "phone": "+12025550143",
        "resume": resume,
        "coverLetter": "Dear Acme",
        "question.PH_Q_1": "₱30K",
        "question.PH_Q_2": ["SAP"],
        "question.PH_Q_3": "No",
        "profileVisibility": "Hidden",
    }
    adapter.fill(page, answers)
    assert page.url == f"{JOB}/apply/review"
    assert adapter.read_back(page) == {
        "firstName": "Ada",
        "lastName": "Placeholder",
        "currentLocation": "Manila City Metro Manila",
        "phone": "+12025550143",
        "resume": "CV Ada.pdf",
        "coverLetter": "Dear Acme",
        "question.PH_Q_1": "₱30K",
        "question.PH_Q_2": ["SAP"],
        "question.PH_Q_3": "No",
        "profileVisibility": "Hidden",
    }
    assert adapter.submit(page) == "Submit application moved to /job/101/apply/success"


def test_an_unanswered_question_keeps_the_step(opened, page):
    adapter, _ = opened
    with pytest.raises(NotTaken, match="Your expected salary"):
        adapter.fill(page, {"question.PH_Q_2": ["SAP"], "question.PH_Q_3": "Yes"})


def test_a_profile_without_a_career_history_is_a_known_gap(page):
    adapter = Jobstreet()
    page.goto(f"{JOB}?nohistory")
    adapter.browser_read(page)
    answers = {"question.PH_Q_1": "₱20K", "question.PH_Q_2": ["Excel"], "question.PH_Q_3": "Yes"}
    with pytest.raises(Unsupported, match="unsupported_flow: profile_incomplete"):
        adapter.fill(page, answers)


def test_sign_in_types_the_otp_after_the_recaptcha(page, otp_command):
    adapter = Jobstreet()
    page.goto(adapter.login_url("ph.jobstreet.com"))
    with otp.source("default", adapter.name, "ph.jobstreet.com"):
        adapter.sign_in_by_otp(page, "ada@example.com")
    assert page.url == f"{SITE}/"


def test_an_email_without_an_account_stops_before_registering(page, otp_command):
    adapter = Jobstreet()
    page.goto(adapter.login_url("ph.jobstreet.com"))
    with otp.source("default", adapter.name, "ph.jobstreet.com"), pytest.raises(Refused):
        adapter.sign_in_by_otp(page, "new@example.com")
    assert "signin-confirmation" in page.url


def test_sign_up_registers_an_email_without_an_account(page, otp_command):
    adapter = Jobstreet()
    page.goto(adapter.login_url("ph.jobstreet.com"))
    with otp.source("default", adapter.name, "ph.jobstreet.com"):
        adapter.sign_up_by_otp(page, "new@example.com", SignUpDetails())
    assert page.url == f"{SITE}/?registered"
    assert json.dumps(adapter.side_effects[0].step) == '"sign_up"'
