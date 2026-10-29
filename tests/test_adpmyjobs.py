"""The ADP MyJobs adapter on tests/pages/adpmyjobs/app.html, with ADP's API served here: the
sign-in, Apply that sends, and a workflow of a statement, contact, prescreen, documents and a
form of pages, one of them a page of entries."""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from atsfill.adapters import adpmyjobs
from atsfill.adapters.adpmyjobs import ADPMyJobs
from atsfill.core import browser, otp
from atsfill.core.adapter import NotTaken, Refused, SignedOut, SignUpDetails, Unsupported
from atsfill.core.results import AnyOf, FieldType, Is

PAGE = Path(__file__).parent / "pages/adpmyjobs/app.html"
SITE = "https://myjobs.adp.com/acmecareers"
JOB = f"{SITE}/cx/job/5001"


def options(codelist: str) -> list[dict]:
    return [{"linkID": "OPTIONS", "href": f"/codelists/{codelist}?rmapp=cla"}]


STATEMENT = [
    {"questionID": "text", "answerDataTypeCode": "INSTRUCTIONS", "questionText": "We keep data"},
    {
        "questionID": "agree",
        "answerDataTypeCode": "CHECKBOX",
        "requiredIndicator": True,
        "questionText": "<DIV>I agree to the above</DIV>",
    },
]
CONTACT = [
    {"questionID": "emailAddress", "answerDataTypeCode": "EMAIL", "disabledIndicator": True,
     "answerDefaultValue": "ada@example.com", "questionText": "Email Address"},
    {"questionID": "phoneType", "answerDataTypeCode": "SELECTION", "requiredIndicator": True,
     "questionText": "Phone Type", "links": options("PT"),
     "dependencies": [{"attributes": [{"answerID": "W", "questionID": "extension"}]}]},
    {"questionID": "extension", "answerDataTypeCode": "TEXT", "questionText": "Extension"},
    {"questionID": "phone", "answerDataTypeCode": "PHONENUMBER", "requiredIndicator": True,
     "answerDefaultValue": "+12025550143", "questionText": "Phone"},
    {"questionID": "country", "answerDataTypeCode": "COUNTRIES", "answerDefaultValue": "USA",
     "dependents": ["state"], "questionText": "Country", "requiredIndicator": True,
     "links": options("COUNTRY")},
    {"questionID": "state", "answerDataTypeCode": "STATES", "requiredIndicator": True,
     "questionText": "State", "links": options("STATE")},
    {"questionID": "zip", "answerDataTypeCode": "TEXT", "requiredIndicator": True,
     "questionText": "Postcode",
     "questionLabels": [{"code": "postcode_USA_label", "value": "ZIP Code:"}]},
    {"questionID": "smsOptIn", "answerDataTypeCode": "BOOLEAN", "answerDefaultValue": "false",
     "questionText": "SMS Messages"},
]  # fmt: skip
CODES = {
    "PT": [("C", "Cell"), ("W", "Work")],
    "COUNTRY": [("CAN", "Canada"), ("USA", "United States")],
    "STATE": [
        ("AL", "Alabama"),
        ("AK", "Alaska"),
        ("AZ", "Arizona"),
        ("DC", "District Of Columbia"),
    ],
}
PRESCREEN = [
    {"questionId": "q_1", "questionType": "Single choice", "orderId": 1,
     "questionDescription": "<DIV>Are you a current employee?</DIV>",
     "possibleAnswers": [{"answerId": "11", "answerLabel": "Yes", "knockoutRule": False,
                          "branchingQuestionId": "q_2"},
                         {"answerId": "12", "answerLabel": "No", "knockoutRule": False,
                          "branchingQuestionId": "q_3"}]},
    {"questionId": "q_2", "questionType": "Single choice", "orderId": 2,
     "questionDescription": "Confirm to close this application",
     "possibleAnswers": [{"answerId": "21", "answerLabel": "Confirmed", "knockoutRule": True,
                          "endOfQuestionIndicator": True}]},
    {"questionId": "q_3", "questionType": "Free form", "orderId": 3,
     "questionDescription": "Your desired salary?", "possibleAnswers": []},
    {"questionId": "q_4", "questionType": "Single choice", "orderId": 4,
     "questionDescription": "Are you 18 or older?",
     "possibleAnswers": [{"answerId": "41", "answerLabel": "Yes"},
                         {"answerId": "42", "answerLabel": "No"}]},
    {"questionId": "q_5", "questionType": "Calendar", "orderId": 5,
     "questionDescription": "Date you are available for work:", "possibleAnswers": []},
    {"questionId": "q_6", "questionType": "Multiple choice", "orderId": 6,
     "questionDescription": "Availability (select one or more):",
     "possibleAnswers": [{"answerId": "61", "answerLabel": "Full-Time"},
                         {"answerId": "62", "answerLabel": "Part-Time"},
                         {"answerId": "63", "answerLabel": "Weekends"}]},
]  # fmt: skip
DOCUMENTS = [{"questionID": "resume", "answerDataTypeCode": "UPLOAD", "requiredIndicator": True,
              "questionText": "Resume"}]  # fmt: skip
