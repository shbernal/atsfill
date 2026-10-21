"""The Dayforce adapter on tests/pages/dayforce, served with the JSON its page and adapter read."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from atsfill.adapters.dayforce import Dayforce
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import Answer, FieldType

PAGE = Path(__file__).parent / "pages/dayforce/apply.html"
JOB = "https://jobs.dayforcehcm.com/en-US/acme/careers/jobs/123"


def field(name: str, required: bool = False, visible: bool = True) -> dict:
    return {"fieldName": name, "displayName": name, "isRequired": required, "isVisible": visible}


def option(n: int, label: str, follow_up: bool = False, name: str | None = None) -> dict:
    return {"optionId": n, "displayName": name or label, "description": label,
            "hasFollowUp": follow_up}  # fmt: skip


def question(n: int, kind: int, label: str, options=(), required: bool = False) -> dict:
    return {"questionId": n, "displayName": f"Q{n}", "description": label, "answerType": kind,
            "isRequired": required, "options": list(options)}  # fmt: skip


TEMPLATE = {
    "sections": [
        {"xRefCode": "PERSONALINFORMATION", "displayName": "Personal Information", "sequence": 0,
         "isRequired": True, "questionnaire": None, "fields": [
            field("Prefix"), field("FirstName", True), field("LastName", True),
            field("MobilePhone", True), field("PreferredContactMethod", True),
            field("Country", True), field("StateProvince", True), field("City", True),
            field("ZipPostalCode", True), field("CandidateSource", True),
            field("Fax", visible=False),
         ]},
        {"xRefCode": "RESUMEUPLOAD", "displayName": "Resume Upload", "sequence": 1,
         "isRequired": False, "questionnaire": None, "fields": None},
        {"xRefCode": "WORKHISTORY", "displayName": "Employment History", "sequence": 2,
         "isRequired": False, "questionnaire": None, "fields": [
            field("Employer Name", True), field("StartDate", True), field("CurrentJob"),
            field("StateProvince"),
         ]},
        {"xRefCode": "AVAILABILITY", "displayName": "Availability", "sequence": 3,
         "isRequired": False, "questionnaire": None, "fields": [field("WorkHoursAvailability")]},
        {"xRefCode": None, "displayName": "Screening", "sequence": 5, "isRequired": False,
         "questionnaire": {"questions": [
            question(11, 2, "Do you hold a licence?", [option(1, "Yes", True), option(2, "No")]),
            question(12, 1, "Please read the following."),
            question(13, 4, "Which shift suits you?", [option(3, "Days"), option(4, "Nights")]),
            question(14, 3, "Your notice period"),
            question(15, 6, "I agree to a background check", required=True),
            question(16, 7, "Which locations?",
                     [option(5, "North"), option(6, "South"), option(7, "East", True)]),
         ]}},
        {"xRefCode": "DISABILITYQUESTIONNAIRE", "displayName": "Disability Form Questionnaire",
         "sequence": 6, "isRequired": False, "questionnaire": {"questions": [
            question(21, 1, "<h3>Voluntary Self-Identification of Disability</h3>"),
            question(22, 3, "Name", required=True),
            question(23, 7, "Disability", [
                option(8, "Yes, I have a disability", name="YES"),
                option(9, "No, I do not", name="NO"),
                option(10, "I do not want to answer", name="DECLINE"),
            ]),
         ]}},
        {"xRefCode": "SUBMIT", "displayName": "Submit", "sequence": 32767, "isRequired": True,
         "questionnaire": None, "fields": None},
    ],
    "candidateAcknowledgement": "<p>By submitting this application, I certify it is true.</p>",
}  # fmt: skip
API = {
    "sitecontext": {"jobBoardId": 1, "hasActiveSMSProvider": True, "enablePrivacyPolicy": True},
    "jobapplication": TEMPLATE,
    "candidatesource": {"candidateSources": [
        {"displayName": "Job Board", "requiresAdditionalExplanation": False},
        {"displayName": "Agency", "requiresAdditionalExplanation": True},
    ]},
    "applicationformfieldsoptions": {"prefix": [{"displayName": n} for n in ("Mr", "Ms", "Dr")]},
    "FileService": {"fileName": "resume.pdf"},
}  # fmt: skip


def serve(route) -> None:
    url = route.request.url
    if "/api/" in url:
        name = next(k for k in API if f"/{k}/" in url)
        route.fulfill(content_type="application/json", body=json.dumps(API[name]))
    elif "/apply/" in url:
        route.fulfill(path=PAGE)
    else:
        route.fulfill(content_type="text/html", body="<main><h1>Engineer</h1></main>")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.dayforce.FORM_TIMEOUT_MS", 1_000)
        mp.setattr("atsfill.adapters.dayforce.SETTLE_MS", 100)
        mp.setattr("atsfill.adapters.dayforce.POLL_MS", 50)
        mp.setattr("atsfill.adapters.dayforce.FOLLOWUP_TIMEOUT_MS", 500)
        mp.setattr("atsfill.adapters.dayforce.DIAL_TIMEOUT_MS", 500)
        mp.setattr("atsfill.adapters.dayforce.OPTIONS_TIMEOUT_MS", 1_000)
        mp.setattr("atsfill.adapters.dayforce.STEP_TIMEOUT_MS", 4_000)
        page.context.route("https://jobs.dayforcehcm.com/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "resume.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def opened(page) -> tuple[Dayforce, list]:
    adapter = Dayforce()
    assert adapter.detect(JOB, None)
    page.goto(JOB)
    return adapter, adapter.read_form(page)


def test_posting_key_drops_the_culture_and_the_step():
    adapter = Dayforce()
    key = "https://jobs.dayforcehcm.com/acme/careers/jobs/123"
    assert adapter.posting(JOB) == key
    assert adapter.posting(f"{key}/apply/manualApplication?applicationSource=Manual") == key
    assert not adapter.detect("https://jobs.dayforcehcm.com/en-US/acme/careers", None)


def test_read_form_lists_every_page(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "email", "confirmEmail", "prefixId", "firstName", "lastName", "mobilePhone",
        "preferredContactMethod", "countryCode", "stateCode", "city", "postalCode",
        "candidateSource", "candidateSourceDescription", "smsConsent", "resume", "coverLetter",
        "additionalDocument", "workHistory.companyName", "workHistory.effectiveStart",
        "workHistory.isCurrent", "workHistory.stateCode", "workHours.days", "workHours.start",
        "workHours.end", "workHours.comment", "q11", "q11.followup", "q13", "q14",
        "q15", "q16", "q16.followup", "q22", "q23", "userAcknowledged",
    ]  # fmt: skip
    assert by_id["prefixId"].options == ["Mr", "Ms", "Dr"]  # a readonly dropdown, scrolled
    assert by_id["countryCode"].options and "United Kingdom" in by_id["countryCode"].options
    assert by_id["stateCode"].type is FieldType.TYPEAHEAD
    assert by_id["mobilePhone"].type is FieldType.PHONE
    assert by_id["postalCode"].max_length == 10
    assert by_id["resume"].accept == [".doc", ".docx", ".pdf"]
    assert not by_id["workHistory.companyName"].required  # the section is optional
    assert by_id["workHistory.companyName"].max_length == 50  # read from an entry it opens
    assert by_id["workHistory.isCurrent"].options == ["No", "Yes"]
    assert by_id["candidateSourceDescription"].label.endswith(
        "(asked when 'CandidateSource' is Agency)"
    )
    assert by_id["q11.followup"].label.endswith("(asked when 'Do you hold a licence?' is Yes)")
    assert by_id["q15"].options == ["Yes", "No"] and by_id["q15"].required
    assert by_id["q16"].type is FieldType.MULTI_CHOICE
    assert by_id["q22"].prefilled  # the disability form's name is the page's own
    assert by_id["q23"].options == ["Yes, I have a disability", "No, I do not",
                                    "I do not want to answer"]  # fmt: skip
    assert by_id["userAcknowledged"].label.startswith("Candidate Acknowledgement: By submitting")


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page)
    past = (date.today() - timedelta(days=400)).isoformat()
    answers: dict[str, Answer] = {
        "email": "ada@example.com",
        "confirmEmail": "ada@example.com",
        "prefixId": "Dr",
        "firstName": "Ada",
        "lastName": "Placeholder",
        "mobilePhone": "+12025550143",
        "preferredContactMethod": "Email",
        "countryCode": "United Kingdom",
        "stateCode": "Greater London",
        "city": "London",
        "postalCode": "SW1A 1AA",
        "candidateSource": "Agency",
        "candidateSourceDescription": "Acme Recruiting",
        "smsConsent": "Yes",
        "resume": resume,
        "coverLetter": resume,
        "workHistory.companyName": "Acme",
        "workHistory.effectiveStart": past,
        "workHistory.isCurrent": "Yes",
        "workHistory.stateCode": "New York",
        "workHours.days": ["Monday", "Friday"],
        "workHours.start": "09:00",
        "workHours.end": "17:00",
        "workHours.comment": "No weekends.",
        "q11": "Yes",
        "q11.followup": "Class B",
        "q13": "Nights",
        "q14": "One month",
        "q15": "Yes",
        "q16": ["North", "East"],
        "q16.followup": "Remote",
        "q23": ["I do not want to answer"],
        "userAcknowledged": "Yes",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)
    assert page.locator("main [test-id=application-next-step]").inner_text() == "Submit"


def test_a_future_start_date_keeps_the_wizard_on_candidate_info(page, resume):
    adapter, fields = opened(page)
    future = (date.today() + timedelta(days=30)).isoformat()
    answers: dict[str, Answer] = {
        "email": "ada@example.com",
        "confirmEmail": "ada@example.com",
        "firstName": "Ada",
        "lastName": "Placeholder",
        "mobilePhone": "+12025550143",
        "preferredContactMethod": "Email",
        "countryCode": "United Kingdom",
        "stateCode": "Greater London",
        "city": "London",
        "postalCode": "SW1A 1AA",
        "candidateSource": "Job Board",
        "workHistory.companyName": "Acme",
        "workHistory.effectiveStart": future,
        "workHours.days": ["Monday"],
        "workHours.start": "09:00",
        "workHours.end": "17:00",
        "q15": "Yes",
        "userAcknowledged": "Yes",
    }
    adapter.fill(page, answers)
    mismatched = {c.field_id for c in matching.check(fields, answers, adapter.read_back(page))
                  if not c.match}  # fmt: skip
    assert mismatched == {"q15", "userAcknowledged"}


def test_an_empty_follow_up_keeps_the_wizard_on_its_questionnaire(page):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {
        "email": "ada@example.com",
        "confirmEmail": "ada@example.com",
        "firstName": "Ada",
        "lastName": "Placeholder",
        "mobilePhone": "+12025550143",
        "preferredContactMethod": "Email",
        "countryCode": "United Kingdom",
        "stateCode": "Greater London",
        "city": "London",
        "postalCode": "SW1A 1AA",
        "candidateSource": "Job Board",
        "workHours.days": ["Monday"],
        "workHours.start": "09:00",
        "workHours.end": "17:00",
        "q15": "Yes",
        "q16": ["East"],
        "q23": ["I do not want to answer"],
        "userAcknowledged": "Yes",
    }
    adapter.fill(page, answers)
    mismatched = {c.field_id for c in matching.check(fields, answers, adapter.read_back(page))
                  if not c.match}  # fmt: skip
    assert mismatched == {"q23", "userAcknowledged"}


def test_a_closed_posting_is_gone(page):
    page.context.unroute("https://jobs.dayforcehcm.com/**")
    page.context.route(
        "https://jobs.dayforcehcm.com/**",
        lambda route: route.fulfill(content_type="text/html", body="<main>Job not found</main>"),
    )
    try:
        page.goto(JOB)
        with pytest.raises(Unsupported, match="posting_gone"):
            Dayforce().read_form(page)
    finally:
        page.context.unroute("https://jobs.dayforcehcm.com/**")
        page.context.route("https://jobs.dayforcehcm.com/**", serve)
