"""The Rippling adapter on tests/pages/rippling, served as a job's apply page."""

import json
from pathlib import Path

import pytest

from atsfill.adapters.rippling import DATA, Rippling, _api, _form
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import Answer, FieldType
from tests import persona

PAGES = Path(__file__).parent / "pages/rippling"
JOB = "https://ats.rippling.com/acme/jobs/0b6a3f8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
GONE = "https://ats.rippling.com/acme/jobs/9f8e7d6c-5b4a-4321-8fed-cba987654321"
PAGE = (PAGES / "apply.html").read_text()
DATA_JSON = json.loads(match[1]) if (match := DATA.search(PAGE)) else {}
Q = "customQuestions.g1."
CLOSED = (
    '<html><script id="__NEXT_DATA__" type="application/json">'
    '{"props": {"pageProps": {"apiData": {}}}}</script><h1>Job not found</h1></html>'
)


def serve(route) -> None:
    url, method = route.request.url, route.request.method
    if method == "POST" or "/api/" in url:
        route.fulfill(status=201 if url.endswith("/apply") else 200, json={})
    elif url.startswith(GONE):
        route.fulfill(content_type="text/html", body=CLOSED)
    elif url.endswith("/apply"):
        route.fulfill(content_type="text/html", body=PAGE)
    else:
        route.fulfill(content_type="text/html", body=f'<a href="{JOB}/apply">Apply</a>')


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.rippling.FORM_TIMEOUT_MS", 3_000)
        mp.setattr("atsfill.adapters.rippling.OPTIONS_TIMEOUT_MS", 2_000)
        page.context.route("https://ats.rippling.com/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    return str(persona.resume(tmp_path / "CV Ada.pdf"))


def opened(page, url: str = JOB) -> tuple[Rippling, list]:
    adapter = Rippling()
    page.goto(url)
    assert adapter.detect(url, None)
    return adapter, adapter.read_form(page)


def test_read_form(page):
    _, fields = opened(page)
    assert page.url == f"{JOB}/apply"
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "first_name",
        "last_name",
        "email",
        "pronouns",
        "phone_number",
        "location",
        "linkedin_link",
        "resume",
        "cover_letter",
        f"{Q}salary",
        f"{Q}why",
        f"{Q}start",
        f"{Q}degree",
        f"{Q}degree_comment",
        f"{Q}source",
        f"{Q}source_other",
        f"{Q}authorized",
        f"{Q}shifts",
        f"{Q}travel",
        f"{Q}licence",
        "eeoc.gender",
        "eeoc.race",
        "eeoc.hispanicOrLatino",
        "eeoc.veteranStatus",
        "sms_opt_in",
    ]
    assert not [f.id for f in fields if f.unfillable]
    assert by_id["phone_number"].type is FieldType.PHONE
    assert by_id["location"].type is FieldType.TYPEAHEAD
    assert "Enter my pronouns" not in (by_id["pronouns"].options or [])
    assert by_id["resume"].accept == [".doc", ".docx", ".pdf"]
    assert by_id[f"{Q}salary"].max_length == 15
    assert by_id[f"{Q}start"].type is FieldType.DATE
    assert by_id[f"{Q}source"].options == ["Job board", "Referral", "Other"]
    assert by_id[f"{Q}source_other"].label == (
        "How did you hear about us?: Other (asked when 'How did you hear about us?' is Other)"
    )
    assert not by_id[f"{Q}degree_comment"].required
    assert by_id[f"{Q}authorized"].type is FieldType.CHOICE
    assert by_id[f"{Q}shifts"].type is FieldType.MULTI_CHOICE
    assert by_id[f"{Q}travel"].options == ["Strong No", "No", "Yes", "Strong Yes"]
    assert not by_id["eeoc.race"].required