FORM = [
    {"sectionID": "p1", "sectionTitle": "Personal Information", "questions": [
        {"questionID": "relatives", "answerDataTypeCode": "LOOKUP", "requiredIndicator": True,
         "questionText": "Relatives employed here?",
         "links": options("YN"),
         "validationRules": {"conditionalRequiredValidations": [
             {"requiredFields": ["names"], "drivingValues": ["true"]}]}},
        {"questionID": "names", "answerDataTypeCode": "TEXT", "questionText": "Their names"},
        {"questionID": "date", "answerDataTypeCode": "DATETIME", "disabledIndicator": True,
         "answerDefaultValue": ""}]},
    {"sectionID": "p3", "sectionTitle": "Employment History"},
    {"sectionID": "p2", "sectionTitle": "eSignature"},
    {"sectionID": "9999999999999", "sectionTitle": "",
     "sectionLabels": [{"code": "sectionReview", "value": "true"}]},
]  # fmt: skip
SIGNATURE = [{"questionID": "sign", "answerDataTypeCode": "ESIGNATURE", "questionText": ""}]


def entry(n: int) -> list[dict]:
    index = [{"code": "repeatIndex", "value": str(n)}, {"code": "repeatType", "value": "REPEAT"}]
    start = {"answerDefaultValue": "1795842000000"} if n == 1 else {}  # 2026-11-28, saved
    return [
        {"questionID": f"employer_{n}", "answerDataTypeCode": "TEXT", "requiredIndicator": True,
         "questionText": "Employer", "questionLabels": index},
        {"questionID": f"start_{n}", "answerDataTypeCode": "DATE", "questionText": "Start Date",
         "questionLabels": index, **start},
        {"questionID": f"duties_{n}", "answerDataTypeCode": "LONGTEXT",
         "questionText": "Job Duties", "questionLabels": index},
    ]  # fmt: skip


EMPLOYMENT = {"sectionID": "p3", "sectionTitle": "Employment History",
              "sectionLabels": [{"code": "sectionRepeatLabel", "value": "Employer"},
                                {"code": "sectionRepeatMin", "value": "1"},
                                {"code": "sectionRepeatMax", "value": "2"}],
              "questions": [{"questionID": "opening", "answerDataTypeCode": "INSTRUCTIONS",
                             "questionText": "Your most recent employer first"},
                            *entry(1), *entry(2)]}  # fmt: skip
APPLICANT = {"person": {"personName": {"givenName": "Ada", "familyName1": "Placeholder"}}}
CODES["YN"] = [("true", "Yes"), ("false", "No")]
SECTIONS = [
    ("data_privacy", "Data Privacy Statement"),
    ("contact", "Contact Information"),
    ("prescreen1", "Preliminary Questions"),
    ("documents", "Documents"),
    ("appbuilder_eform", "Application Form"),
]


