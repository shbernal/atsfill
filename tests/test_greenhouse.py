"""The Greenhouse adapter on tests/pages/greenhouse, served as a job board and an embedding page."""

from pathlib import Path

import pytest

from atsfill.adapters.greenhouse import Greenhouse, _api_fields
from atsfill.core import browser, matching
from atsfill.core.results import FieldType

PAGE = Path(__file__).parent / "pages/greenhouse/job.html"
JOB = "https://job-boards.greenhouse.io/acme/jobs/1234"
COMPANY = "https://www.example.com/careers?gh_jid=1234&gh_src=abc"


def serve(route) -> None:
    url = route.request.url
    if url.startswith("https://www.example.com/"):
        src = "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=1234&b=https%3A%2F%2Fx"
        route.fulfill(content_type="text/html", body=f'<iframe id="grnhse_iframe" src="{src}">')
    elif url.endswith("/confirmation"):
        route.fulfill(content_type="text/html", body="<h1>Thank you for applying</h1>")
    else:
        route.fulfill(path=PAGE)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        for host in ("https://job-boards.greenhouse.io/**", "https://www.example.com/**"):
            page.context.route(host, serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def opened(page, url: str = JOB) -> tuple[Greenhouse, list]:
    adapter = Greenhouse()
    page.goto(url)
    assert adapter.detect(url, page)
    return adapter, adapter.read_form(page)


def test_read_form_waits_for_the_live_page(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "first_name",
        "country",
        "phone",
        "candidate-location",
        "resume",
        "question_1",
        "question_2[]",
        "4000952002",
        "gdpr_processing_consent_given",
    ]
    assert by_id["country"].options == ["France +33", "Canada +1", "United States +1"]
    assert by_id["candidate-location"].type is FieldType.TYPEAHEAD
    assert by_id["question_2[]"].type is FieldType.MULTI_CHOICE
    assert by_id["gdpr_processing_consent_given"].label == "I agree to the processing of my data."
    assert by_id["resume"].accept == [".pdf", ".doc"]
    assert not by_id["4000952002"].required


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page)
    answers = {
        "first_name": "Ada",
        "country": "Canada +1",  # +1 is shared: the flag tells it apart
        "phone": "514 555 0100",
        "candidate-location": "Paris",
        "resume": resume,
        "question_1": "No",
        "question_2[]": ["French", "English"],
        "4000952002": "Decline To Self Identify",
        "gdpr_processing_consent_given": "Yes",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_submit_returns_the_confirmation(page):
    adapter, _ = opened(page)
    assert adapter.submit(page) == JOB + "/confirmation"


def test_an_embedding_company_page(page):
    adapter, fields = opened(page, COMPANY)
    assert (
        page.url == "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=1234&gh_src=abc"
    )
    assert adapter.posting(COMPANY) == JOB
    assert fields


def test_board_urls():
    adapter = Greenhouse()
    for url in (
        JOB,
        "https://boards.greenhouse.io/acme/jobs/1234?gh_src=x",
        "https://job-boards.eu.greenhouse.io/acme/jobs/1234",
        "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=1234",
    ):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == JOB
    assert not adapter.detect("https://job-boards.greenhouse.io/acme", None)


def test_api_fields():
    job = {
        "questions": [
            {
                "label": "First Name",
                "required": True,
                "fields": [{"name": "first_name", "type": "input_text"}],
            },
            {
                "label": "Phone",
                "required": True,
                "fields": [{"name": "phone", "type": "input_text"}],
            },
            {
                "label": "Resume/CV",
                "required": True,
                "fields": [
                    {"name": "resume", "type": "input_file"},
                    {"name": "resume_text", "type": "textarea"},
                ],
            },
            {
                "label": "Sponsorship?",
                "required": True,
                "fields": [
                    {
                        "name": "question_1",
                        "type": "multi_value_single_select",
                        "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}],
                    }
                ],
            },
        ],
        "location_questions": [
            {
                "label": "Latitude",
                "required": True,
                "fields": [{"name": "latitude", "type": "input_hidden"}],
            },
            {
                "label": "Location",
                "required": True,
                "fields": [{"name": "location", "type": "input_text"}],
            },
        ],
        "demographic_questions": {
            "questions": [
                {
                    "id": 4000952002,
                    "label": "Gender",
                    "required": False,
                    "type": "multi_value_single_select",
                    "answer_options": [{"label": "Male"}, {"label": "Female"}],
                },
            ]
        },
    }
    assert [(f.id, f.type, f.required) for f in _api_fields(job) or []] == [
        ("first_name", FieldType.TEXT, True),
        ("country", FieldType.CHOICE, True),
        ("phone", FieldType.PHONE, True),
        ("resume", FieldType.FILE, True),
        ("question_1", FieldType.CHOICE, True),
        ("candidate-location", FieldType.TYPEAHEAD, True),
        ("4000952002", FieldType.CHOICE, False),
    ]
    assert _api_fields(job | {"education": "education_optional"}) is None
    consent = [{"type": "gdpr", "requires_consent": True}]
    assert _api_fields(job | {"data_compliance": consent}) is None
