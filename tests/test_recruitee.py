"""The Recruitee adapter on tests/pages/recruitee, served as a posting's application page."""

from pathlib import Path

import pytest

from atsfill.adapters.recruitee import Recruitee
from atsfill.core import browser, matching
from atsfill.core.adapter import Blocked, Unsupported
from atsfill.core.results import Answer, FieldType

PAGE = Path(__file__).parent / "pages/recruitee/apply.html"
POSTING = "https://acme.recruitee.com/o/product-designer"
CUSTOM = "https://jobs.example.com/o/product-designer"
Q = "candidate.openQuestionAnswers"


def serve(route) -> None:
    if "/o/gone" in route.request.url:
        route.fulfill(status=404, content_type="text/html", body="<title>Not found</title>")
    elif route.request.url.endswith(".js"):
        route.fulfill(content_type="text/javascript", body="")
    else:
        route.fulfill(path=PAGE)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.recruitee.FORM_TIMEOUT_MS", 5_000)
        mp.setattr("atsfill.adapters.recruitee.SUBMIT_TIMEOUT_MS", 2_000)
        page.context.route("https://acme.recruitee.com/**", serve)
        page.context.route("https://jobs.example.com/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def opened(page, url: str = POSTING) -> tuple[Recruitee, list]:
    adapter = Recruitee()
    page.goto(url)
    assert adapter.detect(url, page)
    return adapter, adapter.browser_read(page)


def test_browser_read_opens_the_application(page):
    _, fields = opened(page)
    assert page.url == POSTING + "/c/new"
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "candidate.name",
        "candidate.email",
        "candidate.phone",
        "candidate.photo",
        "candidate.cv",
        "candidate.coverLetterFile",
        "candidate.coverLetter",
        f"{Q}.101.content",
        f"{Q}.102.flag",
        f"{Q}.103.content",
        f"{Q}.104.content",
        f"{Q}.105.content",
        f"{Q}.106.content",
        f"{Q}.107.content",
        f"{Q}.108.multiContent",
        f"{Q}.109.file",
        f"{Q}.111",
        "candidate.locations.value",
        f"{Q}.112.flag",
        "candidate.agreements.0.consent",
    ]
    assert by_id["candidate.name"].label == "Full name" and by_id["candidate.name"].required
    assert by_id["candidate.name"].max_length == 255
    assert by_id["candidate.phone"].type is FieldType.PHONE
    assert by_id["candidate.photo"].accept == [".png", ".jpg", ".jpeg"]
    assert by_id["candidate.cv"].type is FieldType.FILE and by_id["candidate.cv"].required
    assert ".pdf" in (by_id["candidate.cv"].accept or [])
    assert not by_id["candidate.coverLetterFile"].required
    letter = by_id["candidate.coverLetter"]
    assert (letter.type, letter.required, letter.one_of) == (
        FieldType.TEXT,
        False,
        "candidate.coverLetterFile",
    )
    assert by_id[f"{Q}.101.content"].label == "Which best describes your right to work?"
    assert by_id[f"{Q}.101.content"].options == ["Citizen", "I need a permit"]
    assert by_id[f"{Q}.102.flag"].options == ["Yes", "No"]
    assert by_id[f"{Q}.103.content"].label == "What is your expected annual salary? (a number)"
    assert not by_id[f"{Q}.104.content"].required
    assert by_id[f"{Q}.107.content"].type is FieldType.DATE
    assert by_id[f"{Q}.108.multiContent"].type is FieldType.MULTI_CHOICE
    assert by_id["candidate.locations.value"].options == [
        "Springfield (Calle Mayor 1, Springfield)",
        "Shelbyville",
    ]
    assert by_id[f"{Q}.112.flag"].label == "I agree with the privacy statement"
    assert by_id[f"{Q}.112.flag"].options == ["Yes", "No"] and by_id[f"{Q}.112.flag"].required
    assert by_id["candidate.agreements.0.consent"].label.startswith("By submitting my CV")


def test_every_answer_reads_back(page, resume):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {
        "candidate.name": "Ada Placeholder",
        "candidate.email": "ada@example.com",
        "candidate.phone": "+12025550143",
        "candidate.cv": resume,
        "candidate.coverLetterFile": resume,
        f"{Q}.101.content": "I need a permit",
        f"{Q}.102.flag": "No",
        f"{Q}.103.content": "50000",
        f"{Q}.104.content": "3",
        f"{Q}.105.content": "https://example.com",
        f"{Q}.106.content": "Placeholder answer.",
        f"{Q}.107.content": "2026-10-21",
        f"{Q}.108.multiContent": ["Job board", "Referral"],
        f"{Q}.109.file": resume,
        "candidate.locations.value": ["Shelbyville"],
        f"{Q}.112.flag": "Yes",
        "candidate.agreements.0.consent": "Yes",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)
    assert page.locator(f"[name='{Q}.102.flag']:checked").get_attribute("value") == "false"


def test_a_cover_letter_written_replaces_the_upload(page):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {"candidate.coverLetter": "Dear Acme,"}
    adapter.fill(page, answers)
    assert matching.check(fields, answers, adapter.read_back(page))[0].match
    assert not page.locator("[name='candidate.coverLetterFile']").count()


def test_a_custom_domain_is_detected_by_its_page(page):
    assert not Recruitee().detect(CUSTOM, None)
    _, fields = opened(page, CUSTOM)
    assert page.url == CUSTOM + "/c/new" and fields


def test_a_posting_gone(page):
    page.goto("https://acme.recruitee.com/o/gone")
    with pytest.raises(Unsupported, match="posting_gone"):
        Recruitee().browser_read(page)


def test_submit_returns_the_thanks_panel(page):
    adapter, _ = opened(page)
    assert adapter.submit(page) == f"{POSTING}/c/new (All done!)"


def test_an_address_that_already_applied_is_blocked(page):
    adapter, _ = opened(page)
    adapter.fill(page, {"candidate.email": "known@example.com"})
    with pytest.raises(Blocked, match="already_applied"):
        adapter.submit(page)


def test_posting_urls():
    adapter = Recruitee()
    for url in (POSTING, POSTING + "/c/new", POSTING + "?utm_source=x"):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == POSTING
    assert adapter.posting(CUSTOM + "/c/new") == CUSTOM
    assert not adapter.detect("https://acme.recruitee.com/", None)


def test_a_video_question_is_out_of_scope(page):
    adapter = Recruitee()
    page.goto(POSTING + "/c/new")
    page.locator("label[for$='111.content-31']").evaluate("l => l.append(' *')")
    video = next(f for f in adapter.browser_read(page) if f.unfillable)
    assert video.required
    assert video.unfillable == f"out_of_scope: {video.id} is a video recorded live"