class Ats:
    """ADP's API for one account: its applications and the sections each saved."""

    def __init__(self) -> None:
        self.applications: list[dict] = []
        self.saved: set[str] = set()

    def answer(self, url: str, method: str) -> object:
        parts = urlsplit(url)
        path, query = parts.path, parse_qs(parts.query)
        if path.endswith("/career-site/acmecareers"):
            features = {"candidateApplicationWorkflow": True}
            return {"settings": {"externalId": "42", "enabledFeatures": features}}
        if path.endswith("/career-site/legacycareers"):
            return {"settings": {"externalId": "43", "enabledFeatures": {}}}
        if path.endswith("/job-applications"):
            return {"jobApplications": self.applications}
        if path.endswith("/job-applicant"):
            return {"jobApplicants": [APPLICANT]}
        if path.endswith("/assignment-creation"):
            self.applications.append({"reqID": "5001", "assignmentID": "77",
                                      "applicationStatusCode": {"label": "CONTINUE",
                                      "codeValue": "APPLICATION_SUBMITTED"}})  # fmt: skip
            return {"assignmentId": "77"}
        if path.endswith("/answers"):
            self.saved.add(query["formType"][0].split(".")[0])
            return {"questionSections": []}
        if path.endswith("/WORKFLOW"):
            listed = SECTIONS if "data_privacy" in self.saved else SECTIONS[:1]
            return {"questionSections": [
                {"sectionID": f"{c}.1", "sectionTitle": t,
                 "completionStatusCode": "completed" if c in self.saved else None,
                 "sectionLabels": [{"code": "component", "value": c}]}
                for c, t in listed]}  # fmt: skip
        if path.endswith("/prescreen.questions"):
            return {"prescreeningQuestions": PRESCREEN}
        if "/codelists/" in path:
            items = CODES[path.split("/codelists/")[1].split("/")[0]]
            return {"listItems": [{"codeValue": c, "longName": n} for c, n in items]}
        section = path.rsplit("/", 1)[1].split(".")[0]
        if section == "appbuilder_eform":
            if query.get("sectionID") == ["p2"]:
                return {"questionSections": [{"sectionID": "p2", "questions": SIGNATURE}]}
            if query.get("sectionID") == ["p3"]:
                return {"questionSections": [EMPLOYMENT]}
            return {"questionSections": FORM}
        questions = {"data_privacy": STATEMENT, "contact": CONTACT, "documents": DOCUMENTS}
        return {"questionSections": [{"sectionID": "1", "questions": questions[section]}]}


@pytest.fixture
def ats() -> Ats:
    return Ats()


@pytest.fixture
def page(tmp_path_factory, ats):
    def serve(route) -> None:
        request = route.request
        cors = {"access-control-allow-origin": "https://myjobs.adp.com",
                "access-control-allow-credentials": "true",
                "access-control-allow-headers": "*",
                "access-control-allow-methods": "*"}  # fmt: skip
        if request.method == "OPTIONS":
            route.fulfill(status=204, headers=cors)
        elif "/api/" in request.url or "/cx/staffing/" in request.url or "/public/" in request.url:
            route.fulfill(json=ats.answer(request.url, request.method), headers=cors)
        else:
            route.fulfill(path=PAGE)

    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://myjobs.adp.com/**", serve)
        page.context.route("https://my.adp.com/**", serve)
        yield page


