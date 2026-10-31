"""The WelcomeKit adapter on tests/pages/welcomekit/: `old.html`, served as welcomekit.co, and
`new.html` with its job's `new.json`, served as ats.welcometothejungle.com and its API."""

import io
import json
from pathlib import Path

import pytest

from atsfill.adapters.welcomekit import WelcomeKit
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import FieldType
from tests import testuser

PAGES = Path(__file__).parent / "pages/welcomekit"
OLD = "https://www.welcomekit.co/candidates/new?o=AbCdEf&j=ACME_abc123"
OLD_REFUSED = "https://www.welcomekit.co/candidates/new?o=AbCdEf&j=ACME_refused"
OLD_GONE = "https://www.welcomekit.co/candidates/new?o=AbCdEf&j=ACME_gone"
JOB = "11111111-1111-1111-1111-111111111111"
NEW = f"https://ats.welcometothejungle.com/board/acme-AbCd/job/{JOB}/apply"
NEW_CLOSED = "https://ats.welcometothejungle.com/board/acme-AbCd/job/22222222-2222-2222-2222-222222222222/apply"
API = "https://api.ats.welcometothejungle.com/api/v1"
CORS = {"access-control-allow-origin": "*", "access-control-allow-headers": "*"}


def serve(route) -> None:
    request = route.request
    url = request.url
    if request.method == "OPTIONS":
        route.fulfill(headers=CORS)
    elif url.startswith(f"{API}/assets/"):
        route.fulfill(headers=CORS, json={"url": "https://storage.invalid/"})
    elif url == f"{API}/board/organizations/acme-AbCd/jobs/{JOB}":
        route.fulfill(headers=CORS, path=PAGES / "new.json")
    elif url == f"{API}/board/organizations/acme-AbCd/jobs/{JOB}/apply":
        route.fulfill(headers=CORS, status=201, json={"id": "application"})
    elif url.startswith(f"{API}/"):
        route.fulfill(headers=CORS, status=404, json={"errors": {"detail": "Not Found"}})
    elif url.startswith("https://ats.welcometothejungle.com/board/"):
        route.fulfill(path=PAGES / "new.html")
    elif url.startswith("https://www.welcomekit.co/candidates?") and "ACME_refused" in url:
        page = (PAGES / "old.html").read_text()
        error = '<p class="help-block">Veuillez accepter le traitement de vos données</p></form>'
        route.fulfill(content_type="text/html", body=page.replace("</form>", error))
    elif url.startswith("https://www.welcomekit.co/candidates?"):
        route.fulfill(content_type="text/html", body="<h1>Merci pour votre candidature !</h1>")
    elif url in (OLD, OLD_REFUSED):
        job = url.rsplit("j=", 1)[1]
        page = (PAGES / "old.html").read_text()
        route.fulfill(content_type="text/html", body=page.replace("ACME_abc123", job))
    else:
        route.fulfill(content_type="text/html", body="ERROR, missing organization or job")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.welcomekit.FORM_TIMEOUT_MS", 3_000)
        mp.setattr("atsfill.adapters.welcomekit.SUBMIT_TIMEOUT_MS", 5_000)
        for host in ("www.welcomekit.co", "ats.welcometothejungle.com", API.split("/")[2]):
            page.context.route(f"https://{host}/**", serve)
        yield page


def opened(page, url: str) -> tuple[WelcomeKit, list]:
    adapter = WelcomeKit()
    page.goto(url)
    return adapter, adapter.browser_read(page)


