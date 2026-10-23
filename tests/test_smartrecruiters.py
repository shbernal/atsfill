"""The SmartRecruiters adapter on tests/pages/smartrecruiters, served as a job page and its form."""

from pathlib import Path

import pytest

from atsfill.adapters.smartrecruiters import SmartRecruiters
from atsfill.core import browser, matching
from atsfill.core.results import FieldType

PAGE = Path(__file__).parent / "pages/smartrecruiters/apply.html"
JOB = "https://jobs.smartrecruiters.com/Acme/123456-engineer"
FORM = "https://jobs.smartrecruiters.com/oneclick-ui/company/Acme/publication/p1?dcr_ci=Acme"


def serve(route) -> None:
    url = route.request.url
    if "/oneclick-ui/" in url:
        route.fulfill(path=PAGE)
    else:
        route.fulfill(content_type="text/html", body=f'<a href="{FORM}">I\'m interested</a>')


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://jobs.smartrecruiters.com/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def opened(page, url: str = JOB) -> tuple[SmartRecruiters, list]:
    adapter = SmartRecruiters()
    page.goto(url)
    assert adapter.detect(url, None)
    return adapter, adapter.read_form(page)


def test_read_form_lists_both_pages(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    assert page.url == FORM
    assert list(by_id) == [
        "first_name",
        "last_name",
        "email",
        "confirm_email",
        "location",
        "phone",
        "linkedin",
        "website",
        "resume",
        "message",
        "photo",
        "experience",
        "q-text",
        "q-radio",
        "q-select",
        "q-multi",
        "q-currency",
        "q-box",
        "q-eeo.gender",
        "q-eeo.ethnicity",
        "q-files",
        "q-textarea",
        "consent.RECRUIT",
        "consent.CRM",
    ]
    assert by_id["location"].type is FieldType.TYPEAHEAD
    assert by_id["phone"].type is FieldType.PHONE
    assert by_id["resume"].required and by_id["resume"].accept == [".doc", ".pdf"]
    assert not by_id["linkedin"].required
    assert by_id["q-text"].label == "Middle name (optional)"
    assert by_id["q-radio"].options == ["Yes", "No"]
    assert by_id["q-multi"].type is FieldType.MULTI_CHOICE
    assert not by_id["q-multi"].required  # asked only on one answer
    assert by_id["q-multi"].label == "Which entities? (asked when 'Did you work for Acme?' is Yes)"
    assert by_id["q-box"].options == ["Yes", "No"]
    assert by_id["q-eeo.gender"].label == "Gender, Race and Ethnicity: Gender"
    assert (
        by_id["consent.RECRUIT"].label
        == "You declare that you have read the privacy notice of Acme."
    )
    assert by_id["consent.RECRUIT"].required and not by_id["consent.CRM"].required
    assert by_id["photo"].label == "Upload a profile photo"
    assert {i: (f.unfillable, f.required) for i, f in by_id.items() if f.unfillable} == {
        "photo": ("unsupported_rendering: photo: photo is a profile photo", False),
        "experience": ("unsupported_rendering: entries: experience is a list of entries", False),
        "q-files": ("unsupported_rendering: attachments: q-files is a list of attachments", False),
    }


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page, FORM)
    answers = {
        "first_name": "Ada",
        "last_name": "Placeholder",
        "email": "ada@example.com",
        "confirm_email": "ada@example.com",
        "location": "Paris",
        "phone": "+12025550143",
        "linkedin": "https://www.linkedin.com/in/ada",
        "resume": resume,
        "message": "Hello.",
        "q-text": "Grace",
        "q-radio": "No",
        "q-select": "Yes",
        "q-multi": ["Acme Labs", "Acme Retail"],
        "q-currency": "50000",
        "q-box": "Yes",
        "q-eeo.gender": "Prefer not to answer",
        "q-eeo.ethnicity": "Asian",
        "q-textarea": "Placeholder answer.",
        "consent.RECRUIT": "Yes",
        "consent.CRM": "No",
    }
    adapter.fill(page, answers)
    assert page.url.endswith("/screening?dcr_ci=Acme") or "/screening" in page.url
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_a_conditional_question_left_hidden_reads_back_empty(page):
    adapter, fields = opened(page, FORM)
    answers = {"q-select": "No", "q-multi": ["Acme Labs"], "q-radio": "Yes"}
    adapter.fill(page, answers)
    read_back = adapter.read_back(page)
    assert "q-multi" not in read_back
    assert read_back["q-radio"] == "Yes"  # the next containers still map to their questions


def test_submit_returns_the_page_it_lands_on(page):
    adapter, _ = opened(page, FORM)
    adapter.fill(page, {})
    assert (adapter.submit(page) or "").startswith(
        "https://jobs.smartrecruiters.com/oneclick-ui/company/Acme/publication/p1/success"
    )


def test_required_experience_is_listed_required(page):
    page.goto(FORM)
    page.evaluate(
        """() => document.querySelector("oc-experience").insertAdjacentHTML("beforeend",
        '<span data-test="section-required-mark">*</span>')"""
    )
    experience = next(f for f in SmartRecruiters().read_form(page) if f.id == "experience")
    assert experience.required and experience.unfillable


def test_an_unknown_question_type_is_listed_unhandled():
    adapter = SmartRecruiters()
    entry = {"id": "value", "label": "Value", "type": "range", "values": []}
    question = {
        "id": "q",
        "type": "slider",
        "label": "How much?",
        "required": True,
        "fields": [entry],
    }
    adapter._questions = {"q": question}
    [field] = adapter._question_fields(question)
    assert field.unfillable == "unhandled_rendering: 'How much?' is a slider question"


def test_posting_urls():
    adapter = SmartRecruiters()
    for url in (JOB, "https://jobs.smartrecruiters.com/Acme/123456?utm_source=x"):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == "https://jobs.smartrecruiters.com/Acme/123456"
    assert adapter.posting(FORM) == (
        "https://jobs.smartrecruiters.com/oneclick-ui/company/Acme/publication/p1"
    )
    assert adapter.detect("https://www.smartr.me/oneclick-ui/company/Acme/publication/p1", None)
    assert not adapter.detect("https://jobs.smartrecruiters.com/Acme", None)
    assert not adapter.detect("https://careers.example.com/Acme/123456", None)