@pytest.fixture
def opened(page, ats) -> tuple[ADPMyJobs, list]:
    adapter = ADPMyJobs()
    page.goto(JOB)
    evidence = adapter.open(page)
    assert evidence == "Apply made application 77, listed APPLICATION_SUBMITTED"
    return adapter, adapter.browser_read(page)


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def test_a_posting_is_scoped_to_its_career_site():
    adapter = ADPMyJobs()
    assert adapter.detect("https://myjobs.adp.com/AcmeCareers/cx/job/5001?utm_source=x", None)
    assert adapter.detect(f"{SITE}/cx/job-details?reqId=5001", None)
    assert not adapter.detect("https://workforcenow.adp.com/mascsr/default/mdf/recruitment/x", None)
    assert adapter.posting(f"{SITE}/cx/job-details?reqId=5001") == JOB
    assert adapter.account_scope(JOB) == "acmecareers"


def test_the_read_agrees_to_the_statement_then_lists_every_section(opened, ats):
    _, fields = opened
    assert "data_privacy" in ats.saved
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "contact.emailAddress", "contact.phoneType", "contact.extension", "contact.phone",
        "contact.country", "contact.state", "contact.zip", "contact.smsOptIn",
        "prescreen1.q_1", "prescreen1.q_2", "prescreen1.q_3", "prescreen1.q_4",
        "prescreen1.q_5", "prescreen1.q_6", "documents.resume", "appbuilder_eform.relatives",
        "appbuilder_eform.names", "appbuilder_eform.employer_1", "appbuilder_eform.start_1",
        "appbuilder_eform.duties_1", "appbuilder_eform.sign",
    ]  # fmt: skip
    assert by_id["contact.emailAddress"].prefilled == "ada@example.com"
    assert by_id["contact.extension"].shown_when == Is("contact.phoneType", ["Work"])
    assert by_id["contact.state"].options == [
        "Alabama",
        "Alaska",
        "Arizona",
        "District Of Columbia",
    ]
    assert by_id["contact.zip"].label == "ZIP Code"
    assert by_id["contact.smsOptIn"].prefilled == "No"
    assert by_id["prescreen1.q_2"].shown_when == Is("prescreen1.q_1", ["Yes"])
    assert by_id["prescreen1.q_2"].knockouts == ["Confirmed"]
    assert by_id["prescreen1.q_1"].knockouts is None
    assert by_id["prescreen1.q_3"].shown_when == Is("prescreen1.q_1", ["No"])
    assert by_id["prescreen1.q_4"].shown_when == Is("prescreen1.q_3")
    assert by_id["prescreen1.q_5"].type is FieldType.DATE
    assert by_id["prescreen1.q_6"].type is FieldType.MULTI_CHOICE
    assert by_id["prescreen1.q_6"].shown_when == Is("prescreen1.q_5")
    assert by_id["documents.resume"].type is FieldType.FILE
    assert by_id["appbuilder_eform.names"].required_when == Is(
        "appbuilder_eform.relatives", ["Yes"]
    )
    assert not isinstance(by_id["prescreen1.q_4"].shown_when, AnyOf)
    assert by_id["appbuilder_eform.start_1"].type is FieldType.DATE
    assert by_id["appbuilder_eform.start_1"].prefilled == "2026-11-28"
    assert by_id["appbuilder_eform.sign"].prefilled == "Ada Placeholder"


