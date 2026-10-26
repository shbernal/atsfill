"""The JOIN adapter on tests/pages/join: a signed-in posting, its wizard, the account page."""

from pathlib import Path

import pytest

from atsfill.adapters import join
from atsfill.adapters.join import Join
from atsfill.core import browser, matching
from atsfill.core.adapter import Blocked, Refused, SignUpDetails
from atsfill.core.results import FieldType

PAGES = Path(__file__).parent / "pages/join"
JOB = "https://join.com/companies/acme/101-ai-engineer"
TAKEN = "ada@example.com"  # the account that exists, its password `Right-Password-1`
UNCONFIRMED = "new@example.com"
QUESTIONS = [
    {"id": 1, "question": "Your level of English?", "options": ["None", "Fluent"],
     "type": "SINGLE_CHOICE", "isRequired": True, "position": 1},
    {"id": 2, "question": "Do you hold a work permit?", "options": None, "type": "BOOLEAN",
     "isRequired": True, "position": 2},
    {"id": 3, "question": "When can you start?", "options": None, "type": "DATE",
     "isRequired": True, "position": 3},
    {"id": 4, "question": "Your salary expectation?", "options": None, "type": "INTEGER",
     "isRequired": False, "position": 4},
]  # fmt: skip


def serve(route) -> None:
    request = route.request
    url = request.url
    if "operationName=" in url:
        operation = url.split("operationName=")[1]
        sent = (request.post_data_json or {}).get("variables", {}).get("input", {})
        body = {
            "JobScreeningQuestions": {"data": {"jobScreeningQuestions": {"questions": QUESTIONS}}},
            "CheckCandidate": {"data": {"checkCandidate": {"exists": sent.get("email") in (
                TAKEN, UNCONFIRMED)}}},
            "LoginWithCredentials": {"data": {}, "unverified": sent.get("email") == UNCONFIRMED}
            if sent.get("password") == "Right-Password-1" else {"errors": [{"message": "no"}]},
            "RegisterCandidate": {"data": {"registerCandidate": {"id": 1}}},
            "ApplyToJob": {"data": {"applyToJob": {"id": 1}}},
        }[operation]  # fmt: skip
        route.fulfill(json=body)
    elif "/auth/login/candidate" in url:
        route.fulfill(path=PAGES / "account.html")
    elif "/auth/candidates/" in url or "/talent/" in url:
        route.fulfill(body="<!doctype html><title>Account</title>", content_type="text/html")
    else:
        route.fulfill(path=PAGES / "job.html")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://join.com/**", serve)
        yield page


@pytest.fixture
def opened(page) -> tuple[Join, list]:
    adapter = Join()
    page.goto(JOB)
    return adapter, adapter.browser_read(page)


@pytest.fixture
def files(tmp_path) -> tuple[str, str]:
    paths = tmp_path / "CV Ada.pdf", tmp_path / "Letter.pdf"
    for path in paths:
        path.write_bytes(b"%PDF-1.4")
    return str(paths[0]), str(paths[1])


def test_browser_read_lists_the_files_and_the_questions(opened, page):
    _, fields = opened
    assert [(f.id, f.type, f.required) for f in fields] == [
        ("cv", FieldType.FILE, True),
        ("question.1", FieldType.CHOICE, True),
        ("question.2", FieldType.CHOICE, True),
        ("question.3", FieldType.DATE, True),
        ("question.4", FieldType.TEXT, False),
    ]
    assert fields[2].options == ["Yes", "No"]
    assert fields[4].label == "Your salary expectation? (a number)"
    assert page.url.endswith("/apply/cv")  # the wizard left `authentication` on its own


def test_every_answer_reads_back_from_the_review_page(opened, page, files):
    adapter, fields = opened
    answers = {
        "cv": files[0],
        "question.1": "Fluent",
        "question.2": "Yes",
        "question.3": "2026-03-15",
        "question.4": "50000",
    }
    adapter.fill(page, answers)
    assert page.url.endswith("/apply/review")
    read_back = adapter.read_back(page)
    checks = matching.check(fields, answers, read_back)
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert adapter.submit(page) == "POST /candidate-api/graphql?operationName=ApplyToJob 200"


def test_a_required_step_left_unanswered_is_not_taken(opened, page):
    adapter, _ = opened
    with pytest.raises(join.NotTaken, match="cv step keeps Continue disabled"):
        adapter.fill(page, {})


def test_detection_and_posting():
    adapter = Join()
    assert adapter.detect(JOB + "?utm_source=x", None)
    assert adapter.detect("https://join.com/companies/acme/101/apply/cv", None)
    assert not adapter.detect("https://join.com/companies/acme", None)
    assert adapter.posting(JOB) == adapter.posting("https://join.com/companies/acme/101/apply")


def test_an_unknown_question_type_is_listed_unfillable():
    question = {**QUESTIONS[0], "type": "MULTIPLE_CHOICE"}
    field = join._question(question)
    assert (
        field.unfillable
        == "unhandled_rendering: 'Your level of English?' is a MULTIPLE_CHOICE question"
    )


@pytest.fixture
def signing_in(page) -> Join:
    page.context.clear_cookies()
    adapter = Join()
    page.goto(adapter.login_url(None))
    return adapter


def test_sign_in(signing_in, page):
    signing_in.sign_in(page, TAKEN, "Right-Password-1")
    assert page.url.endswith("/talent/home")


@pytest.mark.parametrize(
    ("email", "password", "reason"),
    [(TAKEN, "Wrong-Password-1", "wrong_password"), ("nobody@example.com", "x", "wrong_password")],
)
def test_a_sign_in_refused(signing_in, page, email, password, reason):
    with pytest.raises(Refused, match=f"^{reason}"):
        signing_in.sign_in(page, email, password)


def test_an_unconfirmed_account_waits_for_the_link(signing_in, page):
    with pytest.raises(Blocked, match="^email_link"):
        signing_in.sign_in(page, UNCONFIRMED, "Right-Password-1")


def test_sign_up_waits_for_the_confirmation_link(signing_in, page):
    with pytest.raises(Blocked, match="^email_link"):
        signing_in.sign_up(
            page, "jane@example.com", "Right-Password-1", SignUpDetails(name="Jane Doe")
        )


def test_sign_up_with_a_taken_email_is_refused(signing_in, page):
    with pytest.raises(Refused, match="^email_in_use"):
        signing_in.sign_up(page, TAKEN, "Right-Password-1", SignUpDetails(name="Jane Doe"))


def test_sign_up_with_a_short_password_is_refused(signing_in, page, monkeypatch):
    monkeypatch.setattr(join, "BUTTON_TIMEOUT_MS", 300)
    with pytest.raises(Refused, match="^password_rule"):
        signing_in.sign_up(page, "jane@example.com", "short", SignUpDetails(name="Jane Doe"))
