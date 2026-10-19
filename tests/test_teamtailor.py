"""The Teamtailor adapter on tests/pages/teamtailor, served as a company's career site."""

from pathlib import Path

import pytest

from atsfill.adapters.teamtailor import Teamtailor
from atsfill.core import browser, matching

PAGE = Path(__file__).parent / "pages/teamtailor/new.html"
JOB = "https://careers.example.com/fr/jobs/1234567-solution-engineer"


def serve(route) -> None:
    if route.request.url.endswith("/applications/new"):
        route.fulfill(path=PAGE)
    else:
        route.fulfill(content_type="text/html", body="<a href='https://www.teamtailor.com/'></a>")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://careers.example.com/**", serve)
        yield page


@pytest.fixture
def opened(page) -> tuple[Teamtailor, list]:
    adapter = Teamtailor()
    page.goto(JOB + "?utm_source=LinkedIn")
    assert adapter.detect(page.url, page)
    return adapter, adapter.read_form(page)


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def test_detection_and_posting():
    adapter = Teamtailor()
    assert adapter.detect("https://acme.teamtailor.com/jobs/1-role", None)
    assert not adapter.detect(JOB, None)  # a custom domain shows itself on the page
    assert not adapter.detect("https://acme.teamtailor.com/", None)
    assert (
        adapter.posting(JOB + "/applications/new?x=1") == "https://careers.example.com/jobs/1234567"
    )


def test_read_form(opened):
    fields = {f.id: f for f in opened[1]}
    assert list(fields) == [
        "candidate[first_name]",
        "candidate[email]",
        "candidate[resume_remote_url]",
        "candidate[consent_given]",
        "question:101",
        "question:102",
        "question:103",
        "question:104",
    ]
    assert fields["candidate[first_name]"].label == "Prénom"
    assert fields["candidate[first_name]"].max_length == 50
    assert fields["candidate[consent_given]"].required
    assert fields["candidate[consent_given]"].label == "J'accepte la politique de confidentialité."
    assert fields["question:101"].options == ["Oui", "Non"]
    assert fields["question:102"].options == ["LinkedIn", "Site carrière"]
    assert not fields["question:102"].required
    assert fields["candidate[resume_remote_url]"].accept == [".pdf", ".docx"]


def test_every_answer_reads_back_past_the_cookie_banner(opened, page, resume):
    answers = {
        "candidate[first_name]": "Ada",
        "candidate[email]": "ada@example.com",
        "candidate[resume_remote_url]": resume,
        "candidate[consent_given]": "Yes",
        "question:101": "Oui",
        "question:102": "Site carrière",
        "question:103": ["Français", "Anglais"],
        "question:104": "2026-11-02",
    }
    adapter, fields = opened
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_submit_returns_the_thanks_page(opened, page):
    assert opened[0].submit(page) == JOB + "/applications/456/thanks"
