"""The Personio adapter on tests/pages/personio, served as an apply page and its form API."""

import io
from pathlib import Path

import pytest

from atsfill.adapters.personio import Personio, _form, _parse
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import Answer, FieldType
from tests import testuser

PAGES = Path(__file__).parent / "pages/personio"
JOB = "https://acme.jobs.personio.com/job/1234567"
HTML = (PAGES / "apply.html").read_text()


def serve(route) -> None:
    url, method = route.request.url, route.request.method
    if method == "POST" and url.endswith("/api/v1/documents"):
        route.fulfill(status=201, json={"uuid": "0000"})
    elif method == "POST":
        route.fulfill(status=201, json={})
    elif "/7654321" in url:
        route.fulfill(
            status=404, content_type="text/html", body="<h1>Diese URL existiert nicht</h1>"
        )
    else:
        route.fulfill(path=PAGES / "apply.html")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.personio.FORM_TIMEOUT_MS", 3_000)
        page.context.route("https://acme.jobs.personio.com/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    return str(testuser.resume(tmp_path / "CV Ada.pdf"))


def opened(page, url: str = JOB) -> tuple[Personio, list]:
    adapter = Personio()
    page.goto(url)
    assert adapter.detect(url, None)
    return adapter, adapter.read_form(page)


def test_read_form(page):
    _, fields = opened(page)
    assert page.url == f"{JOB}/apply"  # the job page's form is on its apply page
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "first_name",
        "last_name",
        "email",
        "phone",
        "available_from",
        "gender",
        "birthday",
        "custom_attribute_1001",
        "custom_attribute_1002",
        "custom_attribute_1003",
        "documents.cv",
        "documents.cover-letter",
    ]
    assert not any(f.unfillable for f in fields)
    assert by_id["phone"].type is FieldType.PHONE
    assert by_id["first_name"].label == "Vorname"
    assert by_id["first_name"].required  # its group's label is starred
    assert by_id["email"].label == "E-Mail"
    assert by_id["first_name"].max_length == 255
    assert by_id["gender"].options == ["Männlich", "Weiblich", "Divers", "Unbestimmt"]
    assert by_id["documents.cover-letter"].label == "Anschreiben"
    assert by_id["custom_attribute_1002"].type is FieldType.CHOICE
    assert by_id["custom_attribute_1002"].required
    assert by_id["birthday"].type is FieldType.DATE
    assert by_id["documents.cv"].type is FieldType.FILE
    assert by_id["documents.cv"].required
    assert not by_id["documents.cover-letter"].required
    assert ".pdf" in (by_id["documents.cv"].accept or [])


def test_read_api_lists_the_same_fields(page, monkeypatch):
    _, fields = opened(page)
    fetched = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

    def urlopen(request, timeout):
        fetched.append(request.full_url)
        return Response(HTML.encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert Personio().read_api(f"{JOB}?language=de&utm_source=x") == fields
    assert fetched == [f"{JOB}/apply?language=de"]


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {
        "first_name": "Ada",
        "last_name": "Placeholder",
        "email": "ada@example.com",
        "phone": "+12025550143",
        "available_from": "As soon as possible",
        "gender": "Unbestimmt",
        "birthday": "1990-01-31",
        "custom_attribute_1001": "https://example.com",
        "custom_attribute_1002": "Yes",
        "custom_attribute_1003": "2026-12-01",
        "documents.cv": resume,
        "documents.cover-letter": resume,
    }
    adapter.fill(page, answers)
    assert page.locator("select[name=gender]").input_value() == "undefined"
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_the_test_user_answers_every_field(page, tmp_path):
    adapter, fields = opened(page)
    answers = testuser.answers(fields, testuser.resume(tmp_path / "cv.pdf"))
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []


def test_a_posting_gone(page):
    with pytest.raises(Unsupported, match="posting_gone"):
        opened(page, "https://acme.jobs.personio.com/job/7654321")


def test_an_unknown_control_is_unhandled():
    html = HTML.replace('type="email"', 'type="checkbox"').replace(
        '<select name="gender"', '<select multiple name="gender"'
    )
    fields = {f.id: f for f, _ in _form(_parse(html))}
    assert fields["email"].unfillable == "unhandled_rendering: email is a checkbox"
    assert fields["gender"].unfillable == "unhandled_rendering: gender is a select"


def test_submit_returns_the_api_answer(page):
    adapter, _ = opened(page)
    evidence = "https://acme.jobs.personio.com/api/v1/jobs/1234567/application 201"
    assert adapter.submit(page) == evidence


def test_posting_urls():
    adapter = Personio()
    for url in (
        JOB,
        f"{JOB}/apply",
        f"{JOB}?language=de&display=en",
        "https://acme.jobs.personio.de/job/1234567",
    ):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == JOB
    assert not adapter.detect("https://acme.jobs.personio.com/", None)
    assert not adapter.detect("https://www.example.com/job/1234567", None)
