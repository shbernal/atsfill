"""The Breezy adapter on tests/pages/breezy, served as a position's application page."""

from pathlib import Path

import pytest

from atsfill.adapters import breezy
from atsfill.adapters.breezy import Breezy
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import Answer, FieldType

PAGE = Path(__file__).parent / "pages/breezy/apply.html"
POSTING = "https://acme.breezy.hr/p/0a1b2c3d4e5f"
APPLY = POSTING + "-engineer/apply"
CUSTOM = "https://jobs.acme.example/p/0a1b2c3d4e5f-engineer/apply"
REFERENCE = "section_222_question_3.reference{}.{}"


def serve(route) -> None:
    url, method = route.request.url, route.request.method
    if method == "POST" and "/api/apply/" in url:
        route.fulfill(status=200, json={"_id": "0a1b2c3d4e5f"})
    elif "assets-cdn.breezy.hr" in url:
        route.fulfill(content_type="text/javascript", body="")
    elif "/p/000000000000" in url:  # a closed position redirects to the openings
        route.fulfill(content_type="text/html", body="<title>Openings at Acme</title>")
    else:
        route.fulfill(path=PAGE)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.breezy.FORM_TIMEOUT_MS", 1_000)
        mp.setattr("atsfill.adapters.breezy.STEP_TIMEOUT_MS", 1_000)
        for host in ("acme.breezy.hr", "jobs.acme.example", "assets-cdn.breezy.hr"):
            page.context.route(f"https://{host}/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def opened(page, url: str = POSTING) -> tuple[Breezy, list]:
    adapter = Breezy()
    page.goto(url)
    assert adapter.detect(url, page)
    return adapter, adapter.browser_read(page)


def test_browser_read_opens_the_application(page):
    _, fields = opened(page)
    assert page.url == POSTING + "/apply"
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "cResume",
        "cName",
        "cEmail",
        "cPhoneNumber",
        "smsConsent",
        "cAddress",
        "salaryCurrency",
        "cSalary",
        "salaryPeriod",
        "cLocation",
        "cSummary",
        "cCoverLetter",
        *(f"section_111_question_{n}" for n in range(4)),
        *(f"section_222_question_{n}" for n in range(3)),
        *(REFERENCE.format(n, part) for n in (1, 2)
          for part in ("name", "email_address", "phone_number", "type")),
        "section_222_question_4",
        "race_ethnicity",
        "eeoc.veteran_status",
        "gdprAgreement",
        *(f"work_history.{k}" for k in ("company_name", "title", "date_start", "date_end",
                                         "summary")),
        *(f"education.{k}" for k in ("school_name", "field_of_study", "date_start", "date_end",
                                      "summary")),
    ]  # fmt: skip
    assert by_id["cResume"].label == "Resume" and by_id["cResume"].required
    assert by_id["cResume"].accept == [".pdf", ".doc", ".docx", ".txt", ".rtd", ".pages"]
    assert by_id["cPhoneNumber"].type is FieldType.PHONE
    assert by_id["smsConsent"].label.startswith("By providing your phone number")
    assert by_id["smsConsent"].options == ["Yes", "No"] and not by_id["smsConsent"].required
    assert not by_id["cAddress"].required
    assert by_id["salaryCurrency"].label == "Desired Salary: currency"
    assert by_id["cSalary"].label == "Desired Salary" and by_id["cSalary"].required
    assert by_id["salaryPeriod"].options == ["Hourly", "Yearly"]
    assert by_id["cLocation"].options == ["Springfield", "Shelbyville"]
    assert by_id["section_111_question_0"].label == "Are you comfortable working outdoors?"
    assert by_id["section_111_question_0"].options == ["YES", "NO"]
    assert by_id["section_111_question_1"].options == ["Yes", "No"]
    assert by_id["section_111_question_2"].type is FieldType.MULTI_CHOICE
    assert by_id["section_111_question_2"].options == ["LPN", "CNA"]
    assert not by_id["section_111_question_3"].required
    assert by_id["section_222_question_1"].type is FieldType.DATE
    assert by_id["section_222_question_2"].type is FieldType.FILE
    assert by_id[REFERENCE.format(2, "phone_number")].type is FieldType.PHONE
    assert by_id[REFERENCE.format(1, "type")].label == (
        "Professional references (reference 1 of 2): Relationship"
    )
    assert by_id[REFERENCE.format(1, "type")].options == ["Professional", "Personal"]
    assert by_id["race_ethnicity"].options[-1] == "I don't wish to answer"
    assert by_id["gdprAgreement"].required
    assert by_id["work_history.title"].label == "Work History: Title"
    assert by_id["work_history.title"].required
    assert not by_id["work_history.summary"].required
    assert by_id["education.date_start"].type is FieldType.DATE
    assert not by_id["education.school_name"].required