def dry_run(page, tmp_path, url: str) -> tuple[WelcomeKit, list]:
    adapter, fields = opened(page, url)
    cv = testuser.resume(tmp_path / "CV Ada.pdf")
    picture = testuser.photo(tmp_path / "photo.png")
    answers = {
        f.id: a
        for f in fields
        if not f.unfillable and (a := testuser.answer(f, cv, picture)) is not None
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [c for c in checks if not c.match] == []
    return adapter, fields


def test_detect_and_posting():
    adapter = WelcomeKit()
    assert adapter.detect(f"{OLD}&utm_source=freehire.me", None)
    assert adapter.posting("https://welcomekit.co/candidates/new?j=ACME_abc123&o=AbCdEf") == OLD
    assert not adapter.detect("https://www.welcomekit.co/candidates/new?j=ACME_abc123", None)
    job = NEW.removesuffix("/apply")
    assert adapter.detect(f"{job}?referrer=wttj", None)
    assert adapter.posting(f"{NEW}?referrer=wttj") == NEW
    assert adapter.posting(job) == NEW
    assert not adapter.detect("https://ats.welcometothejungle.com/board/acme-AbCd", None)
    assert not adapter.detect("https://www.welcometothejungle.com/fr/companies/acme/jobs/x", None)


def test_old_form_lists_its_fields_in_order(page):
    _, fields = opened(page, OLD)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "firstname",
        "email",
        "phone",
        "zip_code",
        "country_code",
        "image",
        "resume",
        "media_linkedin",
        "answers.0",
        "consent",
    ]  # the hidden referrer and question inputs are not
    assert by_id["firstname"].label == "Prénom" and by_id["firstname"].required
    assert by_id["phone"].type is FieldType.PHONE
    assert by_id["zip_code"].label == "Adresse: Code postal"
    country = by_id["country_code"]
    assert country.label == "Adresse: Country" and not country.required
    assert country.options == ["France", "United Kingdom", "United States"]
    assert country.default == "France"
    assert by_id["resume"].accept == ["application/pdf"] and by_id["resume"].required
    assert by_id["image"].accept == ["image/png", "image/jpeg", "image/gif"]
    assert by_id["answers.0"].label == "Quand serais-tu disponible ?"
    assert by_id["consent"].options == ["Yes", "No"] and not by_id["consent"].required


def test_new_form_lists_its_fields_with_its_questions_by_order(page):
    _, fields = opened(page, NEW.removesuffix("/apply"))  # the job's tab goes to the form
    assert page.url == NEW
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "firstname",
        "lastname",
        "email",
        "linkedin_url",
        "phone_number",
        "personal_website_url",
        "cv",
        "custom_questions.0",
        "custom_questions.1",
        "custom_questions.2",
        "custom_questions.3",
    ]
    assert by_id["linkedin_url"].type is FieldType.URL
    assert by_id["cv"].required and by_id["cv"].accept == [".pdf"]
    site = by_id["custom_questions.0"]
    assert site.label.startswith("Are you available") and site.options == ["Yes", "No"]
    assert by_id["custom_questions.1"].label == "Why Acme?"
    assert by_id["custom_questions.2"].type is FieldType.FILE
    assert not by_id["custom_questions.3"].required


@pytest.mark.parametrize("url", [OLD, NEW])
def test_request_read_lists_what_browser_read_does(page, monkeypatch, url):
    _, fields = opened(page, url)
    body = (PAGES / ("old.html" if url == OLD else "new.json")).read_bytes()
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: io.BytesIO(body))
    assert WelcomeKit().request_read(url) == fields


def test_a_job_not_published_is_gone(page, monkeypatch):
    with pytest.raises(Unsupported, match="^posting_gone"):
        opened(page, NEW_CLOSED)
    closed = json.loads((PAGES / "new.json").read_text())
    closed["job"]["status"] = "archived"
    reply = json.dumps(closed).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: io.BytesIO(reply))
    assert WelcomeKit().request_read(NEW) is None


def test_an_unknown_old_job_is_gone(page):
    with pytest.raises(Unsupported, match="^posting_gone"):
        opened(page, OLD_GONE)


def test_old_dry_run_reads_back_every_answer_then_submits(page, tmp_path):
    adapter, _ = dry_run(page, tmp_path, OLD)
    assert adapter.read_back(page)["resume"] == "CV Ada.pdf"
    assert adapter.submit(page).startswith("https://www.welcomekit.co/candidates?")


def test_old_submit_fails_when_the_form_comes_back(page, tmp_path):
    adapter, _ = dry_run(page, tmp_path, OLD_REFUSED)
    with pytest.raises(RuntimeError, match="Veuillez accepter"):
        adapter.submit(page)


def test_new_dry_run_reads_back_every_answer_then_submits(page, tmp_path):
    adapter, _ = dry_run(page, tmp_path, NEW)
    read_back = adapter.read_back(page)
    assert read_back["cv"] == "CV Ada.pdf"
    assert read_back["custom_questions.0"] == "Yes"
    assert adapter.submit(page) == f"{API}/board/organizations/acme-AbCd/jobs/{JOB}/apply 201"


def test_old_shown_fields_name_a_field_the_read_did_not_list(page):
    adapter, fields = opened(page, OLD)
    assert set(adapter.shown_fields(page)) == {f.id for f in fields}
    page.locator("form#new_candidate").evaluate(
        """f => f.insertAdjacentHTML("beforeend", '<input name="candidate[follow_up]">')"""
    )
    assert "follow_up" in adapter.shown_fields(page)


def test_new_shows_nothing_its_api_does_not_list(page):
    adapter, _ = opened(page, NEW)
    assert adapter.shown_fields(page) == []
