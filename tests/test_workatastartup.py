"""The Work at a Startup adapter on tests/pages/workatastartup: a ycombinator.com posting, its
signed-in Work at a Startup job page with the apply dialog, and the account forms."""

import io
import json
from collections.abc import Generator
from pathlib import Path

import pytest

from atsfill.adapters import workatastartup
from atsfill.adapters.workatastartup import WorkAtAStartup
from atsfill.core import browser, matching, otp
from atsfill.core.adapter import (
    Blocked,
    KeepsProfile,
    ProfileIncomplete,
    Refused,
    SignUpDetails,
    Unsupported,
)
from atsfill.core.results import Answer, FieldType

PAGES = Path(__file__).parent / "pages/workatastartup"
POSTING = "https://www.ycombinator.com/companies/acme/jobs/AbC123x-software-engineer"
JOB = "https://www.workatastartup.com/jobs/1001"
TAKEN = "ada@example.com"  # the account that exists, its password `Right-Password-1`
CODE = "otp@example.com"  # an account whose sign-in asks the emailed OTP
MESSAGE = "Test account for an open-source tool, please ignore. Not a real candidate."
ANSWERS: dict[str, Answer] = {
    "message": MESSAGE,
    "question_11": "Because.",
    "question_12": "Paris",
    "question_13": "https://example.com/work",
    "question_14": "Yes",
    "question_15": ["Python", "Rust"],
}


STEPS = ["personal", "location", "role", "experience", "skills", "career", "share"]
PROFILE = {
    "first_name": "Jane", "last_name": "Doe", "email": "jane@example.com",
    "linkedin": "https://www.linkedin.com/in/jane-doe", "phone_country": "US/Canada (+1)",
    "phone_number": "2125550123", "share": "no", "yc_affiliation": "none",
    "city_current": "New York, NY, USA", "us_authorized": "yes", "us_visa_sponsorship": "no",
    "remote": "yes", "relocate": "no", "role": "eng", "eng_type": ["Backend"], "in_school": "no",
    "job_type": ["fulltime"], "experience": "5", "manual": True,
    "positions": [{"id": 1, "employer_name_other": "Acme Corp", "title": "Engineer",
                   "start_date": "2020-01", "is_current": True}],
    "educations": [{"id": 2, "school_name_other": "State University", "field_of_study": "CS",
                    "degree": "Bachelor", "start_date": "2015-09", "end_date": "2019-06"}],
    "top_skills": [{"value": 107, "rating": "advanced"}, {"value": 99, "rating": "advanced"}],
    "company_sizes": {"1_10": "medium", "300_+": "medium"}, "equity_importance": "medium",
    "short_phrase": "Backend engineer", "looking_for": "A small product team.",
}  # fmt: skip


class Site:
    """What the routes serve: the job page with `replace` applied, `/apply`'s answer, and
    what was posted to it."""

    def __init__(self) -> None:
        self.replace: dict[str, str] = {}
        self.applied: dict = {"company_id": 40, "job_ids": [1001]}
        self.posted: list[dict] = []
        self.profile: dict = json.loads(json.dumps(PROFILE))
        self.steps = [{"name": n, "status": "complete"} for n in STEPS]
        self.saved: list[dict] = []
        self.save_status = 200

    def serve(self, route) -> None:
        request = route.request
        path = request.url.split("?")[0]
        if path.startswith("https://account.ycombinator.com/"):
            return self._account(route)
        if "/application/" in path:
            return self._profile(route)
        if path.endswith("/apply"):
            self.posted.append(request.post_data_json)
            return route.fulfill(json=self.applied)
        if path.startswith("https://www.ycombinator.com/"):
            return route.fulfill(path=PAGES / "posting.html")
        if "/jobs/" in path:
            text = (PAGES / "job.html").read_text()
            for old, new in self.replace.items():
                text = text.replace(old, new)
            return route.fulfill(body=text, content_type="text/html")
        route.fulfill(body="<!doctype html><title>Companies</title>", content_type="text/html")

    def _profile(self, route) -> None:
        request = route.request
        if request.method == "POST":
            self.saved.append(request.post_data_json)
            errors = {"errors": ["Phone number is an invalid number"], "error_type": "validation"}
            return route.fulfill(
                status=self.save_status,
                json=errors if self.save_status == 422 else {"redirect_to": "/application/next"},
            )
        data = (
            f"window.PROFILE = {json.dumps(self.profile)}; window.STEPS = {json.dumps(self.steps)};"
        )
        inject = f"<script>{data}</script>"
        text = (
            (PAGES / "profile.html")
            .read_text()
            .replace('<div id="app">', inject + '<div id="app">')
        )
        route.fulfill(body=text, content_type="text/html")

    def _account(self, route) -> None:
        request = route.request
        if request.method == "GET":
            return route.fulfill(path=PAGES / "account.html")
        sent = request.post_data_json
        match request.url.split("?")[0].removeprefix("https://account.ycombinator.com"):
            case "/sign_in" if sent["password"] != "Right-Password-1":
                route.fulfill(status=422, json={"error": "Password doesn't match"})
            case "/sign_in":
                route.fulfill(json={"email_code": sent["username"] == CODE})
            case "/sign_in/email_code":
                route.fulfill(status=200 if sent["code"] == "123456" else 422, json={})
            case "/sign_up" if sent["email"] == TAKEN:
                route.fulfill(status=422, json={"errors": {"email": "An account exists."}})
            case "/sign_up" if len(sent["password"]) < 8:
                route.fulfill(status=422, json={"errors": {"password": "Too short."}})
            case _:
                route.fulfill(json={})


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        yield page


