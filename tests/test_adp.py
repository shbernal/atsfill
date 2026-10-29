"""The ADP Workforce Now adapter on tests/pages/adp/app.html, with ADP's API served here: the
sign-in, the talent community step, and the wizard of an employer with a questionnaire of each
answer type, the desired pay, and Voluntary Self-ID."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from atsfill.adapters.adp import ADP
from atsfill.core import browser, otp
from atsfill.core.adapter import NotTaken, Refused, SignedOut, SignUpDetails, Unsupported
from atsfill.core.results import FieldType

PAGE = Path(__file__).parent / "pages/adp/app.html"
HOST = "https://workforcenow.adp.com"
CENTER = f"{HOST}/mascsr/default/mdf/recruitment/recruitment.html?cid=acme&ccId=19000101_000001"
JOB = f"{CENTER}&jobId=5001_1&lang=en_US"
SCOPE = "acme.19000101_000001"


def yes_no(n: int, text: str, required: bool = True) -> dict:
    options = [{"answerID": "Yes", "answerValue": "Y"}, {"answerID": "No", "answerValue": "N"}]
    return {"questionID": f"q{n}", "answerTypeCode": "Yes/No Buttons", "questionText": text,
            "requiredIndicator": required, "answerOptions": options}  # fmt: skip


def listed(*labels: str) -> list[dict]:
    return [{"answerID": label, "answerValue": label} for label in labels]


QUESTIONS = [
    yes_no(1, "Are you 18 years of age or older?"),
    {"questionID": "q2", "answerTypeCode": "Comment", "questionText": "Who referred you?",
     "requiredIndicator": False, "answerOptions": []},
    {"questionID": "q3", "answerTypeCode": "Text Field", "questionText": "Your current title?",
     "requiredIndicator": True, "answerOptions": []},
    {"questionID": "q4", "answerTypeCode": "Number Field", "questionText": "Years of experience?",
     "requiredIndicator": False, "answerOptions": []},
    {"questionID": "q5", "answerTypeCode": "Drop List", "questionText": "How did you hear?",
     "requiredIndicator": True, "answerOptions": listed("Indeed", "Website")},
    {"questionID": "q6", "answerTypeCode": "Multi-Select Menu", "questionText": "Shifts?",
     "requiredIndicator": False, "answerOptions": listed("Morning", "Evening", "Weekends")},
    {"questionID": "q7", "answerTypeCode": "Money Field", "questionText": "Desired pay?",
     "requiredIndicator": True,
     "answerOptions": [{"answerID": "USD", "answerValue": "United States Dollar (USD)"},
                       {"answerID": "CAD", "answerValue": "Canada Dollar (CAD)"}]},
    {"questionID": "q8", "answerTypeCode": "Date Field", "questionText": "Available from?",
     "requiredIndicator": True, "answerOptions": []},
    {"questionID": "q9", "answerTypeCode": "Signature Pad", "questionText": "Sign here",
     "requiredIndicator": False, "answerOptions": []},
]  # fmt: skip


def codes(*items: tuple[str, str]) -> dict:
    return {"codeList": {"listItems": [{"codeValue": c, "longName": n} for c, n in items]}}


PERSON = "/jobApplication/applicant/person"
TRANSFORMS = {
    "/jobApplication/requestedPayRate": {"optional": False, "hidden": False},
    "/jobApplication/requestedPayRate/annualRateAmount/nameCode": codes(
        ("AN", "Annually"), ("HR", "Hourly")
    ),
    "/jobApplication/requestedPayRate/annualRateAmount/currencyCode": codes(
        ("USD", "United States Dollar"), ("CAD", "Canada Dollar")
    ),
    "/jobApplication/comment": {"optional": True, "hidden": False},
    f"{PERSON}/address/countrySubdivisionLevel1": codes(
        ("AL", "US-Alabama"), ("DC", "US-District of Columbia")
    ),
    f"{PERSON}/genderCode": codes(("M", "Man/Male"), ("P", "Prefer not to say")),
    f"{PERSON}/ethnicityCode": codes(("3", "Hispanic or Latino"), ("4", "Not Hispanic or Latino")),
    f"{PERSON}/raceCode": codes(("4", "Asian"), ("1", "White")),
    f"{PERSON}/militaryStatusCode": codes(
        ("X", "I am a protected veteran, but I choose not to self-identify"),
        ("N", "I am not a protected veteran"),
        ("D", "I decline to self-identify"),
        ("Y", "I identify myself as a protected veteran"),
    ),
    f"{PERSON}/disabilityTypeCodes": codes(("D", "I Don't Wish To Answer"), ("N", "No")),
}
INDICATORS = {"VSID": True, "GENDER": True, "RACE": True, "VSID_VeteranEnabledIndicator": True,
              "VSID_DisabilityEnabledIndicator": False}  # fmt: skip


def meta(requisition: str | None) -> dict:
    """The wizard's meta; its questionnaire only for the posting its header names."""
    indicators = [{"nameCode": {"codeValue": k}, "indicatorValue": v}
                  for k, v in INDICATORS.items()]  # fmt: skip
    control = {"attestations": [{"messageTxt": "I certify my answers are true."}],
               "customIndicators": indicators}  # fmt: skip
    if requisition == "5001_1":
        control["questionnaire"] = {"questions": QUESTIONS}
    return {"meta": {"/data/transforms": [TRANSFORMS]}, "data": {"control": control}}


