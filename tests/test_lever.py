"""The Lever adapter on tests/pages/lever, served as a posting's application page."""

from pathlib import Path

import pytest

from atsfill.adapters.lever import Lever, _field
from atsfill.core import browser, matching
from atsfill.core.adapter import Blocked, Unsupported
from atsfill.core.results import Answer, FieldType, Is

PAGE = Path(__file__).parent / "pages/lever/apply.html"
POSTING = "https://jobs.lever.co/acme/0a1b2c3d-0000-4000-8000-123456789abc"
CARD = "cards[11111111-1111-1111-1111-111111111111]"
SURVEY = "surveysResponses[22222222-2222-2222-2222-222222222222][responses][field0]"
OTHER_URL = "urls[Your resume's language: English]"  # a link the employer names, quote and all


def serve(route) -> None:
    url, method = route.request.url, route.request.method
    if method == "POST" and url.endswith("/apply"):
        # Lever answers with a 302, but a route never sees the redirect it fulfils: the
        # browser would fetch /thanks from the real site. A script redirect stays routed.
        body = f"<script>location.replace('{POSTING}/thanks')</script>"
        route.fulfill(content_type="text/html", body=body)
    elif url.endswith("/thanks"):
        route.fulfill(content_type="text/html", body="<h3>Application submitted!</h3>")
    elif "/0000" in url:
        route.fulfill(status=404, content_type="text/html", body="<title>Not found</title>")
    elif "hcaptcha.com" in url:
        route.fulfill(content_type="text/html", body="")
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
        page.context.route("https://newassets.hcaptcha.com/**", serve)
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
    return adapter, adapter.browser_read(page)


def test_browser_read_opens_the_application(page):
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
        OTHER_URL,
        *(f"{CARD}[field{n}]" for n in range(6)),
        SURVEY,
        "eeo[gender]",
        "eeo[race]",
        "eeo[veteran]",
        "eeo[disability]",
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
    assert by_id["eeo[disabilitySignatureDate]"].shown_when == Is("eeo[disability]")
    assert by_id["eeo[disabilitySignature]"].shown_when == Is("eeo[disability]")
    assert [f.id for f in fields if f.shown_when] == [
        "eeo[disabilitySignature]",
        "eeo[disabilitySignatureDate]",
    ]
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
        "location": "London, United Kingdom",  # the pick names the country by its code
        "org": "Acme",  # after the resume, whose parse fills it
        "resume": resume,
        "pronouns": ["They/them"],
        "opportunityLocationId": "Shelbyville",
        "urls[LinkedIn]": "https://example.com",
        OTHER_URL: "https://example.org",
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
        "eeo[disabilitySignatureDate]": "2026-10-20",  # before what shows it: filled after
        "eeo[disability]": "I do not want to answer",
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
    assert adapter.shown_fields(page) == [f.id for f in fields]


def test_a_follow_up_shows_only_once_its_answer_shows_it(page):
    adapter, fields = opened(page)
    assert not set(adapter.shown_fields(page)) & {"eeo[disabilitySignature]"}
    adapter.fill(page, {"eeo[disability]": "I do not want to answer"})
    assert "eeo[disabilitySignature]" in adapter.shown_fields(page)
    assert set(adapter.shown_fields(page)) == {f.id for f in fields}


def test_a_field_an_answer_shows_is_listed_shown(page):
    adapter, fields = opened(page)
    page.locator("#disabilitySignatureSection").evaluate(
        """section => section.parentElement.insertAdjacentHTML("beforeend",
          '<div class="application-question"><input name="cards[new][field0]"></div>')"""
    )
    assert set(adapter.shown_fields(page)) - {f.id for f in fields} == {"cards[new][field0]"}


def test_a_location_with_no_pick_reads_back_empty(page, monkeypatch):
    monkeypatch.setattr("atsfill.adapters.lever.SUGGEST_TIMEOUT_MS", 1_000)
    adapter, _ = opened(page)
    answers: dict[str, Answer] = {"location": "Nowhere"}
    adapter.fill(page, answers)
    assert page.locator("#location-input").input_value() == "Nowhere"
    assert "location" not in adapter.read_back(page)


def test_an_unknown_rendering_is_listed_unhandled():
    raw = {"id": "x", "label": "X", "kind": "slider", "required": True}
    assert _field(raw).unfillable == "unhandled_rendering: x is a slider"


def test_a_posting_gone(page):
    page.goto("https://jobs.lever.co/acme/00000000-0000-4000-8000-000000000000/apply")
    with pytest.raises(Unsupported, match="posting_gone"):
        Lever().browser_read(page)


def test_submit_returns_the_thanks_page(page):
    adapter, _ = opened(page)
    assert adapter.submit(page) == POSTING + "/thanks"


def test_an_hcaptcha_challenge_is_blocked(page, monkeypatch):
    monkeypatch.setattr("atsfill.adapters.lever.SUBMIT_TIMEOUT_MS", 1_000)
    adapter, _ = opened(page)
    page.evaluate("document.body.dataset.challenge = 'shown'")
    with pytest.raises(Blocked, match="captcha"):
        adapter.submit(page)


def test_a_timeout_without_a_challenge_is_an_error(page, monkeypatch):
    monkeypatch.setattr("atsfill.adapters.lever.SUBMIT_TIMEOUT_MS", 1_000)
    adapter, _ = opened(page)
    page.evaluate("document.body.dataset.stalled = 'yes'")
    with pytest.raises(browser.Timeout):
        adapter.submit(page)


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
