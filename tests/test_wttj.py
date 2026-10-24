"""The Welcome to the Jungle adapter on tests/pages/wttj: a signed-in job page, and the sign-in
and sign-up forms."""

from pathlib import Path

import pytest

from atsfill.adapters import wttj
from atsfill.adapters.wttj import WelcomeToTheJungle
from atsfill.core import browser, matching
from atsfill.core.adapter import Blocked, Refused, Unsupported

PAGES = Path(__file__).parent / "pages/wttj"
PAGE = PAGES / "job.html"
JOB = "https://www.welcometothejungle.com/en/companies/acme/jobs/ai-engineer_paris"
CORS = {
    "access-control-allow-origin": "https://www.welcometothejungle.com",
    "access-control-allow-headers": "x-csrf-token",
}
TAKEN = "ada@example.com"  # the account that exists, its password `Right-Password-1`


def serve(route) -> None:
    request = route.request
    path = request.url.split("?")[0]
    if request.method == "OPTIONS":
        route.fulfill(status=204, headers=CORS)
    elif path.endswith(("/api/v1/sessions", "/api/v3/registrations")):
        status = _account(request)
        taken = {"errors": {"email": [{"options": {"validation": "unsafe_unique"}}]}}
        route.fulfill(status=status, headers=CORS, json=taken if status == 422 else {})
    elif path.endswith("/api/v1/applications"):
        route.fulfill(status=201, json={"application": {"reference": "REF-1"}})
    elif path.endswith(("/en/signin", "/en/authenticate/signup")):
        route.fulfill(path=PAGES / "account.html")
    elif path.endswith("/en/busy"):
        route.fulfill(status=429, body="Too Many Requests", content_type="text/plain")
    elif "/complete-profile/" in path:
        route.fulfill(body="<!doctype html><title>Onboarding</title>", content_type="text/html")
    else:
        route.fulfill(path=PAGE)


def _account(request) -> int:
    """403 without the page's CSRF token; 401 for a wrong password, 422 for a taken email."""
    if not request.headers.get("x-csrf-token"):
        return 403
    sent = request.post_data_json
    if request.url.endswith("/sessions"):
        return 201 if (sent["email"], sent["password"]) == (TAKEN, "Right-Password-1") else 401
    return 422 if sent["email"] == TAKEN else 201


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://www.welcometothejungle.com/**", serve)
        page.context.route("https://api.welcometothejungle.com/**", serve)
        yield page


@pytest.fixture
def opened(page) -> tuple[WelcomeToTheJungle, list]:
    adapter = WelcomeToTheJungle()
    page.goto(JOB)
    return adapter, adapter.read_form(page)


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def test_read_form_waits_for_the_account(opened):
    _, fields = opened
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "firstname",
        "phone",
        "location",
        "resume",
        "portfolio",
        "questions.ab12",
        "consent",
    ]  # the account's email is not the form's to change
    assert by_id["questions.ab12"].label == "Why this role?"
    assert by_id["resume"].required and by_id["resume"].accept == ["application/pdf"]
    prefilled = {f.id: f.prefilled for f in fields if f.prefilled is not None}
    assert prefilled == {  # from the account profile
        "firstname": "Ada",
        "phone": "+33 6 00 00 00 00",
        "location": "Lyon, France",
        "resume": "profile-resume.pdf",
    }


def test_every_answer_reads_back(opened, page, resume, monkeypatch):
    monkeypatch.setattr(wttj, "SUGGEST_TIMEOUT_S", 0.5)  # the location is typed twice
    adapter, fields = opened
    answers = {
        "firstname": "Ada",
        "location": "Paris, France",
        "resume": resume,
        "portfolio": resume,
        "questions.ab12": "Because.",
        "consent": "Yes",
    }
    adapter.fill(page, answers)
    read_back = adapter.read_back(page)
    checks = matching.check(fields, answers, read_back)
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert read_back["phone"] == "+33 6 00 00 00 00"  # unanswered, left as the account has it


def test_submit_returns_the_reference(opened, page):
    adapter, _ = opened
    assert adapter.submit(page) == "POST /api/v1/applications 201, reference REF-1"


def test_detection_and_posting():
    adapter = WelcomeToTheJungle()
    assert adapter.detect(JOB + "?fromApply=true", None)
    assert adapter.detect("https://easyapply.jobs/r/abc", None)
    assert not adapter.detect("https://www.welcometothejungle.com/en/companies/acme", None)
    expected = "https://www.welcometothejungle.com/companies/acme/jobs/ai-engineer_paris"
    assert adapter.posting(JOB + "?igbTracker=1") == expected


def test_an_unknown_input_is_listed_unhandled():
    raw = {"id": "questions.cd34", "type": "radio", "label": "Remote?", "required": True}
    field = wttj._field(raw)
    assert field and field.unfillable == "unhandled_rendering: 'Remote?' is a radio input"
    assert wttj._field({**raw, "type": "hidden"}) is None


@pytest.fixture
def signing_in(page) -> WelcomeToTheJungle:
    page.context.clear_cookies()
    adapter = WelcomeToTheJungle()
    page.goto(adapter.login_url(None))
    return adapter


def test_sign_in_waits_for_the_csrf_token(signing_in, page):
    signing_in.sign_in(page, TAKEN, "Right-Password-1")
    assert "/complete-profile/" in page.url


def test_a_wrong_password_is_refused(signing_in, page):
    with pytest.raises(Refused, match="^wrong_password"):
        signing_in.sign_in(page, TAKEN, "Wrong-Password-1")


def test_a_login_page_answering_too_many_requests_blocks(page, monkeypatch):
    monkeypatch.setattr(wttj, "ACCOUNT_TIMEOUT_MS", 1_000)
    page.goto("https://www.welcometothejungle.com/en/busy")
    with pytest.raises(Blocked, match="^rate_limited"):
        WelcomeToTheJungle().sign_in(page, TAKEN, "Right-Password-1")


def test_sign_up_waits_for_the_confirmation_link(signing_in, page):
    with pytest.raises(Blocked, match="^email_link"):
        signing_in.sign_up(page, "new@example.com", "Right-Password-1", "Jane")
    assert "/complete-profile/" in page.url  # created, signed in


@pytest.mark.parametrize(
    ("email", "password", "name", "error", "reason"),
    [
        (TAKEN, "Right-Password-1", "Jane", Refused, "email_in_use"),
        ("new@example.com", "short", "Jane", Refused, "password_rule: Your password must"),
        ("new@example.com", "Right-Password-1", "", Unsupported, "unhandled_rendering"),
    ],
)
def test_what_sign_up_is_told(signing_in, page, email, password, name, error, reason):
    with pytest.raises(error, match=f"^{reason}"):
        signing_in.sign_up(page, email, password, name)