class Ats:
    """ADP's API for one candidate: the drafts Apply opened and the uploads."""

    def __init__(self) -> None:
        self.drafts = 0
        self.uploads = 0

    def answer(self, route) -> None:
        request = route.request
        path = urlsplit(request.url).path
        if path.endswith("/job-applicants"):
            if "SMSESSION" not in (request.headers.get("cookie") or ""):
                route.fulfill(status=302, headers={"location": "/workforcenow/login.html"})
                return
            route.fulfill(json={"jobApplicants": [{"applicantID": "1"}]})
        elif path.endswith("/job-application.submit/meta"):
            route.fulfill(json=meta(request.headers.get("requisitionoid")))
        elif path.endswith("/job-application.submit"):
            self.drafts += 1
            route.fulfill(json={})
        elif path.endswith("/document.upload"):
            self.uploads += 1
            route.fulfill(json={})
        else:
            route.fulfill(path=PAGE)


@pytest.fixture
def ats() -> Ats:
    return Ats()


@pytest.fixture
def page(tmp_path_factory, ats):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route(f"{HOST}/**", ats.answer)
        yield page


@pytest.fixture
def signed_in(page):
    page.context.add_cookies([{"name": "SMSESSION", "value": "ok", "url": HOST}])
    return page


@pytest.fixture
def opened(signed_in) -> tuple[ADP, list]:
    adapter = ADP()
    signed_in.goto(JOB)
    return adapter, adapter.browser_read(signed_in)


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def test_a_posting_is_scoped_to_its_employer_career_center():
    adapter = ADP()
    assert adapter.detect(f"{JOB}&utm_source=x", None)
    assert not adapter.detect(CENTER, None)
    assert not adapter.detect("https://myjobs.adp.com/acme/cx/job/5001", None)
    assert adapter.posting(f"{JOB}&utm_source=x") == (
        f"{HOST}/mascsr/default/mdf/recruitment/recruitment.html"
        "?cid=acme&ccId=19000101_000001&jobId=5001_1"
    )
    assert adapter.account_scope(JOB) == SCOPE
    assert adapter.login_url(SCOPE) == f"{CENTER}&lang=en_US&selectedMenuKey=SignIn"


def test_the_read_lists_each_step_from_the_page_and_the_meta(opened, ats):
    _, fields = opened
    assert ats.drafts == 1
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "personal.first_name", "personal.middle_name", "personal.last_name", "personal.email",
        "personal.mobile_phone", "personal.home_phone", "personal.country",
        "personal.address_line1", "personal.city", "personal.state", "personal.postal_code",
        "personal.county", "resume", "question.q1", "question.q2", "question.q3", "question.q4",
        "question.q5", "question.q6", "question.q7", "question.q7.currency", "question.q8",
        "question.q9", "pay.rate", "pay.amount", "pay.currency", "comment", "self_id.gender",
        "self_id.ethnicity", "self_id.race", "self_id.veteran", "attestation",
    ]  # fmt: skip
    assert by_id["personal.first_name"].prefilled == "Ada"
    assert by_id["personal.email"].prefilled == "ada@example.com"
    assert by_id["personal.mobile_phone"].required
    assert by_id["personal.mobile_phone"].prefilled == "+12025550143"
    assert by_id["personal.home_phone"].prefilled is None
    assert by_id["personal.country"].options == ["United States", "Canada"]
    assert by_id["personal.state"].options == ["Alabama", "District of Columbia"]
    assert by_id["personal.address_line1"].max_length == 30
    assert not by_id["personal.middle_name"].required
    assert by_id["question.q1"].options == ["Yes", "No"]
    assert by_id["question.q5"].options == ["Indeed", "Website"]
    assert by_id["question.q6"].type is FieldType.MULTI_CHOICE
    assert by_id["question.q7.currency"].options == [
        "United States Dollar ( USD )",
        "Canada Dollar ( CAD )",
    ]
    assert by_id["question.q8"].type is FieldType.DATE
    assert by_id["question.q9"].unfillable == (
        "unhandled_rendering: 'Sign here' is a Signature Pad question"
    )
    assert by_id["self_id.veteran"].options == [
        "I am not a protected veteran",
        "I decline to self-identify",
        "I identify myself as a protected veteran",
    ]
    assert not by_id["self_id.gender"].required
    assert by_id["attestation"].label.startswith("Yes, I agree to sign electronically.")
    assert "I certify my answers are true." in by_id["attestation"].label


