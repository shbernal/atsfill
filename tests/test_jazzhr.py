"""The JazzHR adapter on tests/pages/jazzhr, served as a posting's page."""

from pathlib import Path

import pytest

from atsfill.adapters.jazzhr import JazzHR, _field
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import Answer, FieldType, Is

PAGE = Path(__file__).parent / "pages/jazzhr/apply.html"
JOB = "https://acme.applytojob.com/apply/AbCdE12345/Software-Engineer"
POSTING = "https://acme.applytojob.com/apply/AbCdE12345"


def serve(route) -> None:
    if route.request.method == "POST":
        route.fulfill(content_type="text/html", body="<h1>Thank you for applying!</h1>")
    elif "GONE" in route.request.url:
        route.fulfill(status=410, content_type="text/html", body="<h1>Acme - Career Page</h1>")
    else:
        route.fulfill(path=PAGE)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.jazzhr.FORM_TIMEOUT_MS", 1_000)
        page.context.route("https://acme.applytojob.com/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def opened(page) -> tuple[JazzHR, list]:
    adapter = JazzHR()
    page.goto(JOB)
    assert adapter.detect(JOB, None)
    return adapter, adapter.read_form(page)


def test_read_form(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "firstname",
        "email",
        "phone",
        "address",
        "city",
        "state",
        "postal",
        "resume",
        "coverletter",
        "citizen",
        "salary",
        "start",
        "q1001",
        "q1002",
        "q1003",
        "q1004",
        "q1005",
        "eeo_gender",
        "eeoc_veteran",
        "eeoc_disability",
        "eeoc_disability_signature",
        "eeoc_disability_date",
        "sms-consent",
    ]
    assert by_id["phone"].type is FieldType.PHONE
    assert by_id["city"].label == "City"
    assert by_id["city"].required  # the address's star stands for its four inputs
    assert by_id["resume"].required
    assert by_id["resume"].accept == [".pdf", ".doc", ".docx", ".odt", ".txt", ".rtf"]
    assert by_id["citizen"].options == [
        "I am a U.S. Citizen/Permanent Resident",
        "Non-citizen seeking work authorization",
        "I am a Canadian Citizen/Permanent Resident",
        "Other",
    ]
    assert by_id["q1001"].options == ["Yes.", "No, but I am willing to relocate."]
    assert by_id["q1004"].type is FieldType.MULTI_CHOICE
    assert by_id["q1004"].options == ["Weekdays", "Weekends", "Evenings"]
    assert by_id["q1004"].required
    assert by_id["start"].type is FieldType.DATE
    assert by_id["q1005"].type is FieldType.DATE
    assert by_id["eeo_gender"].label == "Gender"
    assert by_id["eeo_gender"].options == ["Decline to answer", "Female", "Male"]
    assert by_id["eeoc_veteran"].label == "Veteran status"
    assert by_id["eeoc_disability"].options == [
        "YES, I HAVE A DISABILITY, OR HAVE HAD ONE IN THE PAST",
        "NO, I DON'T HAVE A DISABILITY AND HAVE NOT HAD ONE IN THE PAST",
        "I DO NOT WANT TO ANSWER",
    ]
    declared = Is("eeoc_disability", ["YES, I HAVE A DISABILITY, OR HAVE HAD ONE IN THE PAST"])
    for signed in ("eeoc_disability_signature", "eeoc_disability_date"):
        assert not by_id[signed].required and by_id[signed].required_when == declared
    assert by_id["eeoc_disability_date"].label == "Disability status, Date"
    assert by_id["sms-consent"].options == ["Yes", "No"]
    assert not by_id["sms-consent"].required
    assert not by_id["salary"].required


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {
        "firstname": "Ada",
        "email": "ada@example.com",
        "phone": "+12025550143",
        "address": "1 Main St",
        "city": "London",
        "state": "London",
        "postal": "20001",
        "resume": resume,
        "coverletter": "Placeholder answer.",
        "citizen": "Other",
        "salary": "50000",
        "start": "2026-12-25",
        "q1001": "No, but I am willing to relocate.",
        "q1002": "3",
        "q1003": "Placeholder answer.",
        "q1004": ["Weekdays", "Evenings"],
        "q1005": "2027-01-04",
        "eeo_gender": "Decline to answer",
        "eeoc_veteran": "I DON’T WISH TO ANSWER",
        "eeoc_disability": "I DO NOT WANT TO ANSWER",
        "eeoc_disability_signature": "Ada Placeholder",
        "eeoc_disability_date": "2026-10-20",
        "sms-consent": "Yes",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_an_unknown_rendering_is_listed_unhandled():
    raw = {"id": "x", "label": "X", "kind": "range", "required": False}
    assert _field(raw).unfillable == "unhandled_rendering: x is a range"


def test_a_posting_gone(page):
    page.goto("https://acme.applytojob.com/apply/GONEGONE00/Nothing")
    with pytest.raises(Unsupported, match="posting_gone"):
        JazzHR().read_form(page)


def test_submit_returns_the_posted_page(page):
    adapter, _ = opened(page)
    assert adapter.submit(page) == f"{JOB} 200"


def test_posting_urls():
    adapter = JazzHR()
    for url in (
        JOB,
        POSTING,
        f"{JOB}?utm_source=x",
    ):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == POSTING
    assert not adapter.detect("https://acme.applytojob.com/apply", None)
    assert not adapter.detect("https://www.example.com/apply/AbCdE12345", None)
