from datetime import date

import pytest

from atsfill.core import browser
from atsfill.core.results import Field, FieldType
from scripts import survey
from tests import persona


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        yield page


def probe(page, url: str, requested: str | None = None) -> survey.Probe:
    response = page.goto(url)
    page.wait_for_load_state("networkidle")
    return survey.probe(page, requested or url, response)


def test_a_block_page_names_its_vendor(page, server):
    found = probe(page, f"{server}/survey/blocked.html")
    assert (found.label, found.vendor, found.signal) == (
        "blocked",
        "DataDome",
        "captcha-delivery.com",
    )


def test_a_password_field_is_a_login_wall(page, server):
    assert probe(page, f"{server}/survey/login.html").label == "login_wall"


def test_a_form_in_another_sites_frame_is_an_embed(page, server):
    found = probe(page, f"{server}/survey/embed.html")
    assert found.label == "embedded"
    assert found.signal and found.signal.startswith("http://127.0.0.1:")


def test_landing_on_another_host_is_a_redirect(page, server):
    found = probe(page, f"{server}/form.html", requested="https://jobs.example.com/1")
    assert (found.label, found.signal) == ("redirect", "localhost")


def test_a_plain_form_has_no_label(page, server):
    assert probe(page, f"{server}/form.html").label is None


def test_the_persona_answers_from_the_fields(tmp_path):
    cv = persona.resume(tmp_path / "resume.pdf")
    fields = [
        Field("e", "Email address", FieldType.TEXT, True),
        Field("n", "Full name", FieldType.TEXT, True, max_length=5),
        Field(
            "g", "Gender", FieldType.CHOICE, False, ["Male", "Female", "Decline to self-identify"]
        ),
        Field("v", "Are you a protected veteran?", FieldType.CHOICE, False, ["Yes", "No"]),
        Field("l", "Languages", FieldType.MULTI_CHOICE, False, ["French", "English"]),
        Field("s", "Expected salary", FieldType.TEXT, False),
        Field("q", "Why us?", FieldType.TEXT, False),
        Field("d", "Start date", FieldType.DATE, False),
        Field("c", "Resume", FieldType.FILE, True),
        Field("x", "Pick one", FieldType.CHOICE, False, []),
    ]
    answers = persona.answers(fields, cv)
    assert answers == {
        "e": persona.EMAIL,
        "n": "Ada P",
        "g": "Decline to self-identify",
        "v": "Yes",
        "l": ["French"],
        "s": persona.SALARY,
        "q": persona.TEXT,
        "d": answers["d"],
        "c": str(cv),
    }
    assert date.fromisoformat(str(answers["d"])) > date.today()
    assert cv.read_bytes().startswith(b"%PDF-1.4")
