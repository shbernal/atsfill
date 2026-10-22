"""The Manatal adapter on tests/pages/manatal, served as a career page and its endpoints."""

import json
from pathlib import Path

import pytest

from atsfill.adapters.manatal import Manatal
from atsfill.core import browser, matching
from atsfill.core.results import FieldType

PAGES = Path(__file__).parent / "pages/manatal"
JOB = "https://www.careers-page.com/acme/job/ABC123?utm_source=board"
FORM = json.loads((PAGES / "form.json").read_text())
NATIONALITIES = [
    {"id": 236, "common_name": "United States", "demonym": "American"},
    {"id": 75, "common_name": "France", "demonym": "French"},
    {"id": 196, "common_name": "Singapore", "demonym": "Singaporean"},
]
submitted: list[str] = []


def serve(route, form: list[dict]) -> None:
    request = route.request
    if request.url.endswith("/application-form/") and request.method == "POST":
        submitted.append(request.url)
        route.fulfill(status=201, json={})
    elif request.url.endswith("/application-form/"):
        route.fulfill(json=form)
    elif request.url.endswith("/apply"):
        route.fulfill(path=PAGES / "apply.html")
    else:
        route.fulfill(content_type="text/html", body='<a href="apply">Apply</a>')


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def opened(page, form: list[dict] = FORM) -> tuple[Manatal, list]:
    page.context.unroute("https://www.careers-page.com/**")
    page.context.route("https://www.careers-page.com/**", lambda r: serve(r, form))
    adapter = Manatal()
    page.goto(JOB)
    assert adapter.detect(JOB, None)
    return adapter, adapter.read_form(page)


def test_detect_and_posting():
    adapter = Manatal()
    assert adapter.posting(JOB + "/apply") == adapter.posting(JOB)
    assert adapter.posting(JOB) == "https://www.careers-page.com/acme/job/ABC123"
    assert not adapter.detect("https://www.careers-page.com/acme", None)


def test_read_form_lists_each_rendering(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    ids = [str(i) for i in range(1, 23)]
    assert list(by_id) == [*ids[:18], "18.other", *ids[18:], "terms_and_condition"]
    gap = "unsupported_rendering: other: 18.other is the text box an answer of Other opens"
    assert (by_id["18.other"].unfillable, by_id["18.other"].required) == (gap, False)
    gap = "unsupported_rendering: entries: 22 is a list of educations"
    assert (by_id["22"].unfillable, by_id["22"].required) == (gap, False)
    assert by_id["3"].type is FieldType.PHONE
    assert by_id["5"].type is FieldType.FILE and by_id["5"].accept == [
        ".pdf",
        ".doc",
        ".docx",
        ".rtf",
    ]
    assert by_id["12"].type is FieldType.MULTI_CHOICE
    assert by_id["12"].options == [
        "American (United States)",
        "French (France)",
        "Singaporean (Singapore)",
    ]
    assert by_id["13"].options is not None and by_id["13"].options[:2] == ["Immediately", "10 days"]
    assert by_id["15"].options == ["Male", "Female", "Other"]
    assert by_id["16"].options == ["Citizen", "Permanent resident", "Need a permit"]
    assert by_id["17"].type is FieldType.CHOICE and by_id["17"].options == [
        "Yes",
        "No",
        "Más tarde",
    ]
    assert by_id["18"].type is FieldType.MULTI_CHOICE and not by_id["18"].required
    assert by_id["20"].options == ["Yes", "No"]
    assert by_id["21"].type is FieldType.DATE
    assert by_id["terms_and_condition"].required


def test_read_api_lists_the_same_fields(page, monkeypatch):
    adapter, fields = opened(page)
    html = (PAGES / "apply.html").read_bytes()

    class Body:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self) -> bytes:
            return self.body

    def urlopen(request, timeout):
        url = request.full_url
        if url.endswith("/application-form/"):
            return Body(json.dumps(FORM).encode())
        if url.endswith("/nationalities/"):
            return Body(json.dumps(NATIONALITIES).encode())
        return Body(html)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    over_http = adapter.read_api(JOB)
    shape = lambda fs: [(f.id, f.type, f.required, f.options) for f in fs]  # noqa: E731
    assert over_http is not None and shape(over_http) == shape(fields)


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page)
    answers = {
        "1": "Ada Placeholder",
        "2": "ada@example.com",
        "3": "+12025550143",
        "4": "Hello.",
        "5": resume,
        "6": resume,
        "7": "https://www.linkedin.com/in/ada",
        "8": "Engineer",
        "9": "Placeholder answer.",
        "10": "50000",
        "11": "3",
        "12": ["French (France)", "Singaporean (Singapore)"],
        "13": "30 days",
        "14": "3+ year",
        "15": "Female",
        "16": "Permanent resident",
        "17": "Más tarde",
        "18": ["CRM", "Hojas de cálculo"],
        "19": ["Europe"],
        "20": "Yes",
        "21": "1990-05-17",
        "terms_and_condition": "Yes",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_a_number_field_left_with_text_reads_back_empty(page):
    adapter, fields = opened(page)
    assert {f.id: f.label for f in fields}["11"] == "Years in sales (a number)"
    adapter.fill(page, {"11": "three"})
    assert "11" not in adapter.read_back(page)


def test_submit_posts_the_form(page):
    adapter, _ = opened(page)
    submitted.clear()
    assert (adapter.submit(page) or "").startswith("POST application-form → 201")
    assert submitted


def test_required_entries_are_listed_required(page):
    form = [*FORM[:-1], {**FORM[-1], "is_required": True}]
    _, fields = opened(page, form)
    assert fields[-2].id == "22" and fields[-2].required and fields[-2].unfillable