def test_fill_walks_every_section_and_page_up_to_the_review(opened, page, ats, resume):
    adapter, _ = opened
    answers = {
        "contact.phoneType": "Work",
        "contact.extension": "12",
        "contact.state": "District Of Columbia",
        "contact.zip": "20001",
        "prescreen1.q_1": "No",
        "prescreen1.q_3": "50000",
        "prescreen1.q_4": "Yes",
        "prescreen1.q_5": "2026-12-01",
        "prescreen1.q_6": ["Full-Time", "Weekends"],
        "documents.resume": resume,
        "appbuilder_eform.relatives": "No",
        "appbuilder_eform.employer_1": "Acme",
        "appbuilder_eform.duties_1": "Placeholder answer",
    }
    adapter.fill(page, answers)
    assert ats.saved == {"data_privacy", "contact", "prescreen1", "documents"}
    assert page.get_by_text("Review").count()
    assert adapter.read_back(page) == {
        "contact.emailAddress": "ada@example.com",
        "contact.phoneType": "Work",
        "contact.extension": "12",
        "contact.phone": "+12025550143",
        "contact.country": "United States",
        "contact.state": "District Of Columbia",
        "contact.zip": "20001",
        "contact.smsOptIn": "No",
        "prescreen1.q_1": "No",
        "prescreen1.q_3": "50000",
        "prescreen1.q_4": "Yes",
        "prescreen1.q_5": "2026-12-01",
        "prescreen1.q_6": ["Full-Time", "Weekends"],
        "documents.resume": "CV Ada.pdf",
        "appbuilder_eform.relatives": "No",
        "appbuilder_eform.employer_1": "Acme",
        "appbuilder_eform.start_1": "2026-11-28",
        "appbuilder_eform.duties_1": "Placeholder answer",
        "appbuilder_eform.sign": "Ada Placeholder",
    }
    assert adapter.submit(page) == "the page said Application Submitted!"
    assert "appbuilder_eform" in ats.saved


def test_a_posting_applied_to_is_continued_without_another_send(opened, page, ats):
    adapter = ADPMyJobs()
    page.goto(JOB)
    assert adapter.open(page) is None
    assert len(ats.applications) == 1
    assert "workflowComponent=contact" in page.url


def test_a_section_the_page_refuses_names_its_error(opened, page):
    adapter, _ = opened
    with pytest.raises(NotTaken, match="Phone Type is required"):
        adapter.fill(page, {"contact.zip": "20001"})


def test_a_site_whose_workflow_is_on_adp_recruiting_stops_before_apply(page, ats):
    page.goto("https://myjobs.adp.com/legacycareers/cx/job/5001")
    with pytest.raises(Unsupported, match="unsupported_flow: recruiting_workflow"):
        ADPMyJobs().open(page)
    assert not ats.applications


def test_a_posting_that_will_not_load_is_gone(page, monkeypatch):
    monkeypatch.setattr(adpmyjobs, "POSTING_TIMEOUT_MS", 3_000)
    page.goto(f"{SITE}/cx/job/404")
    with pytest.raises(Unsupported, match="posting_gone"):
        ADPMyJobs().open(page)


def test_a_posting_that_sends_to_the_sign_in_is_signed_out(page):
    page.goto(JOB)
    to_sign_in = "<script>location.replace('/acmecareers/auth')</script>"
    page.route("**/cx/job-details*", lambda r: r.fulfill(body=to_sign_in, content_type="text/html"))
    with pytest.raises(SignedOut):
        ADPMyJobs().open(page)


def test_sign_in_types_the_passcode_and_agrees_to_the_policy(page, otp_command):
    adapter = ADPMyJobs()
    page.goto(adapter.login_url("acmecareers"))
    with otp.source("default", adapter.name, "acmecareers"):
        adapter.sign_in_by_otp(page, "ada@example.com")
    assert page.url == f"{SITE}/cx"


def test_an_email_the_site_does_not_know_stops_before_its_profile(page, otp_command):
    adapter = ADPMyJobs()
    page.goto(adapter.login_url("acmecareers"))
    with otp.source("default", adapter.name, "acmecareers"), pytest.raises(Refused):
        adapter.sign_in_by_otp(page, "new@example.com")


def test_sign_up_creates_the_profile_by_email(page, otp_command):
    adapter = ADPMyJobs()
    page.goto(adapter.login_url("acmecareers"))
    details = SignUpDetails(name="Ada Placeholder", phone="+12025550143")
    with otp.source("default", adapter.name, "acmecareers"):
        adapter.sign_up_by_otp(page, "new@example.com", details)
    assert page.url == f"{SITE}/cx"
    assert page.evaluate("sessionStorage.getItem('profile')") == "Ada"
