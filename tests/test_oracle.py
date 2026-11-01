"""The Oracle adapter on tests/pages/oracle, served as an employer's Candidate Experience site."""

from pathlib import Path

import pytest

from atsfill.adapters.oracle import Oracle
from atsfill.core import browser, matching, otp
from atsfill.core.adapter import Blocked, NotTaken, RevealsFields
from atsfill.core.results import FieldType, Is, OptionSet

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
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.oracle.SETTLE_MS", 100)
        mp.setattr("atsfill.adapters.oracle.LIST_TIMEOUT_MS", 500)
        mp.setattr("atsfill.adapters.oracle.LIST_POLL_MS", 10)
        mp.setattr("atsfill.adapters.oracle.SEARCH_TIMEOUT_MS", 1_000)
        mp.setattr("atsfill.adapters.oracle.UPLOAD_TIMEOUT_MS", 2_000)
        page.context.route("https://acme.fa.us2.oraclecloud.com/**", serve)
        yield page


@pytest.fixture
def opened(page) -> tuple[Oracle, list]:
    adapter = Oracle()
    page.goto(JOB + "?utm_source=board")
    assert adapter.detect(page.url, page)
    return adapter, adapter.browser_read(page)


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


def test_browser_read(opened):
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
        "postalCode",
        "dateOfBirth",
        "3001",
        "3002",
        "3003",
        "3004",
        "3005",
        "3006",
        "resume",
        "coverLetter",
        "siteLink-1",
        "block:107",
        "block:108",
        "US-STANDARD-ORA_ETHNICITY_US_OTHERS-STANDARD",
        "job-alerts",
        "fullName",
    ]  # the profile import and the state, which the postal code fills, are left out
    assert fields["block:108"].unfillable == (
        "unsupported_rendering: entries: 'Education' needs entries added"
    )
    assert not fields["block:108"].required
    assert fields["3006"].unfillable == (
        "unhandled_rendering: 'Rate your English' has no control atsfill knows"
    )
    assert fields["legalDisclaimer"].label == "I agree with the terms and conditions"
    assert fields["lastName"].required and fields["lastName"].max_length == 150
    assert fields["title"].options == ["Mr.", "Ms."]
    assert fields["phoneNumber"].type is FieldType.PHONE
    assert fields["country"].default == "United Kingdom" and fields["country"].prefilled is None
    assert fields["country"].label == "Country/Region"
    assert fields["country"].options == ["France", "Poland", "United Kingdom"]
    assert fields["city"].type is FieldType.TEXT  # an address search, left unpicked
    assert not fields["postalCode"].required  # the state is not, the postal code is
    held = Is("country", ["United Kingdom"])
    assert fields["postalCode"].required_when == held  # another country has its own rows
    assert fields["addressLine1"].required_when == held
    assert not fields["lastName"].required_when and not fields["country"].required_when
    assert fields["postalCode"].options is None  # under another country, a request each
    assert fields["postalCode"].option_sets == [
        OptionSet(Is("country", ["United Kingdom"]), ["0200, Acton, ACT", "2000, Sydney, NSW"])
    ]
    assert fields["siteLink-1"].type is FieldType.URL
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
        "postalCode": "2000, Sydney, NSW",
        "dateOfBirth": "1991-02-03",
        "resume": resume,  # attached after the pills and text below, whatever the order
        "3001": "Yes",
        "3002": ["Alumni", "None of the above"],
        "3003": "Employee referral",
        "3004": "No",
        "3005": "50000",
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


def test_another_country_reveals_its_address_rows(opened, page):
    adapter, fields = opened
    assert isinstance(adapter, RevealsFields)
    answers = {"email": "ada@example.com", "legalDisclaimer": "Yes", "country": "Poland"}
    adapter.fill(page, answers)
    new = [f for f in adapter.revealed(page) if f.id not in {g.id for g in fields}]
    assert [(f.id, f.label, f.required) for f in new] == [("region3", "Province", True)]
    adapter.fill(page, {"region3": "Mazovia"})  # on the form as it stands
    assert adapter.read_back(page)["region3"] == "Mazovia"
    assert adapter.read_back(page)["country"] == "Poland"
    assert adapter.revealed(page) == list(adapter._fields.values())


def test_the_forms_country_reveals_nothing(opened, page):
    adapter, fields = opened
    adapter.fill(page, {"email": "ada@example.com", "legalDisclaimer": "Yes"})
    assert [f.id for f in adapter.revealed(page)] == [f.id for f in fields]


def test_an_address_row_another_country_drops_is_not_taken(opened, page):
    adapter, _ = opened
    answers = {
        "email": "ada@example.com",
        "legalDisclaimer": "Yes",
        "country": "Poland",
        "city": "London",
    }
    with pytest.raises(NotTaken, match="^city is not on the form"):
        adapter.fill(page, answers)


def test_a_known_address_types_the_emailed_otp_and_replaces_the_drafts_resume(
    opened, page, resume, otp_command
):
    adapter, fields = opened
    answers = {"email": "known@example.com", "legalDisclaimer": "Yes", "resume": resume}
    with otp.source("default", adapter.name, None):
        adapter.fill(page, answers)
    assert page.url.endswith("/apply/section/1")
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [c for c in checks if not c.match] == []


def test_a_known_address_without_an_otp_command_is_blocked(opened, page, tmp_path, monkeypatch):
    adapter, _ = opened
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    with (
        otp.source("default", adapter.name, None),
        pytest.raises(Blocked, match="^no_otp_command: the email step sent an OTP to known@"),
    ):
        adapter.fill(page, {"email": "known@example.com", "legalDisclaimer": "Yes"})
