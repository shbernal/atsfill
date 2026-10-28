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
    NotTaken,
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


class Site:
    """What the routes serve: the job page with `replace` applied, `/apply`'s answer, and
    what was posted to it."""

    def __init__(self) -> None:
        self.replace: dict[str, str] = {}
        self.applied: dict = {"company_id": 40, "job_ids": [1001]}
        self.posted: list[dict] = []

    def serve(self, route) -> None:
        request = route.request
        path = request.url.split("?")[0]
        if path.startswith("https://account.ycombinator.com/"):
            return self._account(route)
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


def test_a_short_message_is_not_taken(page, site):
    adapter, _ = opened(page)
    with pytest.raises(NotTaken, match="^message"):
        adapter.fill(page, {**ANSWERS, "message": "Hi."})


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
