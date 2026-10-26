"""The isolved Hire adapter on tests/pages/isolvedhire, served as an Acme site: the account pages
and the application wizard, its steps served from /apply/view.php as the page fetches them."""

import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from atsfill.adapters.isolvedhire import IsolvedHire
from atsfill.core import browser, matching
from atsfill.core.adapter import Blocked, Refused, SignUpDetails
from atsfill.core.results import AnyOf, FieldType, Is
from tests import testuser

PAGES = Path(__file__).parent / "pages/isolvedhire"
HOST = "https://acme.isolvedhire.com"
JOB, GAPS, SIGNED_OUT = "1001", "1002", "1003"
PASSWORD = "Right-Password-1"
STEPS = dict(re.findall(r'<template id="([\w-]+)">\n?(.*?)</template>', (PAGES / "steps.html")
                        .read_text(), re.DOTALL))  # fmt: skip
SIGNED_IN = '<!doctype html><title>Acme</title><a href="/account/logout.php">Logout</a>'
DETAILS = SignUpDetails(
    "Jane Doe", "+12125550123", "1 Main St", "Washington", "District of Columbia", "20001", "US"
)


def redirect(to: str) -> str:
    """A redirect the browser follows as a navigation, so that the route serves it too."""
    return f"<!doctype html><script>location.replace({to!r})</script>"


class Site:
    """The server: each step as last saved, and what the account pages were sent."""

    def __init__(self) -> None:
        self.saved: dict[str, str] = {}
        self.sent: dict[str, dict[str, list[str]]] = {}
        self.extra: list[str] = []  # steps added to the wizard

    def serve(self, route) -> None:
        request, parts = route.request, urlsplit(route.request.url)
        query = parse_qs(parts.query)
        html = {"content_type": "text/html"}
        match parts.path:
            case "/account/login.php":
                route.fulfill(path=PAGES / "account.html")
            case "/account/login_redirect.php":
                form = parse_qs(request.post_data or "")
                if form.get("password") == [PASSWORD]:
                    route.fulfill(body=SIGNED_IN, **html)
                else:
                    to = "/account/login.php?error=invalid&attempts=4"
                    route.fulfill(body=redirect(to), **html)
            case "/account/" if request.method == "POST":
                self.sent["sign_up"] = parse_qs(request.post_data or "")
                route.fulfill(body=SIGNED_IN, **html)
            case "/account/":
                route.fulfill(path=PAGES / "sign-up.html")
            case "/account/save":
                self.sent["contact"] = {"body": [request.post_data or ""]}
                route.fulfill(body="ok")
            case "/apply/view.php":
                f = query["f"][0]
                if f == "buildAdditionalQuestions":
                    f = "children"
                elif f == "getSources":
                    f = f"sources-{query['source_type_id'][0]}"
                route.fulfill(body=self.saved.get(f, STEPS.get(f, "")), **html)
            case "/apply/save":
                self.saved[query["f"][0]] = request.post_data or ""
                route.fulfill(body="ok")
            case "/apply/upload_resume.php" | "/apply/thanks.php":
                route.fulfill(body="<!doctype html><title>Thanks</title>", **html)
            case path if path.startswith(f"/apply/{SIGNED_OUT}-"):
                to = f"/account/login.php?listing_id={SIGNED_OUT}&i=0"
                route.fulfill(body=redirect(to), **html)
            case path if path.startswith("/apply/"):
                shell = (PAGES / "apply.html").read_text()
                links = "".join(
                    f'<a class="list-group-item step" data-file="{s}"><span>{s}</span></a>'
                    for s in self.extra
                )
                submit = '<a class="list-group-item step incomplete" data-file="submit">'
                route.fulfill(body=shell.replace(submit, links + submit), **html)
            case _:
                route.fulfill(body="<!doctype html><title>Job</title><p>A job</p>", **html)


@pytest.fixture(scope="module")
def browser_page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        yield page


@pytest.fixture
def site(browser_page):
    site = Site()
    browser_page.context.unroute(f"{HOST}/**")
    browser_page.context.route(f"{HOST}/**", site.serve)
    return site


@pytest.fixture
def page(browser_page, site):
    return browser_page


def opened(page, job: str = JOB) -> tuple[IsolvedHire, list]:
    adapter = IsolvedHire()
    url = f"{HOST}/jobs/{job}"
    assert adapter.detect(url, None)
    adapter.account_scope(url)
    page.goto(url)
    return adapter, adapter.browser_read(page)


def test_a_posting_url_and_its_wizard_are_one_posting():
    adapter = IsolvedHire()
    assert adapter.posting(f"{HOST}/jobs/{JOB}?utm_source=x") == f"{HOST}/jobs/{JOB}"
    assert adapter.posting(f"{HOST}/apply/{JOB}-0.php") == f"{HOST}/jobs/{JOB}"
    assert not adapter.detect("https://admin.isolvedhire.com/jobs/1", None)
    assert adapter.account_scope(f"{HOST}/jobs/{JOB}") is None
    assert adapter.login_url(None) == f"{HOST}/account/login.php"


