"""The Pinpoint adapter on tests/pages/pinpoint, served as an employer's apply page."""

import io
from pathlib import Path

import pytest

from atsfill.adapters.pinpoint import Pinpoint, _form, _parse
from atsfill.core import browser, matching
from atsfill.core.adapter import NotTaken, Unsupported
from atsfill.core.results import Answer, FieldType
from tests import testuser

PAGES = Path(__file__).parent / "pages/pinpoint"
UUID = "1a2b3c4d-0000-4000-8000-000000000001"
JOB = f"https://acme.pinpointhq.com/en/postings/{UUID}"
APPLY = f"{JOB}/applications/new"
HTML = (PAGES / "apply.html").read_text()


def serve(route) -> None:
    url, method = route.request.url, route.request.method
    if method == "POST" and url.endswith("/applications"):
        route.fulfill(status=302, headers={"Location": f"{JOB}/applications/1/thanks"})
    elif "/7654321a-" in url:
        route.fulfill(status=404, content_type="text/html", body="<h1>Not found</h1>")
    else:
        route.fulfill(path=PAGES / "apply.html")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.pinpoint.FORM_TIMEOUT_MS", 3_000)
        mp.setattr("atsfill.adapters.pinpoint.OPTION_TIMEOUT_MS", 1_000)
        mp.setattr("atsfill.adapters.pinpoint.SUBMIT_TIMEOUT_MS", 10_000)
        page.context.route("https://acme.pinpointhq.com/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    return str(testuser.resume(tmp_path / "CV Ada.pdf"))


def opened(page, url: str = JOB) -> tuple[Pinpoint, list]:
    adapter = Pinpoint()
    page.goto(url)
    assert adapter.detect(url, None)
    return adapter, adapter.read_form(page)


def test_read_form(page):
    _, fields = opened(page)
    assert page.url == APPLY  # the posting's form is on its apply page
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "first_name",
        "preferred_name",
        "last_name",
        "email",
        "phone",
        "date_of_birth",
        "country",
        "address1",
        "address2",
        "city",
        "town",
        "state",
        "postcode",
        "cv",
        "summary",
        "question_1001",
        "question_1002",
        "question_1003",
        "question_1004",
        "question_1005",
        "question_1006",
        "question_1007",
        "question_1008",
        "question_1009",
        "question_1010",
        "question_1011",
        "equality_monitoring_gender",
        "process_information",
    ]
    assert not any(f.unfillable for f in fields)
    assert by_id["first_name"].required and by_id["first_name"].max_length == 255
    assert not by_id["preferred_name"].required
    assert by_id["phone"].type is FieldType.PHONE and by_id["phone"].required
    assert by_id["date_of_birth"].type is FieldType.DATE
    assert by_id["date_of_birth"].label == "Date of Birth"
    assert by_id["country"].options == [
        "United Kingdom",
        "United States Minor Outlying Islands",
        "United States",
    ]
    assert by_id["state"].label == 'State (asked when "Country" is United States)'
    assert by_id["town"].label == 'Town (asked when "Country" is not United States)'
    assert by_id["cv"].type is FieldType.FILE and by_id["cv"].required
    assert by_id["cv"].label == "Résumé / CV"
    assert by_id["cv"].accept == [".doc", ".docx", ".pdf"]
    assert by_id["question_1003"].label == "Years of experience (a number)"
    assert by_id["question_1005"].options == ["Yes", "No"]
    follow_up = by_id["question_1006"]
    assert (
        follow_up.label
        == 'Which adjustments? (asked when "Do you require any adjustments?" is Yes)'
    )
    assert not follow_up.required
    assert by_id["question_1007"].type is FieldType.CHOICE
    assert by_id["question_1008"].type is FieldType.MULTI_CHOICE
    assert by_id["question_1009"].type is FieldType.DATE
    assert by_id["question_1011"].type is FieldType.FILE
    assert by_id["equality_monitoring_gender"].options == ["Male", "Female", "Prefer Not To Say"]
    assert by_id["process_information"].label.startswith("(Required) Allow us")
    assert by_id["process_information"].required


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
    assert Pinpoint().read_api(f"{JOB}?utm_source=x") == fields
    assert fetched == [APPLY]


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {
        "first_name": "Ada",
        "last_name": "Placeholder",
        "email": "ada@example.com",
        "phone": "+12025550143",
        "date_of_birth": "1990-01-31",
        "country": "United States",
        "address1": "1 Placeholder Street",
        "city": "Washington",
        "state": "Washington DC",
        "postcode": "20001",
        "cv": resume,
        "summary": "Placeholder summary.",
        "question_1001": "One month",
        "question_1003": "3",
        "question_1004": "https://example.com",
        "question_1005": "Yes",
        "question_1006": "A quiet room.",
        "question_1007": "Part time",
        "question_1008": ["Figma", "Other"],
        "question_1009": "2026-12-01",
        "question_1010": resume,
        "question_1011": resume,
        "equality_monitoring_gender": "Prefer Not To Say",
        "process_information": "Yes",
    }
    adapter.fill(page, answers)
    read_back = adapter.read_back(page)
    assert "town" not in read_back and "address2" not in read_back
    checks = matching.check(fields, answers, read_back)
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_an_address_outside_the_united_states(page):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {
        "country": "United Kingdom",
        "address1": "1 Placeholder Street",
        "address2": "Flat 2",
        "town": "London",
        "postcode": "SW1A 1AA",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []


def test_the_test_user_answers_every_field(page, tmp_path):
    adapter, fields = opened(page)
    answers = testuser.answers(fields, testuser.resume(tmp_path / "cv.pdf"))
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []


def test_an_option_the_page_lacks_is_not_taken(page):
    adapter, _ = opened(page)
    with pytest.raises(NotTaken, match="no option '1850'"):
        adapter.fill(page, {"date_of_birth": "1850-01-31"})


def test_a_posting_gone(page):
    with pytest.raises(Unsupported, match="posting_gone"):
        opened(page, "https://acme.pinpointhq.com/en/postings/7654321a-0000-0000-0000-000000000000")


def test_an_unknown_control_is_unhandled():
    html = HTML.replace('type="email"', 'type="color"').replace(
        "Questions::Url", "Questions::Video"
    )
    form = next(n for n in _parse(html).walk() if n.attrs.get("id") == "application-form")
    fields = {f.id: f for f, _ in _form(form, Pinpoint())}
    assert fields["email"].unfillable == "unhandled_rendering: email is a color"
    assert fields["question_1004"].unfillable == "unhandled_rendering: question_1004 is a Video"


def test_submit_returns_the_page_after(page):
    adapter, _ = opened(page)
    page.evaluate(
        "document.querySelectorAll('[required]').forEach(e => e.removeAttribute('required'))"
    )
    adapter.fill(page, {})
    evidence = adapter.submit(page)
    assert evidence == f"{JOB}/applications/1/thanks"


def test_posting_urls():
    adapter = Pinpoint()
    for url in (
        JOB,
        APPLY,
        f"{JOB}?utm_source=freehire",
        f"https://careers.example.com/en/postings/{UUID}",
        f"https://acme.pinpointhq.com/de/postings/{UUID}",
    ):
        assert adapter.detect(url, None)
    assert adapter.posting(APPLY) == JOB
    assert adapter.posting(f"https://acme.pinpointhq.com/de/postings/{UUID}/") == JOB
    assert not adapter.detect("https://acme.pinpointhq.com/", None)
    assert not adapter.detect("https://www.example.com/en/postings/1234", None)
