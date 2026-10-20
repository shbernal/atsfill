"""The Lever adapter on tests/pages/lever, served as a posting's application page."""

from pathlib import Path

import pytest

from atsfill.adapters.lever import Lever
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import Answer, FieldType

PAGE = Path(__file__).parent / "pages/lever/apply.html"
POSTING = "https://jobs.lever.co/acme/0a1b2c3d-0000-4000-8000-123456789abc"
CARD = "cards[11111111-1111-1111-1111-111111111111]"
SURVEY = "surveysResponses[22222222-2222-2222-2222-222222222222][responses][field0]"


def serve(route) -> None:
    url, method = route.request.url, route.request.method
    if method == "POST" and url.endswith("/apply"):
        route.fulfill(status=302, headers={"Location": POSTING + "/thanks"})
    elif url.endswith("/thanks"):
        route.fulfill(content_type="text/html", body="<h3>Application submitted!</h3>")
    elif "/0000" in url:
        route.fulfill(status=404, content_type="text/html", body="<title>Not found</title>")
    else:
        route.fulfill(path=PAGE)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.lever.FORM_TIMEOUT_MS", 1_000)
        page.context.route("https://jobs.lever.co/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def opened(page) -> tuple[Lever, list]:
    adapter = Lever()
    page.goto(POSTING)
    assert adapter.detect(POSTING, None)
    return adapter, adapter.read_form(page)


def test_read_form_opens_the_application(page):
    _, fields = opened(page)
    assert page.url == POSTING + "/apply"
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "resume",
        "name",
        "email",
        "phone",
        "location",
        "org",
        "pronouns",
        "opportunityLocationId",
        "urls[LinkedIn]",
        *(f"{CARD}[field{n}]" for n in range(6)),
        SURVEY,
        "eeo[gender]",
        "eeo[race]",
        "eeo[veteran]",
        "eeo[disabilitySignature]",
        "eeo[disabilitySignatureDate]",
        "comments",
        "consent[store]",
        "consent[marketing]",
    ]
    assert by_id["resume"].type is FieldType.FILE and by_id["resume"].required
    assert by_id["location"].type is FieldType.TYPEAHEAD
    assert by_id["location"].max_length == 100
    assert by_id["phone"].type is FieldType.PHONE
    assert by_id["pronouns"].options == ["He/him", "She/her", "They/them", "Use name only"]
    assert by_id["opportunityLocationId"].options == ["Springfield", "Shelbyville"]
    assert by_id[f"{CARD}[field0]"].label == "Salary expectations"
    assert by_id[f"{CARD}[field2]"].options == ["Yes", "No"]
    assert by_id[f"{CARD}[field3]"].type is FieldType.MULTI_CHOICE
    assert by_id[f"{CARD}[field4]"].options == ["Job board", "Referral & friends"]
    assert by_id[f"{CARD}[field5]"].type is FieldType.FILE
    assert by_id["eeo[race]"].label == "Race"
    assert by_id["eeo[race]"].options == ["Two or More Races", "Decline to self-identify"]
    assert by_id["eeo[veteran]"].options[0] == "I am not a protected veteran"
    assert by_id["eeo[disabilitySignatureDate]"].type is FieldType.DATE
    assert by_id["comments"].label.startswith("Add a cover letter")
    assert by_id["consent[store]"].label.startswith("I agree that Acme stores")
    assert by_id["consent[store]"].required
    assert by_id["consent[marketing]"].options == ["Yes", "No"]
    assert not by_id["consent[marketing]"].required
    assert not by_id[f"{CARD}[field1]"].required


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {
        "name": "Ada Placeholder",
        "email": "ada@example.com",
        "phone": "+12025550143",
        "location": "London",
        "org": "Acme",  # after the resume, whose parse fills it
        "resume": resume,
        "pronouns": ["They/them"],
        "opportunityLocationId": "Shelbyville",
        "urls[LinkedIn]": "https://example.com",
        f"{CARD}[field0]": "50000",
        f"{CARD}[field1]": "Placeholder answer.",
        f"{CARD}[field2]": "No",
        f"{CARD}[field3]": ["Days", "Weekends"],
        f"{CARD}[field4]": "Referral & friends",
        f"{CARD}[field5]": resume,
        SURVEY: "30 or over",
        "eeo[gender]": "Decline to self-identify",
        "eeo[race]": "Decline to self-identify",
        "eeo[veteran]": "I am not a protected veteran",
        "eeo[disabilitySignatureDate]": "2026-10-20",
        "comments": "Placeholder answer.",
        "consent[store]": "Yes",
        "consent[marketing]": "No",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)
    assert page.locator("[name=eeo\\[disabilitySignatureDate\\]]").input_value() == "10/20/2026"
    assert "London, Greater London" in page.locator("#selected-location").input_value()


def test_a_posting_gone(page):
    page.goto("https://jobs.lever.co/acme/00000000-0000-4000-8000-000000000000/apply")
    with pytest.raises(Unsupported, match="posting_gone"):
        Lever().read_form(page)


def test_submit_returns_the_thanks_page(page):
    adapter, _ = opened(page)
    assert adapter.submit(page) == POSTING + "/thanks"


def test_posting_urls():
    adapter = Lever()
    for url in (
        POSTING,
        POSTING + "/apply",
        POSTING + "?utm_source=x",
        POSTING + "/apply?lever-source=x",
    ):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == POSTING
    eu = "https://jobs.eu.lever.co/acme/0a1b2c3d-0000-4000-8000-123456789abc"
    assert adapter.posting(eu + "/apply") == eu
    assert not adapter.detect("https://jobs.lever.co/acme", None)
    assert not adapter.detect(
        "https://www.example.com/acme/0a1b2c3d-0000-4000-8000-123456789abc", None
    )
