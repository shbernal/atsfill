from datetime import date

import pytest

from atsfill.core import browser
from atsfill.core.results import Field, FieldType, Is
from scripts import survey
from tests import testuser


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
        "block",
        "DataDome",
        "captcha-delivery.com",
    )


def test_a_vendor_script_on_a_page_that_loads_is_no_block(page, server):
    assert probe(page, f"{server}/survey/protected.html").label is None


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


@pytest.mark.parametrize(
    "reason, label",
    [
        ("out_of_scope: q1 is a video recorded live", "out_of_scope"),
        ("unsupported_rendering: education_history: schools is a list", "known_gap"),
        ("unsupported_flow: steps: more than 8 steps", "known_gap"),
        ("unhandled_rendering: q2 is a slider", "unhandled"),
        ("adapter_error: KeyError: 'x'", "unhandled"),
        ("external_step: the application is on example.com", None),
    ],
)
def test_an_unsupported_reason_is_labelled_by_its_case(reason, label):
    assert survey.case(reason) == label


def test_the_test_user_answers_from_the_fields(tmp_path):
    cv = testuser.resume(tmp_path / "resume.pdf")
    picture = testuser.photo(tmp_path / "photo.png")
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
        Field("c", "Resume", FieldType.FILE, True, prefilled="resume.pdf"),
        Field("a", "Photo", FieldType.FILE, True, accept=[".jpg", ".png"]),
        Field("b", "Scan", FieldType.FILE, False, accept=[".tiff"]),
        Field("m", "CV", FieldType.FILE, False, accept=["application/pdf"]),
        Field("i", "Avatar", FieldType.FILE, False, accept=["image/png"]),
        Field("x", "Pick one", FieldType.CHOICE, False, []),
        Field("h", "Source", FieldType.CHOICE, True, ["Contacted by a recruiter", "Other", "Web"]),
        Field("t", "City", FieldType.TYPEAHEAD, True),
        Field("w", "Town", FieldType.TYPEAHEAD, False),  # the form's country's towns only
        Field("z", "Postal code / zip code", FieldType.TYPEAHEAD, True),
        Field("p", "Phone", FieldType.PHONE, True, prefilled="+33 600000000"),  # the account's
        Field("o", "Video", FieldType.FILE, False, unfillable="out_of_scope: o is a video"),
        Field("k", "Country", FieldType.CHOICE, True, ["France", "Spain"], default="Spain"),
    ]
    answers = testuser.answers(fields, cv, picture)
    assert answers == {
        "e": testuser.EMAIL,
        "n": "Ada P",
        "g": "Decline to self-identify",
        "v": "Yes",
        "l": ["French"],
        "s": testuser.SALARY,
        "q": testuser.TEXT,
        "d": answers["d"],
        "h": "Web",
        "t": testuser.CITY,
        "z": testuser.ZIP,
        "c": str(cv),
        "a": str(picture),
        "m": str(cv),
        "i": str(picture),
        "k": "Spain",  # the page's, which the lists after it follow
    }
    assert date.fromisoformat(str(answers["d"])) > date.today()
    assert cv.read_bytes().startswith(b"%PDF-1.4")
    assert picture.read_bytes().startswith(b"\x89PNG")


def test_a_follow_up_is_answered_when_its_trigger_option_holds_or(tmp_path):
    current = "Currently working as a student, contingent, or contractor"
    asked = "(asked when 'Have you worked at Acme?' is"
    fields = [
        Field("w", "Have you worked at Acme?", FieldType.CHOICE, True, [current, "Never"]),
        Field("i", f"Your ID {asked} {current})", FieldType.TEXT, False),
        Field("r", f"Why? {asked} Never or Unsure)", FieldType.TEXT, False),
    ]
    answers = testuser.answers(fields, testuser.resume(tmp_path / "resume.pdf"))
    assert answers == {"w": current, "i": testuser.TEXT}


def test_a_choice_takes_the_option_that_shows_the_most_follow_ups(tmp_path):
    fields = [
        Field("w", "Have you worked here?", FieldType.CHOICE, True, ["Never", "Yes"]),
        Field("i", "Your ID", FieldType.TEXT, True, shown_when=Is("w", ["Yes"])),
    ]
    answers = testuser.answers(fields, testuser.resume(tmp_path / "resume.pdf"))
    assert answers == {"w": "Yes", "i": testuser.TEXT}


def test_a_choice_never_shows_a_required_follow_up_without_an_answer(tmp_path):
    fields = [
        Field("w", "Have you worked here?", FieldType.CHOICE, True, ["Never", "Yes"]),
        Field("v", "Video", FieldType.FILE, True, accept=[".mp4"], shown_when=Is("w", ["Yes"])),
    ]
    answers = testuser.answers(fields, testuser.resume(tmp_path / "resume.pdf"))
    assert answers == {"w": "Never"}