def complete(resume: str) -> dict:
    return {
        "personal.home_phone": "+12025550199",
        "personal.country": "United States",
        "personal.address_line1": "1 Main St",
        "personal.city": "Washington",
        "personal.state": "District of Columbia",
        "personal.postal_code": "20001",
        "personal.county": "Kent",
        "resume": resume,
        "question.q1": "Yes",
        "question.q2": "A friend",
        "question.q3": "Engineer",
        "question.q4": "3",
        "question.q5": "Website",
        "question.q6": ["Morning", "Weekends"],
        "question.q7": "50000",
        "question.q7.currency": "United States Dollar ( USD )",
        "question.q8": "2026-12-01",
        "pay.rate": "Hourly",
        "pay.amount": "40",
        "pay.currency": "Canada Dollar ( CAD )",
        "self_id.gender": "Prefer not to say",
        "self_id.veteran": "I decline to self-identify",
        "attestation": "Yes",
    }


def test_fill_walks_every_step_up_to_the_attestation(opened, page, ats, resume):
    adapter, _ = opened
    answers = complete(resume)
    adapter.fill(page, answers)
    assert page.locator("#submitApplication").count()
    assert ats.uploads == 1
    assert adapter.read_back(page) == answers | {
        "personal.first_name": "Ada",
        "personal.last_name": "Placeholder",
        "personal.email": "ada@example.com",
        "personal.mobile_phone": "+12025550143",
        "resume": "CV Ada.pdf",
    }
    assert adapter.submit(page).startswith("Submit closed the wizard")
    assert page.get_by_text("Thank you for applying").count()


def test_a_fixed_dollar_reads_back_as_the_currency(signed_in, monkeypatch, resume):
    pay = TRANSFORMS["/jobApplication/requestedPayRate"] | {"optional": True}
    monkeypatch.setitem(TRANSFORMS, "/jobApplication/requestedPayRate", pay)
    adapter = ADP()
    signed_in.goto(JOB)
    adapter.browser_read(signed_in)
    answers = complete(resume) | {"pay.currency": "United States Dollar ( USD )"}
    adapter.fill(signed_in, answers)
    assert not signed_in.locator("#add_info_select_box").count()
    assert adapter.read_back(signed_in)["pay.currency"] == "United States Dollar ( USD )"


def test_a_step_the_page_refuses_names_its_error(opened, page):
    adapter, _ = opened
    with pytest.raises(NotTaken, match="Correct the information in highlighted fields"):
        adapter.fill(page, {"personal.city": "Washington"})


def test_a_draft_reopened_on_a_later_step_is_read_from_the_first(opened, page, resume):
    adapter, _ = opened
    page.locator("#PersonalAddress_address_line1").fill("1 Main St")
    page.locator("#PersonalAddress_city").fill("Washington")
    page.locator("#PersonalAddress_state").fill("DC")
    page.locator("#PersonalAddress_postalCode").fill("20001")
    page.locator("#ja_sv_cw_next_footer_btn").click()
    page.locator("input[type=file]").wait_for(state="attached")
    page.goto(JOB.replace("/default/", "/applicant/").replace("recruitment.html", "postLogin.html"))
    fields = ADP().browser_read(page)
    assert fields[0].id == "personal.first_name"
    assert page.locator("#personalInfomationFirstName").count()


def test_a_posting_without_apply_is_gone(signed_in, monkeypatch):
    monkeypatch.setattr("atsfill.adapters.adp.PAGE_TIMEOUT_MS", 3_000)
    signed_in.goto(JOB.replace("5001_1", "404"))
    with pytest.raises(Unsupported, match="posting_gone"):
        ADP().browser_read(signed_in)


def test_a_posting_that_sends_to_the_login_is_signed_out(page, monkeypatch):
    monkeypatch.setattr("atsfill.adapters.adp.PAGE_TIMEOUT_MS", 3_000)
    page.goto(JOB)
    with pytest.raises(SignedOut):
        ADP().browser_read(page)


def test_sign_in_types_the_emailed_code(page, otp_command):
    adapter = ADP()
    page.goto(adapter.login_url(SCOPE))
    with otp.source("default", adapter.name, SCOPE):
        adapter.sign_in_by_otp(page, "ada@example.com")
    assert page.url.startswith(f"{HOST}/mascsr/applicant/mdf/recruitment/postLogin.html")


def test_an_email_the_employer_does_not_know_stops_before_its_talent_community(page, otp_command):
    adapter = ADP()
    page.goto(adapter.login_url(SCOPE))
    with otp.source("default", adapter.name, SCOPE), pytest.raises(Refused):
        adapter.sign_in_by_otp(page, "new@example.com")


def test_sign_up_joins_the_talent_community_by_email(page, otp_command):
    adapter = ADP()
    page.goto(adapter.login_url(SCOPE))
    details = SignUpDetails(name="Ada Placeholder", phone="+12025550143")
    with otp.source("default", adapter.name, SCOPE):
        adapter.sign_up_by_otp(page, "new@example.com", details)
    assert page.url.startswith(f"{HOST}/mascsr/applicant/mdf/recruitment/postLogin.html")
    assert page.evaluate("sessionStorage.getItem('profile')") == "Ada"
