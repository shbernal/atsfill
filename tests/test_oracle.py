"""The Oracle adapter on tests/pages/oracle, served as an employer's Candidate Experience site."""

from pathlib import Path

import pytest

from atsfill.adapters.oracle import Oracle
from atsfill.core import browser, matching
from atsfill.core.adapter import Blocked
from atsfill.core.results import FieldType

PAGES = Path(__file__).parent / "pages/oracle"
JOB = "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1234"


def serve(route) -> None:
    url = route.request.url
    if "/apply/email" in url:
        route.fulfill(path=PAGES / "email.html")
    elif "/apply/section/" in url:
        route.fulfill(path=PAGES / "form.html")
    else:
        route.fulfill(content_type="text/html", body="<h1>Job</h1>")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://acme.fa.us2.oraclecloud.com/**", serve)
        yield page


@pytest.fixture
def opened(page) -> tuple[Oracle, list]:
    adapter = Oracle()
    page.goto(JOB + "?utm_source=board")
    assert adapter.detect(page.url, page)
    return adapter, adapter.read_form(page)


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def test_detection_and_posting():
    adapter = Oracle()
    assert adapter.detect(JOB + "?utm_source=board", None)
    assert not adapter.detect(
        "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/", None
    )
    other_site = JOB.replace("/en/sites/CX_1/", "/fr/sites/CX_2/") + "/apply/email"
    assert adapter.posting(other_site) == adapter.posting(JOB)


def test_read_form(opened):
    fields = {f.id: f for f in opened[1]}
    assert list(fields) == [
        "email",
        "legalDisclaimer",
        "lastName",
        "title",
        "phoneNumber",
        "country",
        "addressLine1",
        "city",
        "dateOfBirth",
        "3001",
        "3002",
        "3003",
        "3004",
        "3005",
        "resume",
        "coverLetter",
        "siteLink-1",
        "block:107",
        "US-STANDARD-ORA_ETHNICITY_US_OTHERS-STANDARD",
        "job-alerts",
        "fullName",
    ]  # the profile import and the optional education tiles are left out
    assert fields["legalDisclaimer"].label == "I agree with the terms and conditions"
    assert fields["lastName"].required and fields["lastName"].max_length == 150
    assert fields["title"].options == ["Mr.", "Ms."]
    assert fields["phoneNumber"].type is FieldType.PHONE
    assert fields["country"].prefilled and fields["country"].label == "Country/Region"
    assert fields["country"].options == ["France", "Poland", "United Kingdom"]
    assert fields["city"].type is FieldType.TYPEAHEAD
    assert fields["dateOfBirth"].type is FieldType.DATE
    assert fields["3002"].type is FieldType.MULTI_CHOICE
    assert fields["3003"].options == ["Job board", "Employee referral", "Social media"]
    assert fields["3004"].options == ["Yes", "No"]
    assert fields["resume"].required and not fields["coverLetter"].required
    assert fields["resume"].accept == [".pdf", ".docx"]
    assert fields["block:107"].label == "Academic Transcript"
    assert fields["job-alerts"].options == ["Yes", "No"]


def test_every_answer_reads_back(opened, page, resume):
    adapter, fields = opened
    answers = {
        "email": "ada@example.com",
        "legalDisclaimer": "Yes",
        "lastName": "Placeholder",
        "title": "Ms.",
        "phoneNumber": "+44 20 7946 0000",
        "country": "France",
        "addressLine1": "1 Example Street",
        "city": "London",
        "dateOfBirth": "1991-02-03",
        "3001": "Yes",
        "3002": ["Alumni", "None of the above"],
        "3003": "Employee referral",
        "3004": "No",
        "3005": "50000",
        "resume": resume,
        "block:107": resume,
        "siteLink-1": "https://example.com",
        "US-STANDARD-ORA_ETHNICITY_US_OTHERS-STANDARD": ["I do not wish to answer"],
        "job-alerts": "Yes",
        "fullName": "Ada Placeholder",
    }
    assert not matching.problems(fields, answers)
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [c for c in checks if not c.match] == []


def test_a_known_address_gets_an_emailed_code(opened, page, resume):
    adapter, _ = opened
    with pytest.raises(Blocked, match="emailed_code"):
        adapter.fill(page, {"email": "known@example.com", "legalDisclaimer": "Yes"})
