"""The Workable adapter on tests/pages/workable, served as a posting's application page."""

from pathlib import Path

import pytest

from atsfill.adapters.workable import Workable, _api_fields
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import Answer, FieldType

PAGE = Path(__file__).parent / "pages/workable/apply.html"
JOB = "https://apply.workable.com/acme/j/ABCDEF1234/"
POSTING = "https://apply.workable.com/j/ABCDEF1234"


def serve(route) -> None:
    url = route.request.url
    if url.endswith("/apply"):
        route.fulfill(status=201, content_type="application/json", body="{}")
    elif "GONE" in url:
        route.fulfill(content_type="text/html", body="<h1>Page not found</h1>")
    else:
        route.fulfill(path=PAGE)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.workable.FORM_TIMEOUT_MS", 1_000)
        mp.setattr("atsfill.adapters.workable.COOKIE_TIMEOUT_MS", 1_000)
        page.context.route("https://apply.workable.com/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def opened(page) -> tuple[Workable, list]:
    adapter = Workable()
    page.goto(JOB)
    assert adapter.detect(JOB, None)
    return adapter, adapter.read_form(page)


def test_read_form_opens_the_application(page):
    _, fields = opened(page)
    assert page.url == POSTING + "/apply/"
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "firstname",
        "email",
        "phone",
        "address",
        "avatar",
        "resume",
        "CA_1",
        "cover_letter",
        "CA_2",
        "QA_1",
        "CA_3",
        "CA_4",
        "CA_5",
        "QA_2",
        "gdpr",
    ]  # the optional education entry left out
    assert by_id["phone"].type is FieldType.PHONE
    assert by_id["CA_1"].options == ["Immediately", "1 month"]
    assert by_id["CA_2"].type is FieldType.TEXT
    assert by_id["QA_1"].options == ["Yes", "No"]
    assert by_id["CA_3"].options == ["Lyon", "Nantes"]
    assert by_id["CA_4"].type is FieldType.MULTI_CHOICE
    assert by_id["CA_5"].type is FieldType.DATE
    assert by_id["avatar"].accept == [".jpg", ".jpeg", ".png"]
    assert by_id["firstname"].max_length == 127
    assert by_id["gdpr"].label.startswith("I accept the Privacy Notice")
    assert by_id["gdpr"].required
    assert not by_id["address"].required
    assert not by_id["CA_5"].required


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {
        "firstname": "Ada",
        "email": "ada@example.com",
        "phone": "+12025550143",
        "address": "London",
        "resume": resume,
        "CA_1": "1 month",
        "cover_letter": "Placeholder answer.",
        "CA_2": "50000",
        "QA_1": "No",
        "CA_3": "Nantes",
        "CA_4": ["Days", "Nights"],
        "CA_5": "1990-12-25",
        "QA_2": resume,
        "gdpr": "Yes",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)
    assert page.locator("[name=CA_2]").input_value() == "50,000"


def test_a_number_input_keeps_digits_only(page):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {"CA_2": "a month"}
    adapter.fill(page, answers)
    assert not matching.check(fields, answers, adapter.read_back(page))[0].match


def test_a_required_entry_is_not_supported(page):
    page.goto(POSTING + "/apply/")
    page.evaluate("() => document.querySelector('#education_label small').remove()")
    with pytest.raises(Unsupported, match="form_shape: education is a group"):
        Workable().read_form(page)


def test_a_posting_gone(page):
    page.goto("https://apply.workable.com/j/GONE")
    with pytest.raises(Unsupported, match="posting_gone"):
        Workable().read_form(page)


def test_submit_returns_the_apply_answer(page):
    adapter, _ = opened(page)
    page.evaluate("() => { document.querySelector('form').noValidate = true }")  # left empty
    assert adapter.submit(page) == "https://apply.workable.com/api/v1/jobs/ABCDEF1234/apply 201"


def test_posting_urls():
    adapter = Workable()
    for url in (
        JOB,
        POSTING,
        "https://apply.workable.com/j/ABCDEF1234?utm_source=x",
        "https://apply.workable.com/acme/j/ABCDEF1234/apply/",
    ):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == POSTING
    assert not adapter.detect("https://apply.workable.com/acme/", None)
    assert not adapter.detect("https://www.example.com/j/ABCDEF1234", None)


def test_api_fields():
    form = [
        {
            "name": "Personal information",
            "fields": [
                {"id": "firstname", "label": "First name", "required": True, "type": "text"},
                {"id": "phone", "label": "Phone", "required": True, "type": "phone"},
                {
                    "id": "resume",
                    "label": "Resume",
                    "required": True,
                    "type": "file",
                    "supportedFileTypes": [".pdf"],
                },
            ],
        },
        {
            "name": "Profile",
            "fields": [
                {"id": "education", "label": "Education", "required": False, "type": "group"},
                {"id": "CA_1", "label": "Salary", "required": True, "type": "number"},
                {"id": "QA_1", "label": "EU?", "required": True, "type": "boolean"},
                {
                    "id": "CA_4",
                    "label": "Shifts",
                    "required": True,
                    "type": "multiple",
                    "singleOption": False,
                    "options": [{"name": "1", "value": "Days"}, {"name": "2", "value": " Nights"}],
                },
                {"id": "CA_5", "label": "Born", "required": False, "type": "date"},
            ],
        },
    ]
    assert [(f.id, f.type, f.required, f.options) for f in _api_fields(form) or []] == [
        ("firstname", FieldType.TEXT, True, None),
        ("phone", FieldType.PHONE, True, None),
        ("resume", FieldType.FILE, True, None),
        ("CA_1", FieldType.TEXT, True, None),
        ("QA_1", FieldType.CHOICE, True, ["Yes", "No"]),
        ("CA_4", FieldType.MULTI_CHOICE, True, ["Days", "Nights"]),
        ("CA_5", FieldType.DATE, False, None),
    ]
    required = {"id": "experience", "label": "Experience", "required": True, "type": "group"}
    assert _api_fields([{"fields": [required]}]) is None
    assert _api_fields([{"fields": [{"id": "x", "required": True, "type": "signature"}]}]) is None
