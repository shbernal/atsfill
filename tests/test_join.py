"""The JOIN adapter on tests/pages/join: a signed-in posting, its wizard, the account page and
the account profile's form."""

from pathlib import Path

import pytest

from atsfill.adapters import join
from atsfill.adapters.join import Join
from atsfill.core import browser, matching
from atsfill.core.adapter import Blocked, KeepsProfile, ProfileIncomplete, Refused, SignUpDetails
from atsfill.core.results import Answer, FieldType

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
SAVED = {"type": "candidate", "firstName": "Ada", "lastName": "Placeholder", "email": TAKEN,
         "phoneNumber": "+33639980000", "country": "France", "city": "Paris"}  # fmt: skip
ACCOUNT = dict(SAVED)  # the account profile, as `api/me` answers it and the form saves it


@pytest.fixture(autouse=True)
def account():
    ACCOUNT.clear()
    ACCOUNT.update(SAVED)
    return ACCOUNT


def serve(route) -> None:
    request = route.request
    url = request.url
    if url.endswith("/api/me"):
        route.fulfill(json=ACCOUNT)
    elif "operationName=UpdateTalentPersonalInformation" in url:
        sent = request.post_data_json["variables"]["input"]
        if ACCOUNT["phoneNumber"] and not sent["phoneNumber"]:
            route.fulfill(json={"errors": [{"message": "Phone number is invalid: "}]})
            return
        ACCOUNT.update(sent)
        route.fulfill(json={"data": {"updateCandidatePersonalInfo": {"id": 1}}})
    elif "operationName=" in url:
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
    elif "/talent/me" in url:
        route.fulfill(path=PAGES / "profile.html")
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


def test_it_keeps_an_account_profile():
    assert isinstance(Join(), KeepsProfile)


def profile(page) -> Join:
    adapter = Join()
    page.goto(adapter.profile_url(None))
    assert adapter.profile_steps(page) == ["general"]
    assert adapter.open_step(page, "general")
    return adapter


def test_the_profile_form_lists_its_saved_values(page):
    fields = {f.id: f for f in profile(page).profile_read(page)}
    assert {k: f.prefilled for k, f in fields.items()} == {
        "first_name": "Ada", "last_name": "Placeholder", "phone": "+33639980000",
        "country": "France", "city": "Paris", "linkedin": None,
    }  # fmt: skip
    assert fields["country"].options == ["Belgium", "France", "Germany"]
    assert fields["first_name"].max_length == 30


def test_the_profile_form_reads_back_what_it_is_filled_with_and_saves(page, account):
    adapter = profile(page)
    fields = adapter.profile_read(page)
    answers: dict[str, Answer] = {
        "first_name": "Jane",
        "phone": "+1 212 555 0123",  # +1, not American Samoa's +1684
        "country": "Germany",
        "city": "Berlin",
        "linkedin": "https://www.linkedin.com/in/jane-doe",
    }
    adapter.profile_fill(page, answers)
    checks = matching.check(fields, answers, adapter.profile_read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    adapter.save(page)
    assert account["phoneNumber"] == "+12125550123"
    assert [account[k] for k in ("firstName", "country", "city")] == ["Jane", "Germany", "Berlin"]


def test_a_city_change_alone_keeps_save_disabled(page, monkeypatch):
    monkeypatch.setattr(join, "BUTTON_TIMEOUT_MS", 500)
    adapter = profile(page)
    adapter.profile_fill(page, {"city": "Lyon"})
    assert adapter.profile_read_back(page)["city"] == "Lyon"
    with pytest.raises(Refused, match="^step_refused: general: the form keeps its Save button"):
        adapter.save(page)


def test_a_saved_phone_cannot_be_emptied(page):
    adapter = profile(page)
    page.locator('[id="candidate.phoneNumber"]').fill("")
    with pytest.raises(Refused, match="^step_refused: general: Phone number is invalid"):
        adapter.save(page)


def test_a_phone_without_its_country_code_is_not_taken(page):
    with pytest.raises(join.NotTaken, match="no country code"):
        profile(page).profile_fill(page, {"phone": "0639980000"})


def test_a_job_asking_what_the_profile_lacks_names_it(page, account, files, monkeypatch):
    monkeypatch.setattr(join, "STEP_TIMEOUT_MS", 1_000)
    account["phoneNumber"] = ""
    adapter = Join()
    page.goto(JOB)
    adapter.browser_read(page)
    with pytest.raises(ProfileIncomplete, match="^the job asks for a phone$"):
        adapter.fill(page, {"cv": files[0]})
    assert page.url.endswith("/apply/personalInformation")

    saving = profile(page)
    saving.profile_fill(page, {"phone": "+33 6 39 98 00 00"})
    saving.save(page)
    adapter = Join()
    page.goto(JOB)
    adapter.browser_read(page)
    with pytest.raises(join.NotTaken, match="^the question-1 step"):  # past personalInformation
        adapter.fill(page, {"cv": files[0]})