def test_sign_up_declines_texts_and_agrees_to_the_privacy_policy(page, site):
    page.goto(f"{HOST}/account/login.php")
    IsolvedHire().sign_up(page, "jane@example.com", PASSWORD, DETAILS)
    sent = {k: v[0] for k, v in site.sent["sign_up"].items()}
    assert sent | {"csrf_token": ""} == {
        "csrf_token": "", "first_name": "Jane", "last_name": "Doe",
        "email": "jane@example.com", "email_confirm": "jane@example.com",
        "contact_number": "2125550123", "contact_number_type": "2",
        "street1": "1 Main St", "country_id": "226", "state_id": "9", "city": "Washington",
        "zip": "20001", "password": PASSWORD, "password_confirm": PASSWORD,
        "applicant_consent_approval": "2", "text_approval": "0", "info_use_consent": "on",
    }  # fmt: skip


def test_sign_up_with_a_state_the_country_lacks_is_refused(page, site):
    page.goto(f"{HOST}/account/login.php")
    details = SignUpDetails(**{**DETAILS.__dict__, "state": "DC"})
    with pytest.raises(Refused, match="^sign_up_details: no state DC"):
        IsolvedHire().sign_up(page, "jane@example.com", PASSWORD, details)
    assert "sign_up" not in site.sent


def test_sign_in_with_a_wrong_password_is_refused(page):
    adapter = IsolvedHire()
    page.goto(f"{HOST}/account/login.php")
    with pytest.raises(Refused, match="^wrong_password"):
        adapter.sign_in(page, "jane@example.com", "Wrong-1")
    page.goto(f"{HOST}/account/login.php")
    adapter.sign_in(page, "jane@example.com", PASSWORD)


def test_a_signed_out_wizard_is_a_login_wall(page):
    with pytest.raises(Blocked, match="^login_wall"):
        opened(page, SIGNED_OUT)


def test_the_read_lists_every_step(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "screening.101", "screening.107", "screening.102", "screening.103", "screening.104",
        "screening.105", "screening.106", "resume.resume", "eeoc.sex_type", "eeoc.is_vet_2014",
        "eeoc.disabled_employee_id", "eeoc.is_disabled", "source.source_type_id",
        "source.source_id", "source.source_other",
    ]  # fmt: skip
    assert by_id["screening.101"].options == ["Yes", "No"]
    assert by_id["screening.107"].shown_when == Is("screening.101", ["No"])
    assert by_id["screening.103"].max_length == 5000
    assert not by_id["screening.104"].required
    assert by_id["screening.105"].type is FieldType.MULTI_CHOICE
    assert by_id["screening.105"].options == ["North", "South"]
    assert by_id["screening.106"].options == ["Day", "Night"]
    assert by_id["resume.resume"].type is FieldType.FILE
    assert by_id["eeoc.sex_type"].label == "Sex/Gender"
    assert by_id["eeoc.is_disabled"].options[0].startswith("Yes")
    sources = by_id["source.source_id"].option_sets or []
    assert [(s.when, s.options) for s in sources] == [
        (Is("source.source_type_id", ["Company Website"]), ["Acme Website", "Other"]),
        (Is("source.source_type_id", ["Referral"]), ["Employee Referral", "Other"]),
    ]
    assert by_id["source.source_other"].shown_when == AnyOf(
        [Is("source.source_id", ["Other"]), Is("source.source_type_id", ["Referral"])]
    )


def test_the_test_user_answers_every_field(page, site, tmp_path):
    adapter, fields = opened(page)
    answers = testuser.answers(fields, testuser.resume(tmp_path / "cv.pdf"))
    adapter.fill(page, answers)
    assert page.locator("#steps a.step.current").get_attribute("data-file") == "submit"
    contact = site.sent["contact"]["body"][0]  # multipart: each field's name, then its value
    assert re.search(r'name="applicant_consent_approval"\s+2\s', contact)
    assert re.search(r'name="info_use_consent"\s+on\s', contact)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.answer, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_every_answer_reads_back(page, tmp_path):
    adapter, fields = opened(page)
    answers = {
        "screening.101": "No", "screening.107": "Yes", "screening.102": "Bachelor's Degree",
        "screening.103": "Grade 3", "screening.104": "A short note.",
        "screening.105": ["North", "South"], "screening.106": "Night",
        "resume.resume": str(testuser.resume(tmp_path / "cv.pdf")),
        "eeoc.sex_type": "I choose not to provide my gender.",
        "eeoc.is_vet_2014": "I DON'T WISH TO ANSWER",
        "eeoc.is_disabled": "I do not want to answer",
        "source.source_type_id": "Referral", "source.source_id": "Employee Referral",
        "source.source_other": "Jane Doe",
    }  # fmt: skip
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.answer, c.read_back) for c in checks if not c.match] == []


def test_a_missing_answer_stops_its_step(page):
    adapter, _ = opened(page)
    with pytest.raises(Exception, match="the screening step was not saved"):
        adapter.fill(page, {"screening.101": "Yes"})


def test_an_entry_list_and_a_drawn_signature_are_known_gaps(page, site):
    site.extra = ["employment", "disclosure"]
    _, fields = opened(page, GAPS)
    by_id = {f.id: f for f in fields}
    assert (by_id["employment.entries"].unfillable or "").startswith(
        "unsupported_rendering: entry_list"
    )
    assert (by_id["disclosure.signature"].unfillable or "").startswith(
        "unsupported_rendering: drawn_signature"
    )
    assert by_id["disclosure.disclosure_agreed_at"].options == ["Yes", "No"]


def test_submit_sends_from_the_last_step(page, tmp_path):
    adapter, fields = opened(page)
    adapter.fill(page, testuser.answers(fields, testuser.resume(tmp_path / "cv.pdf")))
    assert adapter.submit(page) == f"{HOST}/apply/thanks.php?orig_job_app=1001-5001"
