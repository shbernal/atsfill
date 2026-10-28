"""The JobAffinity adapter on tests/pages/jobaffinity: the older form with its questionnaire, and
the newer form, served as jobaffinity.fr."""

import io
from pathlib import Path

import pytest

from atsfill.adapters.jobaffinity import JobAffinity
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import Answer, FieldType, Is
from tests import testuser

PAGES = Path(__file__).parent / "pages/jobaffinity"
OLD = "https://jobaffinity.fr/apply/acme0000000000000a"
NEW = "https://jobaffinity.fr/apply/acme0000000000000b"
QUESTIONNAIRE = (
    "https://jobaffinity.fr/newcandidate/questionnaire/w1zard00/1001?session_id=5e55&ui=False"
)


def serve(route) -> None:
    url, method = route.request.url, route.request.method
    if method == "POST" and "/application_form/submission/candidate_application" in url:
        route.fulfill(status=200, json={"ok": True, "action": []})
    elif method == "POST" and url.startswith(OLD):  # JobAffinity redirects to the questionnaire
        route.fulfill(path=PAGES / "questionnaire.html")
    elif method == "POST" and "/newcandidate/questionnaire/" in url:
        route.fulfill(content_type="text/html", body="<p>Merci pour votre candidature</p>")
    elif "/newcandidate/questionnaire/" in url:
        route.fulfill(path=PAGES / "questionnaire.html")
    elif url.startswith(OLD):
        route.fulfill(path=PAGES / "old.html")
    elif url.startswith(NEW):
        route.fulfill(path=PAGES / "new.html")
    else:
        route.fulfill(status=404, content_type="text/html", body="<h1>Erreur 404 Not Found</h1>")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.jobaffinity.FORM_TIMEOUT_MS", 3_000)
        page.context.route("https://jobaffinity.fr/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    return str(testuser.resume(tmp_path / "CV Ada.pdf"))


def opened(page, url: str) -> tuple[JobAffinity, list]:
    adapter = JobAffinity()
    page.goto(url)
    assert adapter.detect(url, None)
    return adapter, adapter.browser_read(page)


def test_browser_read_the_older_form_and_its_questionnaire(page):
    _, fields = opened(page, OLD)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "cv",
        "other",
        "title",
        "lastName",
        "firstName",
        "eMail",
        "mobilePhone",
        "availability",
        "availability_date",
        "salary_1",
        "comment",
        "gdpr_agreement",
        "question_11",
        "question_12",
        "question_13",
    ]  # the address its page's styles hide, the experience JobAffinity's do, are left out
    assert not any(f.unfillable for f in fields)
    assert by_id["cv"].type is FieldType.FILE and by_id["cv"].required
    assert not by_id["other"].required
    assert by_id["lastName"].label == "Nom de famille" and by_id["lastName"].required
    assert by_id["title"].options == ["M.", "Mme"]
    assert by_id["mobilePhone"].type is FieldType.PHONE
    assert by_id["availability_date"].type is FieldType.DATE
    assert by_id["availability_date"].shown_when == Is(
        "availability", ["Disponible à partir du..."]
    )
    assert by_id["gdpr_agreement"].options == ["Yes", "No"]
    assert by_id["gdpr_agreement"].required
    assert by_id["question_11"].options == ["Ingénieur", "Technicien"]
    assert by_id["question_12"].label == "Quel type de contrat recherchez-vous ?"
    assert by_id["question_12"].options == ["CDI", "CDD"]
    assert by_id["question_12"].required
    assert not by_id["question_13"].required


def test_browser_read_the_newer_form(page):
    _, fields = opened(page, NEW)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "cv",
        "other_documents",
        "title",
        "last_name",
        "first_name",
        "email",
        "mobile_phone",
        "availability",
        "availability_date",
        "salary_1",
        "comment",
        "gdpr_agreement",
    ]
    assert by_id["cv"].label == "CV" and by_id["cv"].required
    assert not by_id["other_documents"].required
    assert not by_id["mobile_phone"].required  # its marker is hidden
    assert by_id["mobile_phone"].type is FieldType.PHONE
    assert by_id["availability_date"].type is FieldType.DATE
    assert by_id["availability_date"].shown_when == Is(
        "availability", ["Disponible à partir du..."]
    )


