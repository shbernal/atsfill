"""The Wellfound adapter on tests/pages/wellfound: a posting whose Apply opens the dialog its
GraphQL read describes, the sign-in and the job seeker sign-up."""

import json
from collections.abc import Generator
from pathlib import Path
from urllib.parse import parse_qsl

import pytest

from atsfill.adapters import wellfound
from atsfill.adapters.wellfound import Wellfound
from atsfill.core import browser, matching, otp
from atsfill.core.adapter import Blocked, NotTaken, Refused, SignedOut, SignUpDetails, Unsupported
from atsfill.core.results import Answer, FieldType

PAGES = Path(__file__).parent / "pages/wellfound"
POSTING = "https://wellfound.com/jobs/1001-software-engineer"
TAKEN = "ada@example.com"  # the account that exists, its password `Right-Password-1`
NOTE = "Test account for an open-source tool, please ignore. Not a real candidate."


def options(*names: str, first: int = 1) -> list[dict]:
    return [{"id": str(first + i), "option": n} for i, n in enumerate(names)]


QUESTIONS = [
    {"id": "11", "kind": "LONG", "question": "What interests you about Acme?", "required": False,
     "options": []},
    {"id": "12", "kind": "SHORT", "question": "LinkedIn ", "required": True, "options": []},
    {"id": "13", "kind": "NUMBER", "question": "Years of Python?", "required": True,
     "options": []},
    {"id": "14", "kind": "DATE", "question": "Earliest start date", "required": False,
     "options": []},
    {"id": "15", "kind": "SINGLE", "question": "Will you require visa sponsorship?",
     "required": True, "options": options("Yes", "No", first=21)},
    {"id": "16", "kind": "MULTIPLE", "question": "Which languages?", "required": False,
     "options": options("Python", "Go", "Rust", first=31)},
]  # fmt: skip
ANSWERS: dict[str, Answer] = {
    "question_11": NOTE,
    "question_12": "https://www.linkedin.com/in/jane-doe",
    "question_13": "4",
    "question_14": "2027-01-04",
    "question_15": "No",
    "question_16": ["Python", "Rust"],
}


def job(**changes) -> dict:
    return {
        "id": "1001",
        "questions": QUESTIONS,
        "candidateNoteRequired": False,
        "autoPosted": False,
        "currentUserApplied": False,
        "offsiteListingUrl": None,
        "aiAssessmentRequired": False,
        "applicationQuestionSets": [],
        "currentUserQualificationReport": {"errors": [], "warnings": ["INCORRECT_ROLE"]},
        "startup": {"id": "40", "name": "Acme"},
    } | changes


class Site:
    """What the routes serve: the dialog's read, the send's answer, and what was sent."""

    def __init__(self) -> None:
        self.modal: dict = {"data": {"jobListing": job()}}
        self.created: dict = {"data": {"jobListing": {"id": "1001", "currentUserApplied": True}}}
        self.challenged = False
        self.posting = (PAGES / "job.html").read_text()
        self.sent: list[dict] = []
        self.code_status = 200

    def serve(self, route) -> None:
        request = route.request
        path = request.url.split("?")[0].removeprefix("https://wellfound.com")
        match path:
            case "/graphql":
                return self._graphql(route)
            case "/login" if request.method == "POST":
                form = dict(parse_qsl(request.post_data or ""))
                if form.get("password") == "Right-Password-1":
                    # the site redirects; Firefox routes no redirect a fulfill answers
                    moved = "<script>location.replace('/jobs')</script>"
                    return route.fulfill(body=moved, content_type="text/html")
                text = (PAGES / "login.html").read_text()
                text = text.replace("<form", "<p>Email or password is incorrect</p><form")
                return route.fulfill(body=text, content_type="text/html")
            case "/login":
                return route.fulfill(path=PAGES / "login.html")
            case "/jobs/signup":
                return route.fulfill(path=PAGES / "signup.html")
            case "/jobs/signup/email-verification-code":
                if request.post_data_json["email"] == TAKEN:
                    errors = {"email": ["Email has already been taken"]}
                    body = {"success": False, "error": "Check your details", "field_errors": errors}
                    return route.fulfill(status=422, json=body)
                return route.fulfill(status=self.code_status, json={"success": True})
            case "/jobs/signup/email-verification-code/verify":
                ok = request.post_data_json["code"] == "123456"
                return route.fulfill(status=200 if ok else 422, json={"success": ok})
            case "/jobs/create-user":
                return route.fulfill(json={"success": True})
            case _ if path.startswith("/jobs/1001"):
                return route.fulfill(body=self.posting, content_type="text/html")
        route.fulfill(body="<!doctype html><title>Wellfound</title>", content_type="text/html")

    def _graphql(self, route) -> None:
        sent = route.request.post_data_json
        if sent["operationName"] == "JobApplicationModal":
            return route.fulfill(json=self.modal)
        self.sent.append(sent)
        headers = {"cf-mitigated": "challenge"} if self.challenged else {}
        route.fulfill(json=self.created, headers=headers)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        yield page


