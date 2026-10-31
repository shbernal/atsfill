"""The Wellfound adapter on tests/pages/wellfound: a posting whose Apply opens the dialog its
GraphQL read describes, the sign-in, the job seeker sign-up and the account profile's two tabs,
whose saves the routes hold as the system does."""

import json
from collections.abc import Generator
from pathlib import Path
from urllib.parse import parse_qsl

import pytest

from atsfill.adapters import wellfound
from atsfill.adapters.wellfound import Wellfound
from atsfill.core import browser, matching, otp
from atsfill.core.adapter import (
    Blocked,
    KeepsProfile,
    NotTaken,
    ProfileIncomplete,
    Refused,
    SignedOut,
    SignUpDetails,
    Unsupported,
)
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


ROLES = {"1": "Software Engineer", "2": "Backend Engineer"}
TAGS = {
    "1": ("Paris", "France"),
    "2": ("Lyon", "France"),
    "11": ("Python", None),
    "12": ("Go", None),
    "13": ("React.js", None),
}
COMPANIES = {"21": "Acme", "22": "Initech"}
PROFILE_ANSWERS: dict[str, Answer] = {
    "location": "Paris, France",
    "primary_role": "Software Engineer",
    "years_experience": "3 Years",
    "skills": ["Python", "React.js"],
    "bio": "Builds internal tools.",
    "linkedin": "https://linkedin.com/in/jane-doe",
    "work_company": "Acme",
    "work_title": "Software Engineer",
    "work_from": "01/2022",
    "work_to": "06/2025",
    "work_current": "No",
    "work_description": "Built internal tools.",
    "job_type": "Full-time Employee",
    "us_authorized": "No",
    "require_sponsorship": "Yes",
    "remote": "Yes",
    "job_status": "Closed to offers",
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


OFFER = """<div class="ReactModal__Content" style="position:fixed;inset:0;background:#fff">
  We want to feature you to recruiters!
  <button onclick="fetch('/graphql', {method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({operationName: 'OfferStart'})})">Start my application</button>
  <button onclick="this.parentNode.remove()">Skip for now — I'm not ready yet</button>
</div><main"""


class Site:
    """What the routes serve: the dialog's read, the send's answer, and what was sent."""

    def __init__(self) -> None:
        self.modal: dict = {"data": {"jobListing": job()}}
        self.created: dict = {"data": {"jobListing": {"id": "1001", "currentUserApplied": True}}}
        self.challenged = False
        self.posting = (PAGES / "job.html").read_text()
        self.sent: list[dict] = []
        self.code_status = 200
        self.candidate: dict = {}  # what the profile's mutations saved
        self.operations: list[str] = []
        self.answer_errors: list[dict] = []  # a mutation's answer
        self.offer = False
        self.enrolled = False

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
            case "/profile/edit" | "/profile/edit/preferences":
                return route.fulfill(body=self.profile_page(), content_type="text/html")
            case _ if path.startswith("/jobs/1001"):
                return route.fulfill(body=self.posting, content_type="text/html")
        route.fulfill(body="<!doctype html><title>Wellfound</title>", content_type="text/html")

    def profile_page(self) -> str:
        """The tabs' page around the Apollo state the server renders it from."""
        saved = self.candidate
        ref = lambda kind, key: {"__ref": f"{kind}:{key}"}  # noqa: E731
        state = {"Candidate:7": saved | {
            "__typename": "Candidate",
            "primaryRole": [ROLES[saved["primaryRoleId"]]] if "primaryRoleId" in saved else [],
            "basedInLocation": ref("NewTag", saved["place"]) if "place" in saved else None,
            "skillTags": [ref("NewTag", t) for t in saved.get("skills", [])],
            "startupRoleMetadata": [ref("StartupRoleMetadata", "5")] if "work" in saved else [],
        }}  # fmt: skip
        for tag, (name, parent) in TAGS.items():
            state[f"NewTag:{tag}"] = {
                "id": tag, "displayName": name, "parentLocationDisplayName": parent,
            }  # fmt: skip
        if "work" in saved:
            work = saved["work"]
            state["StartupRoleMetadata:5"] = work | {"id": "5", "startup": ref("Startup", "9")}
            state["Startup:9"] = {"name": COMPANIES[work["startupId"]]}
        data = {"props": {"pageProps": {"apolloState": {"data": state}}}}
        page = (PAGES / "profile.html").read_text().replace("/*STATE*/", json.dumps(data))
        if self.offer:
            page = page.replace("<main", OFFER, 1)
        return page

    def _save_profile(self, operation: str, values: dict) -> None:
        saved = self.candidate
        match operation:
            case "ProfileSavePrimaryLocation":
                saved["place"] = values["primaryLocation"]
            case "ProfileSaveSkills":
                saved["skills"] = values["skillTags"]
            case "ProfileSaveExperience":
                entry = saved.get("work", {}) | {k: v for k, v in values.items() if v is not None}
                saved["work"] = entry | {"endedAtMonth": values["endedAtMonth"]}
                saved["work"] |= {"endedAtYear": values["endedAtYear"]}
            case "SaveCandidateState":
                # the system answers 200 and keeps none while a required field is missing
                if "primaryRoleId" in saved and "usAuthorized" in saved:
                    saved |= values
            case _:
                saved |= {k: v for k, v in values.items() if v is not None}

    def _graphql(self, route) -> None:
        sent = route.request.post_data_json
        if sent["operationName"] == "JobApplicationModal":
            return route.fulfill(json=self.modal)
        if sent["operationName"] == "OfferStart":
            self.enrolled = True
            return route.fulfill(json={})
        if sent["operationName"].startswith(("Profile", "SaveCandidate")):
            self.operations.append(sent["operationName"])
            if self.answer_errors:
                return route.fulfill(json={"errors": self.answer_errors})
            self._save_profile(sent["operationName"], sent["variables"]["input"])
            return route.fulfill(json={"data": {}})
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
    assert adapter.shown_fields(page) == [f.id for f in fields]


def test_a_field_an_answer_shows_is_listed_shown(page, site):
    adapter, fields = opened(page)
    page.locator(wellfound.FORM).evaluate(
        """form => form.insertAdjacentHTML("afterbegin",
          '<input name="customQuestionAnswers[999][answer]">')"""
    )
    assert set(adapter.shown_fields(page)) - {f.id for f in fields} == {"question_999"}


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
            "the job needs the profile's skills",
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
    with pytest.raises((Unsupported, ProfileIncomplete), match=f"^{reason}"):
        opened(page)


def test_an_unfinished_profile_is_the_profile_commands_to_fix(page, site):
    site.modal = {"redirect": "/profile/edit?job_listing_id=1001"}
    with pytest.raises(ProfileIncomplete, match="unfinished profile"):
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


def run_profile(page, answers: dict[str, Answer]) -> list[str]:
    """The core's walk of the steps, returning the ids that do not read back."""
    adapter, wrong = Wellfound(), []
    assert isinstance(adapter, KeepsProfile)
    page.goto(adapter.profile_url(None))
    for step in adapter.profile_steps(page):
        assert adapter.open_step(page, step)
        fields = adapter.profile_read(page)
        mine = {f.id: answers[f.id] for f in fields if f.id in answers}
        adapter.profile_fill(page, mine)
        checks = matching.check(fields, mine, adapter.profile_read_back(page))
        wrong += [c.field_id for c in checks if not c.match]
        adapter.save(page)
    return wrong


def test_a_fresh_profile_is_saved_and_reads_back(page, site):
    assert run_profile(page, PROFILE_ANSWERS) == []
    assert site.candidate["candidateState"] == "closed"
    assert site.operations[-1] == "SaveCandidateState"  # ignored while a required one is missing


def test_a_profile_that_matches_saves_nothing(page, site):
    run_profile(page, PROFILE_ANSWERS)
    site.operations.clear()
    assert run_profile(page, PROFILE_ANSWERS) == []
    assert site.operations == []


def test_changes_reach_saved_values(page, site):
    run_profile(page, PROFILE_ANSWERS)
    changed = PROFILE_ANSWERS | {
        "location": "Lyon, France",
        "primary_role": "Backend Engineer",
        "skills": ["Python", "Go"],
        "work_title": "Senior Engineer",
        "work_from": "02/2021",
        "work_current": "Yes",
        "job_status": "Open to offers",
    }
    del changed["work_to"]
    assert run_profile(page, changed) == []
    assert site.candidate["work"]["current"] is True
    assert site.candidate["work"]["endedAtYear"] is None
    assert site.candidate["skills"] == ["11", "12"]


def test_the_steps_list_the_saved_values(page, site):
    run_profile(page, PROFILE_ANSWERS)
    adapter = Wellfound()
    adapter.open_step(page, "about")
    fields = {f.id: f for f in adapter.profile_read(page)}
    assert fields["primary_role"].options == list(ROLES.values())
    assert fields["primary_role"].prefilled == "Software Engineer"
    assert fields["work_to"].prefilled == "06/2025"
    assert fields["skills"].prefilled == ["Python", "React.js"]
    adapter.open_step(page, "preferences")
    fields = {f.id: f for f in adapter.profile_read(page)}
    assert fields["job_status"].prefilled == "Closed to offers"
    assert fields["remote"].prefilled == "Yes"


def test_a_remote_box_never_ticked_already_answers_no(page, site):
    assert run_profile(page, {"remote": "No"}) == []
    assert site.operations == []


def test_a_status_the_system_ignored_does_not_read_back(page, site):
    assert run_profile(page, {"job_status": "Closed to offers"}) == ["job_status"]


def test_a_saved_entrys_company_cannot_change(page, site):
    run_profile(page, PROFILE_ANSWERS)
    with pytest.raises(Refused, match="^step_refused: about: work_company"):
        run_profile(page, PROFILE_ANSWERS | {"work_company": "Initech"})


def test_a_first_entry_needs_its_dates(page, site):
    with pytest.raises(Refused, match="needs work_company, work_from, work_to"):
        run_profile(page, {"work_title": "Software Engineer"})


def test_a_name_the_search_does_not_list_is_not_taken(page, site, monkeypatch):
    monkeypatch.setattr(wellfound, "SUGGEST_TIMEOUT_MS", 500)
    with pytest.raises(NotTaken, match="'Cobol'"):
        run_profile(page, {"skills": ["Cobol"]})


def test_a_value_the_page_refuses_saves_nothing(page, site, monkeypatch):
    monkeypatch.setattr(wellfound, "SAVE_TIMEOUT_MS", 500)
    with pytest.raises(Refused, match="^step_refused: github: the page saved nothing"):
        run_profile(page, {"github": "not a url"})


def test_a_mutation_refused_stops_the_step(page, site):
    site.answer_errors = [{"message": "Bio is too long"}]
    with pytest.raises(Refused, match="^step_refused: bio: Bio is too long"):
        run_profile(page, {"bio": "Too long."})


def test_the_recruiter_offer_is_declined_never_started(page, site):
    site.offer = True
    assert run_profile(page, PROFILE_ANSWERS) == []
    assert not site.enrolled


def test_the_fixture_dialog_read_matches_a_live_shape():
    """The fields the adapter reads from each question, as `JobApplicationModal` answers them."""
    for question in QUESTIONS:
        assert set(question) == {"id", "kind", "question", "required", "options"}
    assert json.dumps(job())  # serializable as the route answers it