def test_read_api_lists_the_same_fields(page):
    _, fields = opened(page)
    shape = lambda fs: [(f.id, f.label, f.type, f.required, f.options) for f in fs]  # noqa: E731
    assert shape(_form(_api(DATA_JSON))) == shape(fields)


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {
        "first_name": "Ada",
        "last_name": "Placeholder",
        "email": "ada@example.com",
        "pronouns": "They/them/theirs",
        "phone_number": "+447700900123",
        "location": "London",
        "linkedin_link": "https://www.linkedin.com/in/ada-placeholder",
        "resume": resume,
        "cover_letter": resume,
        f"{Q}salary": "50000",
        f"{Q}why": "Placeholder answer.",
        f"{Q}start": "2026-11-20",
        f"{Q}degree": "Bachelor's degree",
        f"{Q}degree_comment": "A placeholder comment.",
        f"{Q}source": "Other",
        f"{Q}source_other": "A placeholder source",
        f"{Q}authorized": "Yes",
        f"{Q}shifts": ["Weekdays", "Weekends"],
        f"{Q}travel": "Strong Yes",
        f"{Q}licence": resume,
        "eeoc.gender": "Choose not to disclose",
        "eeoc.race": "Choose not to disclose",
        "eeoc.hispanicOrLatino": "Choose not to disclose",
        "eeoc.veteranStatus": "I am not a protected veteran",
        "sms_opt_in": "No - I do not consent to receiving text messages",
    }
    adapter.fill(page, answers)
    read_back = adapter.read_back(page)
    checks = matching.check(fields, answers, read_back)
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)
    assert read_back["phone_number"] == "+44 7700900123"
    assert read_back["first_name"] == "Ada"  # typed over what the résumé's parse filled


def test_the_persona_dry_run_reads_back(page, tmp_path):
    adapter, fields = opened(page)
    answers = persona.answers(fields, persona.resume(tmp_path / "cv.pdf"))
    assert f"{Q}source_other" not in answers  # the persona picks the first option
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []


def test_a_posting_gone(page):
    page.goto(GONE)
    with pytest.raises(Unsupported, match="posting_gone"):
        Rippling().read_form(page)


def test_unknown_question_types_are_unhandled():
    data = json.loads(json.dumps(DATA_JSON))
    post = data["props"]["pageProps"]["apiData"]["jobPost"]
    post["activeJobApplication"]["additionalQuestions"][0]["form"]["questions"][0][
        "questionType"
    ] = "RATING"
    post["activeJobApplication"]["basicQuestions"][0]["fieldType"] = "ADDRESS"
    by_id = {f.id: f for f in _form(_api(data))}
    assert by_id[f"{Q}salary"].unfillable == f"unhandled_rendering: {Q}salary is a RATING"
    assert by_id["first_name"].unfillable == "unhandled_rendering: first_name is a ADDRESS"


def test_a_live_video_question_is_out_of_scope():
    data = json.loads(json.dumps(DATA_JSON))
    data["props"]["pageProps"]["apiData"]["videoScreeningQuestions"] = [
        {"uniqueKey": "intro", "title": "Introduce yourself"}
    ]
    video = {f.id: f for f in _form(_api(data))}["video.intro"]
    assert video.required
    assert video.unfillable == "out_of_scope: video.intro is a video recorded live"


def test_a_field_missing_from_the_page_is_unhandled(page):
    page.goto(f"{JOB}/apply")
    page.evaluate("() => document.querySelector(\"[data-testid='eeoc.gender']\").remove()")
    fields = {f.id: f for f in Rippling().read_form(page)}
    assert fields["eeoc.gender"].unfillable == (
        "unhandled_rendering: eeoc.gender is missing on the page"
    )


def test_submit_returns_the_api_answer(page):
    adapter, _ = opened(page)
    assert adapter.submit(page) == f"{JOB.replace('/acme/', '/api/v1/board/acme/')}/apply 201"


def test_posting_urls():
    adapter = Rippling()
    for url in (
        JOB,
        f"{JOB}/apply",
        f"{JOB}?utm_source=x",
        "https://ats.rippling.com/en-GB/acme/jobs/0b6a3f8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b",
    ):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == JOB
    assert not adapter.detect("https://ats.rippling.com/acme/jobs", None)
    assert not adapter.detect(f"https://www.example.com/acme/jobs/{JOB[-36:]}", None)
