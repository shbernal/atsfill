"""The WeRecruit adapter on tests/pages/werecruit/posting.html, served as careers.werecruit.io
and as an employer's own domain."""

import io
from pathlib import Path

import pytest

from atsfill.adapters.werecruit import WeRecruit
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import Answer, FieldType
from tests import testuser

PAGE = Path(__file__).parent / "pages/werecruit/posting.html"
URL = "https://careers.werecruit.io/fr/acme/offres/ingenieur-devops-a1b2c3"
OWN = "https://jobs.acme.example/fr/offres/ingenieur-devops-a1b2c3"
QUESTION = "question:00000000-0000-0000-0000-00000000000a"
RADIO = "question:00000000-0000-0000-0000-00000000000b"
THANKS = "<p>Merci, votre candidature a bien été envoyée</p>"


def serve(route) -> None:
    url, method = route.request.url, route.request.method
    if method == "POST" and url == URL:
        route.fulfill(content_type="text/html", body=THANKS)
    elif url.startswith((URL, OWN)):
        route.fulfill(path=PAGE)
    else:
        route.fulfill(status=404, content_type="text/html", body="<h1>Page introuvable</h1>")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.werecruit.FORM_TIMEOUT_MS", 3_000)
        mp.setattr("atsfill.adapters.werecruit.SUBMIT_TIMEOUT_MS", 3_000)
        page.context.route("https://careers.werecruit.io/**", serve)
        page.context.route("https://jobs.acme.example/**", serve)
        yield page


def opened(page, url: str = URL) -> tuple[WeRecruit, list]:
    adapter = WeRecruit()
    page.goto(url)
    assert adapter.detect(url, page)
    return adapter, adapter.browser_read(page)


def test_browser_read(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "civility",
        "lastName",
        "firstName",
        "email",
        "phone",
        "availability",
        "salaryExpectationMin",
        "photo",
        "Resume",
        "Lettre de motivation",
        QUESTION,
        RADIO,
    ]  # the consent box, commented out on every page, is not listed
    assert not any(f.unfillable for f in fields)
    assert by_id["civility"].label == "Civilité" and by_id["civility"].required
    assert by_id["civility"].options == ["Madame", "Monsieur", "Mx"]
    assert by_id["phone"].type is FieldType.PHONE
    assert by_id["availability"].required
    assert by_id["availability"].options[0] == "immédiate"
    assert by_id["salaryExpectationMin"].label == "Attentes salariales (Brut/an)"
    assert not by_id["salaryExpectationMin"].required
    assert by_id["photo"].type is FieldType.FILE and not by_id["photo"].required
    assert by_id["photo"].accept == [".jpg"]
    assert by_id["Resume"].label == "CV" and by_id["Resume"].required
    assert not by_id["Lettre de motivation"].required
    assert by_id[QUESTION].label == "Comment avez vous connu Acme ?"
    assert by_id[QUESTION].required and by_id[QUESTION].type is FieldType.TEXT
    assert by_id[RADIO].options == ["Oui", "Non"] and not by_id[RADIO].required


def test_request_read_lists_the_same_fields(page, monkeypatch):
    _, fields = opened(page)

    class Response(io.BytesIO):
        def __enter__(self):
            return self

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout: Response(PAGE.read_bytes())
    )
    assert WeRecruit().request_read(f"{URL}?utm_source=freehire.me") == fields


def answers(resume: str) -> dict[str, Answer]:
    return {
        "civility": "Mx",
        "lastName": "Placeholder",
        "firstName": "Ada",
        "email": "ada@example.com",
        "phone": "+12025550143",
        "availability": "1 mois",
        "salaryExpectationMin": "45 000€ - 50 000€",
        "Resume": resume,
        "Lettre de motivation": resume,
        QUESTION: "LinkedIn",
        RADIO: "Non",
    }


def test_every_answer_reads_back_and_submits(page, tmp_path):
    adapter, fields = opened(page)
    given = answers(str(testuser.resume(tmp_path / "CV Ada.pdf")))
    adapter.fill(page, given)
    checks = matching.check(fields, given, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(given)
    assert adapter.submit(page) == f"{URL} 200"


def test_the_test_user_answers_every_field(page, tmp_path):
    adapter, fields = opened(page)
    given = testuser.answers(fields, testuser.resume(tmp_path / "cv.pdf"))
    adapter.fill(page, given)
    checks = matching.check(fields, given, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []


def test_a_form_the_page_does_not_post_is_a_failure(page):
    adapter, _ = opened(page)
    held = "form => form.addEventListener('submit', e => e.preventDefault())"
    page.locator("#form").evaluate(held)
    with pytest.raises(RuntimeError, match="not posted"):
        adapter.submit(page)


def test_an_employers_own_domain(page):
    adapter, fields = opened(page, OWN)
    assert len(fields) == 12
    assert not adapter.detect(OWN, None)  # only its page tells
    assert adapter.posting(f"{OWN}?utm_source=x") == "https://jobs.acme.example/fr/offres/a1b2c3"


def test_a_posting_gone(page):
    page.goto("https://careers.werecruit.io/fr/acme/offres/ingenieur-devops-000000")
    with pytest.raises(Unsupported, match="posting_gone"):
        WeRecruit().browser_read(page)


def test_posting_urls():
    adapter = WeRecruit()
    key = "https://careers.werecruit.io/fr/acme/offres/a1b2c3"
    for url in (
        URL,
        f"{URL}?utm_source=freehire.me",
        "https://careers.werecruit.io/en/acme/offers/ingenieur-devops-a1b2c3",
        "https://careers.werecruit.io/fr/acme/offres/a1b2c3",
    ):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == key
    assert not adapter.detect("https://careers.werecruit.io/fr/acme", None)
    assert not adapter.detect("https://careers.werecruit.io/fr/acme/offres/", None)


def test_shown_fields_name_a_field_the_read_did_not_list(page):
    adapter, fields = opened(page)
    assert set(adapter.shown_fields(page)) == {f.id for f in fields}
    page.locator("form#form").evaluate(
        """f => f.insertAdjacentHTML("beforeend", '<input name="follow_up">')"""
    )
    assert "follow_up" in adapter.shown_fields(page)