@pytest.fixture
def site(page) -> Generator[Site]:
    site = Site()
    for host in ("www.ycombinator.com", "www.workatastartup.com", "account.ycombinator.com"):
        page.context.route(f"https://{host}/**", site.serve)
    yield site
    page.context.unroute_all()


def opened(page, url: str = POSTING) -> tuple[WorkAtAStartup, list]:
    adapter = WorkAtAStartup()
    page.goto(url)
    return adapter, adapter.browser_read(page)


def test_browser_read_lists_the_message_and_the_questions(page, site):
    _, fields = opened(page)
    assert page.url == JOB
    assert [(f.id, f.type, f.required, f.max_length) for f in fields] == [
        ("message", FieldType.TEXT, True, None),
        ("question_11", FieldType.TEXT, True, 5000),
        ("question_12", FieldType.TEXT, False, 250),
        ("question_13", FieldType.URL, False, 2000),
        ("question_14", FieldType.CHOICE, True, None),
        ("question_15", FieldType.MULTI_CHOICE, False, None),
    ]  # the archived question is not shown
    assert fields[2].label == "Where do you live?"
    assert fields[5].options == ["Python", "Go", "Rust"]


def test_request_read_lists_the_same_fields(page, site, monkeypatch):
    _, fields = opened(page)
    posting = (PAGES / "posting.html").read_text()
    questions = json.loads(posting.split("data-page='")[1].split("'>")[0])["props"][
        "customQuestions"
    ]
    fetched = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

    def urlopen(request, timeout):
        fetched.append(request.full_url)
        if request.full_url.endswith("/custom_questions.json"):
            return Response(json.dumps({"questions": questions}).encode())
        return Response(posting.encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    workatastartup._yc_props.cache_clear()
    assert WorkAtAStartup().request_read(POSTING + "?utm_source=x") == fields
    assert WorkAtAStartup().request_read(JOB) == fields
    assert fetched == [
        "https://www.ycombinator.com/companies/acme/jobs/AbC123x-software-engineer",
        f"{JOB}/custom_questions.json",
    ]


def test_every_answer_reads_back_and_the_relocation_box_is_unticked(page, site):
    adapter, fields = opened(page)
    adapter.fill(page, ANSWERS)
    checks = matching.check(fields, ANSWERS, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert not page.locator("[data-atsfill-field=open_to_relocation]").is_checked()


def test_submit_posts_the_answers_and_returns_the_jobs_applied_to(page, site):
    adapter, _ = opened(page)
    adapter.fill(page, ANSWERS)
    assert adapter.submit(page) == "POST /apply 200, job_ids [1001]"
    (posted,) = site.posted
    assert (posted["message"], posted["open_to_relocation"]) == (MESSAGE, False)
    assert posted["custom_question_answers"]["14"] == 1


@pytest.mark.parametrize(
    ("answer", "error", "reason"),
    [
        (
            {"limit": True, "reason": "You reached your limit."},
            Blocked,
            "rate_limited: You reached",
        ),
        ({"success": False, "question_id": 12, "error": "Too long"}, Blocked, "answer_refused"),
    ],
)
def test_what_send_is_answered(page, site, answer, error, reason):
    site.applied = answer
    adapter, _ = opened(page)
    adapter.fill(page, ANSWERS)
    with pytest.raises(error, match=f"^{reason}"):
        adapter.submit(page)


def test_an_answer_without_the_job_is_no_evidence(page, site):
    site.applied = {"company_id": 40, "job_ids": [999]}
    adapter, _ = opened(page)
    adapter.fill(page, ANSWERS)
    assert adapter.submit(page) is None


def test_a_short_message_refuses_before_the_fill(page, site):
    _, fields = opened(page)
    assert fields[0].min_length == 50
    problems = matching.problems(fields, {**ANSWERS, "message": "Hi."})
    assert problems == ["message: 3 characters, under the minimum of 50"]


@pytest.mark.parametrize(
    ("old", "new", "error", "reason"),
    [
        ("minimally_complete: true", "minimally_complete: false", ProfileIncomplete, "incomplete"),
        ("shared: true", "shared: false", ProfileIncomplete, "hidden"),
        ("appliedJobIds: []", "appliedJobIds: [1001]", Unsupported, "posting_gone"),
        ("job: {id: 1001", "job: null, x: {id: 1001", Unsupported, "posting_gone"),
    ],
)
def test_what_the_job_page_stops(page, site, old, new, error, reason):
    site.replace = {old: new}
    with pytest.raises(error, match=f"^{reason}"):
        opened(page, JOB)


def test_detection_and_posting(monkeypatch):
    adapter = WorkAtAStartup()
    assert adapter.detect(POSTING, None)
    assert adapter.detect(JOB, None)
    assert not adapter.detect("https://www.ycombinator.com/companies/acme", None)
    assert not adapter.detect("https://www.workatastartup.com/companies", None)
    assert adapter.posting(JOB + "?utm_source=x") == JOB
    monkeypatch.setattr(workatastartup, "_yc_props", lambda url: {"job": {"id": 1001}})
    assert adapter.posting(POSTING + "?utm_source=x") == JOB


@pytest.fixture
def signing_in(page, site) -> WorkAtAStartup:
    page.context.clear_cookies()
    page.goto(workatastartup.ACCOUNT)
    return WorkAtAStartup()


def test_sign_in(signing_in, page):
    signing_in.sign_in(page, TAKEN, "Right-Password-1")
    assert page.url == "https://www.workatastartup.com/"


def test_a_wrong_password_is_refused(signing_in, page):
    with pytest.raises(Refused, match="^wrong_password"):
        signing_in.sign_in(page, TAKEN, "Wrong-Password-1")


def test_an_emailed_otp_is_typed_in(signing_in, page, otp_command):
    with otp.source("default", signing_in.name, None):
        signing_in.sign_in(page, CODE, "Right-Password-1")
    assert page.url == "https://www.workatastartup.com/"


def test_sign_up(signing_in, page):
    signing_in.sign_up(page, "new@example.com", "Right-Password-1", SignUpDetails(name="Jane Doe"))
    assert page.url == "https://www.workatastartup.com/"


@pytest.mark.parametrize(
    ("email", "password", "name", "reason"),
    [
        (TAKEN, "Right-Password-1", "Jane Doe", "email_in_use"),
        ("new@example.com", "short", "Jane Doe", "password_rule: Too short."),
        ("new@example.com", "Right-Password-1", "Jane", "sign_up_details"),
    ],
)
def test_what_sign_up_is_told(signing_in, page, email, password, name, reason):
    with pytest.raises(Refused, match=f"^{reason}"):
        signing_in.sign_up(page, email, password, SignUpDetails(name=name))


def step(page, name: str) -> WorkAtAStartup:
    adapter = WorkAtAStartup()
    page.goto(adapter.profile_url(None))
    assert adapter.profile_steps(page) == STEPS
    assert adapter.open_step(page, name)
    return adapter


def test_it_keeps_an_account_profile():
    assert isinstance(WorkAtAStartup(), KeepsProfile)


def test_a_step_the_wizard_does_not_load_yet(page, site):
    site.steps[1]["status"] = "locked"
    adapter = WorkAtAStartup()
    page.goto(adapter.profile_url(None))
    assert not adapter.open_step(page, "location")


@pytest.mark.parametrize(
    ("name", "prefilled"),
    [
        ("personal", {
            "first_name": "Jane", "last_name": "Doe", "email": "jane@example.com",
            "linkedin": "https://www.linkedin.com/in/jane-doe", "phone_country": "US/Canada (+1)",
            "phone_number": "2125550123", "yc_affiliation": "No affiliation",
            "share": "I'm not looking for a job / not ready to meet founders yet. HIDE MY PROFILE.",
        }),
        ("location", {
            "city_current": "New York, NY, USA", "us_authorized": "Yes", "relocate": "No",
            "us_visa_sponsorship": "Yes", "remote": "I'm open to working remotely",
        }),
        ("role", {
            "role": "Engineering", "eng_type": ["Backend"], "in_school": "No",
            "job_type": ["Full-time employee"], "experience": "5",
        }),
        ("experience", {
            "work_employer": "Acme Corp", "work_title": "Engineer", "work_from": "01/2020",
            "work_current": "Yes", "education_school": "State University", "education_field": "CS",
            "education_degree": "Bachelor", "education_from": "09/2015", "education_to": "06/2019",
        }),
        ("skills", {"skills": ["Python: Advanced", "PostgreSQL: Advanced"]}),
        ("career", {
            "company_sizes_1_10": "OK", "company_sizes_300_+": "OK",
            "equity_importance": "Some equity",
        }),
        ("share", {"short_phrase": "Backend engineer", "looking_for": "A small product team."}),
    ],
)  # fmt: skip
def test_each_step_lists_its_saved_values(page, site, name, prefilled):
    fields = step(page, name).profile_read(page)
    assert {f.id: f.prefilled for f in fields if f.prefilled is not None} == prefilled


def test_a_list_of_yc_companies_is_left_unfilled(page, site):
    fields = {f.id: f for f in step(page, "personal").profile_read(page)}
    assert fields["hidden_companies"].unfillable and not fields["hidden_companies"].required
    assert fields["share"].options == [
        "I'm actively looking for a job",
        "I'm open to new opportunities",
        "I'm not looking for a job / not ready to meet founders yet. HIDE MY PROFILE.",
    ]


@pytest.mark.parametrize(
    ("name", "answers"),
    [
        ("personal", {"share": "I'm open to new opportunities", "phone_country": "France",
                      "phone_number": "612345678", "first_name": "Janet"}),
        ("location", {"city_current": "Austin, TX, USA", "remote": "I only want to work remotely"}),
        ("role", {"eng_type": ["Frontend", "AI engineer"], "job_type": ["Contractor"],
                  "github": "https://github.com/jane", "experience": "6"}),
        ("experience", {"work_employer": "Acme Corp", "work_title": "Staff Engineer",
                        "work_current": "No", "work_to": "03/2024", "work_from": "02/2019",
                        "education_degree": "Master", "education_summary": "Thesis."}),
        ("skills", {"skills": ["Python: Intermediate", "Go: Beginner"]}),
        ("career", {"company_sizes_1_10": "Preferred",
                    "equity_importance": "Equity is very important to me"}),
        ("share", {"proud_project": "Test account for an open-source tool, please ignore."}),
    ],
)  # fmt: skip
def test_each_step_reads_back_what_it_is_filled_with(page, site, name, answers):
    adapter = step(page, name)
    fields = adapter.profile_read(page)
    adapter.profile_fill(page, answers)
    fields += [f for f in adapter.profile_read(page) if f.id not in {g.id for g in fields}]
    checks = matching.check(fields, answers, adapter.profile_read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    adapter.save(page)
    assert site.saved[-1]["from"] == name


def test_experience_is_filled_manually(page, site):
    site.profile["manual"] = False
    fields = step(page, "experience").profile_read(page)
    assert "work_employer" in {f.id for f in fields}


def test_a_step_the_system_refuses(page, site):
    site.save_status = 422
    adapter = step(page, "personal")
    with pytest.raises(Refused, match="^step_refused: personal: Phone number is an invalid number"):
        adapter.save(page)


def test_a_required_field_left_empty_posts_nothing(page, site, monkeypatch):
    monkeypatch.setattr(workatastartup, "SAVE_TIMEOUT_MS", 1_000)
    adapter = step(page, "share")
    adapter.profile_fill(page, {"short_phrase": ""})
    with pytest.raises(Refused, match="^step_refused: share"):
        adapter.save(page)
    assert site.saved == []