@pytest.mark.parametrize("url", [OLD, NEW])
def test_request_read_lists_the_same_fields(page, monkeypatch, url):
    _, fields = opened(page, url)
    fetched = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

    def urlopen(request, timeout):
        fetched.append(request.full_url)
        name = "questionnaire" if "questionnaire" in request.full_url else url[-1] == "a" and "old"
        return Response((PAGES / f"{name or 'new'}.html").read_bytes())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert JobAffinity().request_read(f"{url}?src=x") == fields
    assert fetched == [url, QUESTIONNAIRE] if url == OLD else [url]


def old_answers(resume: str) -> dict[str, Answer]:
    return {
        "cv": resume,
        "other": resume,
        "title": "Mme",
        "lastName": "Placeholder",
        "firstName": "Ada",
        "eMail": "ada@example.com",
        "mobilePhone": "+12025550143",
        "availability": "Disponible à partir du...",
        "availability_date": "2026-12-01",
        "salary_1": "50000",
        "comment": "Bonjour",
        "gdpr_agreement": "Yes",
        "question_11": "Technicien",
        "question_12": "CDD",
        "question_13": "Lyon",
    }


def test_every_answer_reads_back_on_the_older_form(page, resume):
    adapter, fields = opened(page, OLD)
    answers = old_answers(resume)
    adapter.fill(page, answers)
    assert page.locator("[name=availability_date]").input_value() == "01/12/2026"
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)
    assert len(page.context.pages) == 2  # the questionnaire, filled in its own tab
    assert adapter.submit(page) == QUESTIONNAIRE
    assert len(page.context.pages) == 1


def test_every_answer_reads_back_on_the_newer_form(page, resume):
    adapter, fields = opened(page, NEW)
    answers: dict[str, Answer] = {
        "cv": resume,
        "other_documents": resume,
        "title": "M.",
        "last_name": "Placeholder",
        "first_name": "Ada",
        "email": "ada@example.com",
        "mobile_phone": "+12025550143",
        "availability": "Disponible à partir du...",
        "availability_date": "2026-12-01",
        "salary_1": "50000",
        "comment": "Bonjour",
        "gdpr_agreement": "Yes",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)
    applied = "/application_form/submission/candidate_application?publication_token="
    assert adapter.submit(page) == f"https://jobaffinity.fr{applied}acme0000000000000b 200"


@pytest.mark.parametrize("url", [OLD, NEW])
def test_the_test_user_answers_every_field(page, tmp_path, url):
    adapter, fields = opened(page, url)
    answers = testuser.answers(fields, testuser.resume(tmp_path / "cv.pdf"))
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []


def test_a_posting_gone(page):
    with pytest.raises(Unsupported, match="posting_gone"):
        opened(page, "https://jobaffinity.fr/apply/gone00000000000000")


def test_the_newer_forms_questionnaire_step_is_a_known_gap(page):
    source = (PAGES / "new.html").read_text().replace('data-cap-wizard="0"', 'data-cap-wizard="1"')
    page.route(NEW, lambda route: route.fulfill(content_type="text/html", body=source))
    try:
        with pytest.raises(Unsupported, match="unsupported_flow: questionnaire_step"):
            opened(page, NEW)
    finally:
        page.unroute(NEW)


def test_posting_urls():
    adapter = JobAffinity()
    for url in (
        OLD,
        f"{OLD}?src=linkedin",
        f"{OLD}/",
        "https://www.jobaffinity.fr/apply/acme0000000000000a",
    ):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == OLD
    assert not adapter.detect("https://jobaffinity.fr/", None)
    assert not adapter.detect("https://www.example.com/apply/acme0000000000000a", None)
