"""The Workday adapter on tests/pages/workday, served as an Acme tenant's career site."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from atsfill.adapters import workday as workday_module
from atsfill.adapters.workday import Workday
from atsfill.core import browser, matching
from atsfill.core.adapter import Blocked, NotTaken, Refused, Unsupported
from atsfill.core.results import FieldType
from tests import testuser

PAGES = Path(__file__).parent / "pages/workday"
SITE = "https://acme.wd5.myworkdayjobs.com/en-US/Careers"
JOB = f"{SITE}/job/London-UK/Engineer_R123-1"
SHARED = "https://wd3.myworkdaysite.com/recruiting/acme/Careers/job/Sydney/Engineer_R123-1"


class Profiles:
    """A request carrying cookies for some hosts, answering `userprofile` for the signed-in ones."""

    def __init__(self, hosts: list[str], signed_in: set[str]) -> None:
        self.hosts, self.signed_in, self.asked = hosts, signed_in, []

    def storage_state(self) -> dict:
        return {"cookies": [{"domain": h} for h in self.hosts]}

    def get(self, url: str):
        self.asked.append(url)
        signed_in = any(f"//{h}/" in url for h in self.signed_in)
        return SimpleNamespace(ok=True, json=lambda: {"emailAddress": "a@x"} if signed_in else {})


APPLY = f"{JOB}/apply/applyManually"
DEFINITION_MS = 1_000
API = {
    "/wday/cxs/acme/Careers/job/Engineer_R123-1": "job.json",
    "/applyflowpages": "applyflowpages.json",
    "/questionnaire/qn1": "questionnaire.json",
    "/jobapplication/acme/values/phone/countryCodes": "countryCodes.json",
    "/jobapplication/acme/values/educations/degrees": "degrees.json",
    "/jobapplication/acme/schools": "schools.json",
}


def serve(route) -> None:
    path = route.request.url.split("?")[0]
    if path.endswith("/definition"):  # the reply comes once the next answer is given
        route.request.frame.page.wait_for_timeout(DEFINITION_MS)
        route.fulfill(path=PAGES / "questionnaire.json", content_type="application/json")
        return
    if json_file := next((f for end, f in API.items() if path.endswith(end)), None):
        route.fulfill(path=PAGES / json_file, content_type="application/json")
        return
    if path.endswith("/apply/applyManually"):
        route.fulfill(path=PAGES / "apply.html")
        return
    email = route.request.post_data or ""
    if path.endswith("/register") and email == "ada@example.com":
        route.fulfill(status=303, headers={"location": path.replace("/register", "/login")})
    elif path.endswith("/register") and email.endswith("@verify.example"):
        warning = path.replace("/register", "/login/warning?redirect=%2FCareers%2FuserHome")
        route.fulfill(status=303, headers={"location": warning})
    elif path.endswith("/login"):
        route.fulfill(path=PAGES / "login.html")
    else:
        route.fulfill(body="<!doctype html><title>Acme Careers</title>", content_type="text/html")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://acme.wd5.myworkdayjobs.com/**", serve)
        yield page


@pytest.fixture
def workday(page, monkeypatch) -> Workday:
    monkeypatch.setattr(workday_module, "PRESS_TIMEOUT_MS", 1_000)
    page.context.clear_cookies()
    adapter = Workday()
    page.goto(adapter.login_url(adapter.account_scope(JOB)))
    return adapter


def test_every_url_of_a_posting_has_one_key_and_the_tenant_as_scope():
    adapter = Workday()
    urls = [JOB, f"{JOB}/apply/applyManually", JOB.replace("/en-US", "")]
    assert {adapter.posting(u) for u in urls} == {
        "https://acme.wd5.myworkdayjobs.com/Careers/details/Engineer_R123-1"
    }
    assert (
        adapter.posting(SHARED)
        == "https://acme.wd3.myworkdayjobs.com/Careers/details/Engineer_R123-1"
    )
    assert {adapter.account_scope(u) for u in [*urls, SHARED]} == {"acme"}
    assert adapter.detect(JOB, None) and adapter.detect(SHARED, None)
    assert not adapter.detect(SITE, None)  # the site's search, not a posting


def test_the_login_page_is_the_last_postings_site():
    adapter = Workday()
    adapter.account_scope(SHARED)
    assert (
        adapter.login_url("acme") == "https://wd3.myworkdaysite.com/recruiting/acme/Careers/login"
    )


def signed_in(page) -> bool:
    return page.url == "https://acme.wd5.myworkdayjobs.com/Careers" and any(
        c["name"] == "PLAY_SESSION" for c in page.context.cookies()
    )


def test_signed_in_asks_the_tenants_hosts_from_the_session():
    adapter = Workday()
    hosts = [".acme.wd5.myworkdayjobs.com", "other.wd5.myworkdayjobs.com", "wd3.myworkdaysite.com"]
    request = Profiles(hosts, signed_in={"wd3.myworkdaysite.com"})
    assert adapter.signed_in(request, "acme")  # ty: ignore[invalid-argument-type]
    assert request.asked == [
        "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/x/userprofile",
        "https://wd3.myworkdaysite.com/wday/cxs/acme/x/userprofile",
    ]  # never another tenant's host
    assert not adapter.signed_in(Profiles(hosts, set()), "acme")  # ty: ignore[invalid-argument-type]


def test_sign_in_through_the_overlay(workday, page):
    workday.sign_in(page, "ada@example.com", "Right-123!")
    assert signed_in(page)


def test_the_email_form_behind_social_accounts(workday, page):
    page.goto(f"{page.url}?social")
    workday.sign_in(page, "ada@example.com", "Right-123!")
    assert signed_in(page)


def test_a_wrong_password_is_refused(workday, page):
    with pytest.raises(Refused, match="^wrong_password"):
        workday.sign_in(page, "ada@example.com", "Wrong-123!")


def test_an_account_not_verified_yet_blocks(workday, page):
    with pytest.raises(Blocked, match="^email_link"):
        workday.sign_in(page, "unverified@example.com", "Right-123!")


def test_sign_up(workday, page):
    workday.sign_up(page, "new@example.com", "Right-123!", None)
    assert signed_in(page)


@pytest.mark.parametrize(
    ("email", "password", "error", "reason"),
    [
        ("new@example.com", "short", Refused, "password_rule"),
        ("ada@example.com", "Right-123!", Refused, "email_in_use"),
        ("new@verify.example", "Right-123!", Blocked, "email_link"),
    ],
)
def test_what_sign_up_is_told(workday, page, email, password, error, reason):
    with pytest.raises(error, match=f"^{reason}"):
        workday.sign_up(page, email, password, None)


@pytest.fixture
def opened(page) -> tuple[Workday, list]:
    """A fresh draft: the read stops at My Information, which does not save empty."""
    page.goto(JOB)
    page.evaluate("sessionStorage.clear()")
    adapter = Workday()
    return adapter, adapter.read_form(page)


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


ANSWERS = {
    "name--legalName--firstName": "Ada",
    "name--preferredCheck": "No",
    "previousWorker--candidateIsPreviousWorker": "No",
    "source--source": "Job Fair (Acme)",  # in the list, not found by a search
    "phoneNumber--phoneNumber": "+44 1234 567890",
    "primaryQuestionnaire--q1": "Yes",
    "primaryQuestionnaire--q2": ["English", "French"],
    "primaryQuestionnaire--q3": "Because.",
    "primaryQuestionnaire--q4": "2026-11-02",
    "workExperience--jobTitle": "Engineer",
    "workExperience--companyName": "Initech",
    "workExperience--currentlyWorkHere": "Yes",
    "workExperience--startDate": "03/2021",
    "education--school": "University of Acme",
    "education--degree": "Master's Degree",
}


def test_a_fresh_draft_lists_the_pages_up_to_the_first_it_cannot_save(opened):
    _, fields = opened
    by_id = {f.id: f for f in fields}
    assert list(by_id)[:7] == [
        "country--country",
        "name--legalName--firstName",
        "name--preferredCheck",
        "previousWorker--candidateIsPreviousWorker",
        "source--source",
        "phoneNumber--countryPhoneCode",
        "phoneNumber--phoneNumber",
    ]
    assert by_id["country--country"].options == ["United Kingdom", "United States of America"]
    assert by_id["country--country"].required and by_id["country--country"].prefilled
    assert by_id["name--preferredCheck"].options == ["Yes", "No"]
    assert by_id["previousWorker--candidateIsPreviousWorker"].type is FieldType.CHOICE
    assert by_id["source--source"].type is FieldType.CHOICE
    assert by_id["source--source"].options == [
        "LinkedIn",
        "Indeed",
        "Linked Jobs",
        "Job Fair (Acme)",
        "Corporate Website",
        "Employee Referral",
    ]
    code = by_id["phoneNumber--countryPhoneCode"]
    assert code.options == ["United Kingdom (+44)", "United States of America (+1)"]  # the API's
    assert code.type is FieldType.CHOICE and not code.prefilled
    assert by_id["phoneNumber--phoneNumber"].type is FieldType.PHONE
    assert by_id["resumeAttachments--attachments"].required  # from the apply flow's sections
    entry = [i for i in by_id if i.startswith("workExperience--")]
    assert entry == [
        "workExperience--jobTitle",
        "workExperience--companyName",
        "workExperience--location",
        "workExperience--currentlyWorkHere",
        "workExperience--startDate",
        "workExperience--endDate",
    ]  # the first entry; Role Description is hidden
    assert by_id["workExperience--jobTitle"].required
    assert not by_id["workExperience--location"].required  # shown, not required
    assert by_id["workExperience--startDate"].label == "Work Experience: From (MM/YYYY)"
    school = by_id["education--school"]  # the tenant lists schools
    assert school.type is FieldType.TYPEAHEAD and not school.required  # Education is optional
    assert by_id["education--degree"].options == ["Bachelor's Degree", "Master's Degree"]
    assert by_id["skills--skills"].type is FieldType.TYPEAHEAD
    assert by_id["myExperience--Websites"].unfillable == (
        "unsupported_flow: experience_entries: Websites is a list of entries"
    )
    assert not any("Languages" in i or "Certification" in i for i in by_id)  # hidden; a part
    assert by_id["primaryQuestionnaire--q2"].type is FieldType.MULTI_CHOICE
    assert by_id["primaryQuestionnaire--q4"].type is FieldType.DATE
    follow_up = by_id["primaryQuestionnaire--q5"]
    assert not follow_up.required
    assert follow_up.label == (
        'Which visa do you need? (asked when "Are you authorized to work in the UK?" is "No")'
    )
    assert by_id["primaryQuestionnaire--q6"].label == "Years of experience (a number)"
    assert "personalInfoPerson--gender" not in by_id  # behind pages that do not save yet


def test_the_disclosures_are_read_once_the_pages_before_them_are_saved(opened, page, resume):
    adapter, _ = opened
    with pytest.raises(
        NotTaken,
        match="^Voluntary Disclosures shows fields the read did not: Gender, I Agree; read again$",
    ):
        adapter.fill(page, ANSWERS | {"resumeAttachments--attachments": resume})
    page.goto(JOB)
    adapter = Workday()
    fields = adapter.read_form(page)
    by_id = {f.id: f for f in fields}
    assert by_id["personalInfoPerson--gender"].options == ["Female", "Male", "Not declared"]
    assert by_id["termsAndConditions--acceptTermsAndAgreements"].required
    answers = ANSWERS | {
        "resumeAttachments--attachments": resume,
        "personalInfoPerson--gender": "Not declared",
        "termsAndConditions--acceptTermsAndAgreements": "Yes",
    }
    adapter.fill(page, answers)
    assert (
        page.locator("[data-automation-id=progressBarActiveStep]").inner_text().endswith("Review")
    )
    read_back = adapter.read_back(page)
    checks = matching.check(fields, answers, read_back)
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert read_back["resumeAttachments--attachments"] == "CV Ada.pdf"  # the draft's is deleted
    assert read_back["phoneNumber--phoneNumber"] == "+44 1234567890"  # typed without the code
    assert read_back["country--country"] == "United Kingdom"  # unanswered, as the draft holds it


def test_another_country_renders_fields_the_read_did_not(opened, page):
    adapter, _ = opened
    answers = {"country--country": "United States of America"}
    with pytest.raises(Unsupported, match="^unsupported_flow: other_country: .*: State$"):
        adapter.fill(page, answers)


def test_a_follow_up_an_answer_opens_on_my_information_is_named(opened, page):
    adapter, _ = opened
    with pytest.raises(
        NotTaken, match="^My Information shows fields the read did not: What's their name.; an"
    ):
        adapter.fill(page, {"source--source": "Employee Referral"})


def test_a_follow_up_of_a_follow_up_names_its_question_as_listed():
    yes_no = "Multiple Choice - Single Select"
    items = [
        {"id": "a", "body": "Held a public office?", "required": True,
         "type": {"descriptor": yes_no},
         "possibleAnswers": [{"answerText": "Yes", "branchingQuestion": {"id": "b"}},
                             {"answerText": "No"}]},
        {"id": "b", "body": "<p>In the last two years?</p>", "required": True,
         "type": {"descriptor": yes_no},
         "possibleAnswers": [{"answerText": "Yes", "branchingQuestion": {"id": "c"}},
                             {"answerText": "No", "branchingQuestion": {"id": "c"}}]},
        {"id": "c", "body": "Which one?", "required": True, "type": {"descriptor": "Text"}},
    ]  # fmt: skip
    fields = {f.id: f for f in workday_module._questions("primary", items)}
    b, c = fields["primaryQuestionnaire--b"], fields["primaryQuestionnaire--c"]
    assert b.label == 'In the last two years? (asked when "Held a public office?" is "Yes")'
    assert c.label == f'Which one? (asked when "{b.label}" is "Yes or No")'
    assert not b.required and not c.required
    answers = testuser.answers(list(fields.values()), Path("cv.pdf"))
    assert answers["primaryQuestionnaire--a"] == "Yes"
    assert "primaryQuestionnaire--c" in answers  # the Test user reads the nested label
