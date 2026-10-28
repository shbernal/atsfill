"""The Recruiterflow adapter on tests/pages/recruiterflow/form.html, served as recruiterflow.com."""

import io
from pathlib import Path

import pytest

from atsfill.adapters.recruiterflow import Recruiterflow
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import FieldType
from tests import testuser

PAGE = Path(__file__).parent / "pages/recruiterflow/form.html"
URL = "https://recruiterflow.com/acme/jobs/1"
CLOSED = "https://recruiterflow.com/acme/jobs/2"
FAILING = "https://recruiterflow.com/acme/jobs/3"


def serve(route) -> None:
    url = route.request.url
    if url.endswith("/api/file/upload"):
        route.fulfill(json={"status": "success"})
    elif url.endswith("/acme/jobs/1/submit-application"):
        route.fulfill(json={"message": "Application submitted", "RESULT": "SUCCESS"})
    elif url.endswith("/acme/jobs/3/submit-application"):
        route.fulfill(json={"message": "Already applied", "RESULT": "FAILURE"})
    elif url.startswith(CLOSED):
        route.fulfill(content_type="text/html", body=PAGE.read_text().replace(
            '"job_visibility_id": 1', '"job_visibility_id": 6'))  # fmt: skip
    elif url.startswith((URL, FAILING)):
        route.fulfill(path=PAGE)
    else:
        route.fulfill(status=404, content_type="text/html", body="<h1>Not found</h1>")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.recruiterflow.FORM_TIMEOUT_MS", 3_000)
        mp.setattr("atsfill.adapters.recruiterflow.SUBMIT_TIMEOUT_MS", 5_000)
        page.context.route("https://recruiterflow.com/**", serve)
        yield page


def opened(page, url: str = URL) -> tuple[Recruiterflow, list]:
    adapter = Recruiterflow()
    page.goto(url)
    return adapter, adapter.browser_read(page)


def test_detect_and_posting():
    adapter = Recruiterflow()
    assert adapter.detect(f"{URL}?utm_source=freehire.me", None)
    assert adapter.posting(f"{URL}/?utm_source=freehire.me") == URL
    assert not adapter.detect("https://recruiterflow.com/acme/jobs", None)
    assert not adapter.detect("https://example.com/acme/jobs/1", None)


def test_browser_read_lists_the_drawn_fields_in_order(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "personal_info.first_name",
        "personal_info.last_name",
        "personal_info.email",
        "personal_info.phone",
        "personal_info.location.city",
        "personal_info.location.state",
        "personal_info.location.country",
        "personal_info.location.postal_code",
        "candidate_profile.school.0",
        "candidate_profile.degree.0",
        "candidate_profile.from-graduation-year.0",
        "candidate_profile.to-graduation-year.0",
        "candidate_profile.resume",
        "candidate_profile.summary",
        "candidate_profile.company-name.0",
        "candidate_profile.designation.0",
        "candidate_profile.from-experience-year.0",
        "candidate_profile.to-experience-year.0",
        "question.101",
        "question.102",
        "question.103",
        "question.104",
        "question.105",
        "question.106",
        "question.107",
        "question.108",
        "question.109.min",
        "question.109.max",
        "question.110",
        "question.111",
        "question.112",
        "mandatory-opt-in-checkbox",
    ]  # LinkedIn and the cover letter are `off`, not drawn
    assert by_id["personal_info.phone"].type is FieldType.PHONE
    assert not by_id["personal_info.phone"].required
    assert by_id["personal_info.location.country"].type is FieldType.TYPEAHEAD
    assert by_id["personal_info.location.postal_code"].required
    assert by_id["candidate_profile.resume"].accept == [".doc", ".docx", ".txt", ".rtf", ".pdf"]
    # a required list's first entry: its first three are checked
    assert [by_id[f"candidate_profile.{k}.0"].required for k in (
        "company-name", "designation", "from-experience-year", "to-experience-year"
    )] == [True, True, True, False]  # fmt: skip
    assert by_id["candidate_profile.from-experience-year.0"].label.endswith("(MM/YYYY)")
    assert by_id["question.102"].label == "Tell us more about yourself"
    assert by_id["question.103"].label.endswith("(a number)")
    assert by_id["question.105"].type is FieldType.DATE
    assert by_id["question.106"].options == ["Yes", "No"]
    assert by_id["question.107"].options == ["Online", "Referral"]
    assert by_id["question.108"].type is FieldType.MULTI_CHOICE
    assert by_id["question.110"].type is FieldType.FILE
    assert by_id["question.111"].options == [
        "Backend", "Engineering: Other", "Product design", "Design: Other"
    ]  # fmt: skip
    assert by_id["question.112"].unfillable == "unhandled_rendering: question.112 is a view field 3"
    consent = by_id["mandatory-opt-in-checkbox"]
    assert consent.label == "Acme is an equal opportunity employer."
    assert consent.required and consent.options == ["Yes", "No"]


def test_request_read_lists_what_browser_read_does(page, monkeypatch):
    _, fields = opened(page)
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout: io.BytesIO(PAGE.read_bytes())
    )
    assert Recruiterflow().request_read(URL) == fields


def test_a_closed_posting_is_gone(page):
    with pytest.raises(Unsupported, match="^posting_gone"):
        opened(page, CLOSED)


def test_dry_run_reads_back_every_answer(page, tmp_path):
    adapter, fields = opened(page)
    cv = testuser.resume(tmp_path / "CV Ada.pdf")
    answers = {
        f.id: a for f in fields if not f.unfillable and (a := testuser.answer(f, cv)) is not None
    }
    answers |= {
        "candidate_profile.from-experience-year.0": "03/2019",
        "question.105": "2030-01-31",
        "question.108": ["Remote", "On site"],
        "question.109.min": "50000",
        "question.111": ["Engineering: Other", "Product design"],
    }
    adapter.fill(page, answers)
    read_back = adapter.read_back(page)
    assert read_back["candidate_profile.resume"] == "CV Ada.pdf"
    assert read_back["personal_info.location.country"] == "United States"
    assert read_back["question.111"] == ["Engineering: Other", "Product design"]
    checks = matching.check(fields, answers, read_back)
    assert [c for c in checks if not c.match] == []


def test_submit_returns_the_post_as_evidence(page):
    adapter, _ = opened(page)
    evidence = adapter.submit(page)
    assert evidence == f"{URL}/submit-application 200"


def test_submit_fails_on_a_failure_answer(page):
    adapter, _ = opened(page, FAILING)
    with pytest.raises(RuntimeError, match="Already applied"):
        adapter.submit(page)