def test_every_answer_reads_back_across_the_steps(page, resume):
    adapter, fields = opened(page, APPLY)
    answers: dict[str, Answer] = {
        "cResume": resume,
        "cName": "Ada Placeholder",
        "cEmail": "ada@example.com",
        "cPhoneNumber": "+12025550143",
        "smsConsent": "No",
        "cAddress": "1 Placeholder St, Washington, DC",
        "salaryCurrency": "Euro (€)",
        "cSalary": "50000",
        "salaryPeriod": "Hourly",
        "cLocation": "Shelbyville",
        "cSummary": "Placeholder answer.",
        "work_history.company_name": "Acme",
        "work_history.title": "Engineer",
        "work_history.date_start": "2020-01-01",
        "education.school_name": "Placeholder University",
        "section_111_question_0": "NO",
        "section_111_question_1": "No",
        "section_111_question_2": ["LPN", "CNA"],
        "section_222_question_1": "2026-11-20",
        "section_222_question_2": resume,
        REFERENCE.format(1, "name"): "Jane Doe",
        REFERENCE.format(1, "phone_number"): "+12025550144",
        REFERENCE.format(2, "type"): "Personal",
        "race_ethnicity": "I don't wish to answer",
        "eeoc.veteran_status": "I AM NOT A PROTECTED VETERAN",
        "gdprAgreement": "Yes",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)
    assert page.get_by_text("Submit Application").is_visible()  # the last step
    assert set(adapter.shown_fields(page)) <= {f.id for f in fields}


def test_a_field_an_answer_shows_is_listed_shown(page):
    adapter, fields = opened(page, APPLY)
    page.locator(breezy.FORM).evaluate(
        """form => form.insertAdjacentHTML("beforeend", '<input name="section_111_question_9">')"""
    )
    assert set(adapter.shown_fields(page)) - {f.id for f in fields} == {"section_111_question_9"}


def test_a_step_breezy_keeps_stops_the_fill(page):
    adapter, _ = opened(page)
    adapter.fill(page, {"cName": "Ada Placeholder", "section_111_question_3": "Now"})
    assert page.locator("[data-section=application]").is_visible()


def test_a_video_question_is_out_of_scope(page):
    page.goto(APPLY)
    page.locator(".video-response .required").evaluate("e => e.classList.remove('ng-hide')")
    video = next(f for f in Breezy().browser_read(page) if f.unfillable)
    assert (video.label, video.required) == ("Introduce yourself on video", True)
    assert video.unfillable == f"out_of_scope: {video.id} is a video recorded live"


def test_a_video_question_keeps_its_id_when_a_field_is_added_before_it(page):
    page.goto(APPLY)
    video = next(f.id for f in Breezy().browser_read(page) if f.unfillable)
    page.locator("[name=cSummary]").evaluate(
        "e => e.insertAdjacentHTML('afterend', '<input type=text name=cExtra>')"
    )
    assert next(f.id for f in Breezy().browser_read(page) if f.unfillable) == video


def test_a_posting_gone(page):
    page.goto("https://acme.breezy.hr/p/000000000000-closed/apply")
    with pytest.raises(Unsupported, match="posting_gone"):
        Breezy().browser_read(page)


def test_submit_returns_the_application_response(page):
    adapter, _ = opened(page)
    page.evaluate("() => document.querySelector('[ng-click=\"apply()\"]').classList"
                  ".remove('ng-hide')")  # fmt: skip
    assert adapter.submit(page) == "https://acme.breezy.hr/api/apply/0a1b2c3d4e5f 200"


def test_posting_urls(page):
    adapter = Breezy()
    for url in (POSTING, POSTING + "-engineer", APPLY, POSTING + "-engineer?utm_source=x"):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == POSTING
    assert not adapter.detect("https://acme.breezy.hr/", None)
    assert not adapter.detect(CUSTOM, None)
    page.goto(CUSTOM)
    assert adapter.detect(CUSTOM, page)
    assert adapter.posting(CUSTOM) == "https://jobs.acme.example/p/0a1b2c3d4e5f"