@pytest.fixture
def site(page) -> Generator[Site]:
    site = Site()
    page.context.route("https://wellfound.com/**", site.serve)
    yield site
    page.context.unroute_all()


def opened(page) -> tuple[Wellfound, list]:
    adapter = Wellfound()
    page.goto(POSTING)
    return adapter, adapter.browser_read(page)


def test_browser_read_lists_the_questions(page, site):
    _, fields = opened(page)
    assert [(f.id, f.type, f.required) for f in fields] == [
        ("question_11", FieldType.TEXT, False),
        ("question_12", FieldType.TEXT, True),
        ("question_13", FieldType.TEXT, True),
        ("question_14", FieldType.DATE, False),
        ("question_15", FieldType.CHOICE, True),
        ("question_16", FieldType.MULTI_CHOICE, False),
    ]
    assert fields[1].label == "LinkedIn"
    assert fields[5].options == ["Python", "Go", "Rust"]


def test_without_questions_the_dialog_asks_a_note(page, site):
    site.modal = {"data": {"jobListing": job(questions=[], candidateNoteRequired=True)}}
    adapter, fields = opened(page)
    assert [(f.id, f.label, f.required) for f in fields] == [
        ("note", "Write a note to Acme.", True)
    ]
    adapter.fill(page, {"note": NOTE})
    assert adapter.read_back(page) == {"note": NOTE}


def test_every_answer_reads_back(page, site):
    adapter, fields = opened(page)
    adapter.fill(page, ANSWERS)
    checks = matching.check(fields, ANSWERS, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []


def test_a_choice_answered_again_moves_the_ticks(page, site):
    adapter, _ = opened(page)
    adapter.fill(page, ANSWERS)
    adapter.fill(page, {"question_15": "Yes", "question_16": ["Go"]})
    read = adapter.read_back(page)
    assert (read["question_15"], read["question_16"]) == ("Yes", ["Go"])


@pytest.mark.parametrize(
    ("answers", "said"),
    [
        ({"question_15": "Maybe"}, "'Maybe' is no option of question_15"),
        ({"question_13": "four"}, "'four' is no number for question_13"),
        ({"question_14": "04/01/2027"}, "'04/01/2027' is no date for question_14"),
    ],
)
def test_an_answer_the_dialog_cannot_take(page, site, answers, said):
    adapter, _ = opened(page)
    with pytest.raises(NotTaken, match=said):
        adapter.fill(page, answers)


def test_submit_returns_the_job_applied_to(page, site):
    adapter, _ = opened(page)
    adapter.fill(page, ANSWERS)
    assert adapter.submit(page) == "CreateJobApplication 200: job 1001 currentUserApplied"
    (sent,) = site.sent
    assert sent["variables"]["input"]["jobListingId"] == "1001"


@pytest.mark.parametrize(
    ("created", "challenged", "reason"),
    [
        ({"errors": [{"message": "Answer is too long"}]}, False, "answer_refused: Answer is too"),
        ({"data": None}, True, "captcha"),
    ],
)
def test_what_the_send_is_answered(page, site, created, challenged, reason):
    site.created, site.challenged = created, challenged
    adapter, _ = opened(page)
    adapter.fill(page, ANSWERS)
    with pytest.raises(Blocked, match=f"^{reason}"):
        adapter.submit(page)


def test_an_answer_without_the_job_applied_is_no_evidence(page, site):
    site.created = {"data": {"jobListing": {"id": "1001", "currentUserApplied": False}}}
    adapter, _ = opened(page)
    adapter.fill(page, ANSWERS)
    assert adapter.submit(page) is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"currentUserApplied": True}, "already_sent"),
        ({"offsiteListingUrl": "https://jobs.example.com/1"}, "external_step"),
        (
            {"currentUserQualificationReport": {"errors": ["MISSING_SKILLS"], "warnings": []}},
            "unsupported_flow: profile_incomplete: the job needs the profile's skills",
        ),
        (
            {"currentUserQualificationReport": {"errors": ["LOCATION_MISMATCH"], "warnings": []}},
            "not_qualified: LOCATION_MISMATCH",
        ),
        ({"aiAssessmentRequired": True}, "unsupported_flow: ai_assessment"),
        ({"applicationQuestionSets": [{"id": "1"}]}, "unsupported_rendering: question_sets"),
    ],
)
def test_what_the_dialog_stops(page, site, changes, reason):
    site.modal = {"data": {"jobListing": job(**changes)}}
    with pytest.raises(Unsupported, match=f"^{reason}"):
        opened(page)


