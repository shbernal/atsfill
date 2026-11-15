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
        ("coverLetterFile", FieldType.FILE, False, None),
        ("question.PH_Q_1", FieldType.CHOICE, True, None),
        ("question.PH_Q_2", FieldType.MULTI_CHOICE, True, None),
        ("question.PH_Q_3", FieldType.CHOICE, True, None),
        ("profileVisibility", FieldType.CHOICE, True, None),
    ]
    assert fields[8].options == ["Excel", "SAP"]
    assert {f.id: f.one_of for f in fields if f.one_of} == {
        "coverLetter": "coverLetter",
        "coverLetterFile": "coverLetter",
    }


def test_an_apply_that_links_out_is_an_external_step(page):
    page.goto(f"{JOB}?linkout")
    with pytest.raises(Unsupported, match="external_step"):
        Jobstreet().browser_read(page)


def test_a_session_the_site_ended_is_signed_out(page):
    page.goto(f"{JOB}?ended")
    with pytest.raises(SignedOut, match="login_required"):
        Jobstreet().browser_read(page)


CLIENT = "AbC123"
MARK = {"name": f"auth0.{CLIENT}.is.authenticated", "domain": "ph.jobstreet.com"}
CALLBACK = f"{SITE}/oauth/callback/"


class Answered:
    """A request carrying `cookies`, answered `status` with `headers`, whatever it asks."""

    def __init__(self, cookies: list[dict], status: int, headers: dict[str, str]) -> None:
        self.cookies, self.status, self.headers = cookies, status, headers
        self.asked: list[str] = []

    def storage_state(self) -> dict:
        return {"cookies": self.cookies}

    def get(self, url: str, **kwargs) -> Answered:
        self.asked.append(url)
        return self


@pytest.mark.parametrize(
    ("cookies", "status", "headers", "signed_in"),
    [
        ([MARK], 302, {"location": f"{CALLBACK}?code=c0de"}, True),
        ([MARK], 302, {"location": f"{CALLBACK}?error=login_required"}, False),
        ([{**MARK, "domain": "my.jobstreet.com"}], 302, {}, False),  # another site's client
        ([], 302, {}, False),
        ([MARK], 403, {"cf-mitigated": "challenge"}, None),  # Cloudflare outside the browser
    ],
)
def test_the_session_check_asks_seek_for_the_sites_client(cookies, status, headers, signed_in):
    request = Answered(cookies, status, headers)
    assert Jobstreet().signed_in(cast(Request, request), "ph.jobstreet.com") is signed_in
    for url in request.asked:
        assert f"client_id={CLIENT}" in url and "prompt=none" in url


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
    assert adapter.shown_fields(page) == ["question.PH_Q_1", "question.PH_Q_2", "question.PH_Q_3"]
    assert adapter.submit(page) == "Submit application moved to /job/101/apply/success"


def test_fill_uploads_the_cover_letter_its_alternative(opened, page, resume, tmp_path):
    adapter, _ = opened
    letter = tmp_path / "letter" / "CV Ada.pdf"  # named as the resume, as the profile's can be
    letter.parent.mkdir()
    letter.write_bytes(b"%PDF-1.4")
    adapter.fill(page, {"resume": resume, "coverLetterFile": str(letter),
                        "question.PH_Q_1": "₱30K", "question.PH_Q_2": ["SAP"],
                        "question.PH_Q_3": "No"})  # fmt: skip
    read = adapter.read_back(page)
    assert read["coverLetterFile"] == "CV Ada.pdf"
    assert "coverLetter" not in read


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


def test_a_resume_past_the_ten_seek_keeps_is_a_known_gap(page, resume):
    adapter = Jobstreet()
    page.goto(f"{JOB}?fullresumes")
    adapter.browser_read(page)
    with pytest.raises(Unsupported, match="unsupported_flow: resume_limit: the profile holds 10"):
        adapter.fill(page, {"resume": resume})


def test_sign_in_types_the_otp_after_the_recaptcha(page, otp_command):
    adapter = Jobstreet()
    page.goto(adapter.login_url("ph.jobstreet.com"))
    with otp.source("default", adapter.name, "ph.jobstreet.com"):
        adapter.sign_in_by_otp(page, "ada@example.com")
    assert page.url == f"{SITE}/sign-in/onboarding?returnUrl=%2F"


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