def test_an_unfinished_onboarding_sends_apply_to_the_profile(page, site):
    site.modal = {"redirect": "/profile/edit?job_listing_id=1001"}
    with pytest.raises(Unsupported, match="^unsupported_flow: profile_incomplete"):
        opened(page)


def test_a_session_ended_shows_the_visitors_dialog(page, site):
    site.modal = {"visitor": True}
    with pytest.raises(SignedOut, match="^login_required"):
        opened(page)


def test_a_closed_posting_is_gone(page, site):
    site.posting = site.posting.replace(
        "<h1>", "<div>The job listing is no longer available</div><h1>"
    )
    with pytest.raises(Unsupported, match="^posting_gone"):
        opened(page)


def test_detection_and_posting():
    adapter = Wellfound()
    assert adapter.detect(POSTING + "?utm_source=x", None)
    assert adapter.detect("https://wellfound.com/company/acme/jobs/1001-software-engineer", None)
    assert not adapter.detect("https://wellfound.com/company/acme", None)
    assert not adapter.detect("https://wellfound.com/jobs", None)
    assert adapter.posting(POSTING + "?utm_source=x") == "https://wellfound.com/jobs/1001"
    assert adapter.posting("https://wellfound.com/company/acme/jobs/1001-x") == (
        "https://wellfound.com/jobs/1001"
    )


@pytest.fixture
def signing_in(page, site) -> Wellfound:
    page.context.clear_cookies()
    page.goto(wellfound.LOGIN)
    return Wellfound()


def test_sign_in(signing_in, page):
    signing_in.sign_in(page, TAKEN, "Right-Password-1")
    assert page.url == "https://wellfound.com/jobs"


def test_a_wrong_password_is_refused(signing_in, page):
    with pytest.raises(Refused, match="^wrong_password"):
        signing_in.sign_in(page, TAKEN, "Wrong-Password-1")


def test_sign_up_types_the_emailed_otp(signing_in, page, otp_command):
    with otp.source("default", signing_in.name, None):
        signing_in.sign_up(
            page, "new@example.com", "Right-Password-1", SignUpDetails(name="Jane Doe")
        )
    assert page.url == "https://wellfound.com/jobs/onboarding/extended_profile"


@pytest.mark.parametrize(
    ("email", "password", "reason"),
    [
        (TAKEN, "Right-Password-1", "email_in_use"),
        ("new@example.com", "short", "password_rule"),
    ],
)
def test_what_sign_up_is_told(signing_in, page, email, password, reason):
    with pytest.raises(Refused, match=f"^{reason}"):
        signing_in.sign_up(page, email, password, SignUpDetails(name="Jane Doe"))


def test_too_many_otps_asked(signing_in, page, site):
    site.code_status = 429
    with pytest.raises(Blocked, match="^rate_limited"):
        signing_in.sign_up(
            page, "new@example.com", "Right-Password-1", SignUpDetails(name="Jane Doe")
        )


def test_the_fixture_dialog_read_matches_a_live_shape():
    """The fields the adapter reads from each question, as `JobApplicationModal` answers them."""
    for question in QUESTIONS:
        assert set(question) == {"id", "kind", "question", "required", "options"}
    assert json.dumps(job())  # serializable as the route answers it
