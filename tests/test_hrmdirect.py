"""The HRMDirect adapter on tests/pages/hrmdirect, served as an Acme org's apply page."""

from pathlib import Path

import pytest

from atsfill.adapters.hrmdirect import HRMDirect
from atsfill.core import browser, matching
from atsfill.core.adapter import NotTaken, Unsupported
from atsfill.core.results import Answer, FieldType
from tests import testuser

PAGES = Path(__file__).parent / "pages/hrmdirect"
JOB_ID = "11111111-2222-3333-4444-555555555555"
GONE = "99999999-2222-3333-4444-555555555555"
HOST = "https://acme.clearcompany.com"
APPLY = f"{HOST}/careers/jobs/{JOB_ID}/apply"


def serve(route) -> None:
    url = route.request.url
    if route.request.method == "POST":
        route.fulfill(status=200, json={"ok": True})
    elif GONE in url:
        route.fulfill(status=200, content_type="text/html", body="<p>Not found</p>")
    else:
        route.fulfill(path=PAGES / "apply.html")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.hrmdirect.FORM_TIMEOUT_MS", 3_000)
        mp.setattr("atsfill.adapters.hrmdirect.PAGE_TIMEOUT_MS", 3_000)
        mp.setattr("atsfill.adapters.hrmdirect.SETTLE_MS", 100)
        page.context.route(f"{HOST}/**", serve)
        yield page


def opened(page, url: str = APPLY, fresh: bool = True) -> tuple[HRMDirect, list]:
    adapter = HRMDirect()
    page.goto(url)
    if fresh:
        page.evaluate("() => sessionStorage.clear()")
        page.goto(url)
    assert adapter.detect(url, None)
    return adapter, adapter.browser_read(page)


def test_a_fresh_read_stops_at_the_first_page(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == ["file.resume", "file.applinkedin", "file.appcoverletter"]
    assert by_id["file.resume"].type is FieldType.FILE
    assert by_id["file.resume"].required
    assert by_id["file.applinkedin"].unfillable.startswith("out_of_scope")
    assert not by_id["file.appcoverletter"].required


def test_every_answer_reads_back(page, tmp_path):
    adapter, _ = opened(page)
    cv = str(testuser.resume(tmp_path / "cv.pdf"))
    with pytest.raises(NotTaken, match="read again"):  # the first page saved, the second unread
        adapter.fill(page, {"file.resume": cv})
    page.goto(APPLY)
    fields = adapter.browser_read(page)
    by_id = {f.id: f for f in fields}
    assert by_id["t1"].max_length == 50
    assert by_id["s1"].options == ["Canada", "United States"]
    assert by_id["r-1"].type is FieldType.CHOICE
    assert by_id["r-1"].options == ["Yes", "No"]
    assert by_id["m-1"].type is FieldType.MULTI_CHOICE
    assert by_id["c-1"].options == ["Yes", "No"]
    assert by_id["c-1"].required
    assert by_id["d1"].type is FieldType.DATE
    answers: dict[str, Answer] = {
        "file.resume": cv,
        "file.appcoverletter": cv,
        "t1": "Ada",
        "t2": "Placeholder",
        "s1": "United States",
        "s2": "District of Columbia",
        "r-1": "Yes",
        "m-1": ["Indeed", "Other"],
        "c-1": "Yes",
        "w1": "Acme",
        "d1": "2020-01-31",
    }
    adapter.fill(page, answers)
    assert page.locator("#d1").input_value() == "01/31/2020"
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_the_test_user_answers_every_field(page, tmp_path):
    adapter, _ = opened(page)
    with pytest.raises(NotTaken):
        adapter.fill(page, {"file.resume": str(testuser.resume(tmp_path / "first.pdf"))})
    adapter, fields = opened(page, fresh=False)
    answers = testuser.answers(fields, testuser.resume(tmp_path / "cv.pdf"))
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []


def test_an_option_the_list_does_not_hold(page, tmp_path):
    adapter, _ = opened(page)
    cv = str(testuser.resume(tmp_path / "cv.pdf"))
    with pytest.raises(NotTaken, match="Atlantis"):
        adapter.fill(page, {"file.resume": cv, "s1": "Atlantis"})


def test_a_posting_gone(page):
    with pytest.raises(Unsupported, match="posting_gone"):
        opened(page, f"{HOST}/careers/jobs/{GONE}/apply")


def test_submit_returns_the_answer(page, tmp_path):
    adapter, _ = opened(page)
    with pytest.raises(NotTaken):
        adapter.fill(page, {"file.resume": str(testuser.resume(tmp_path / "cv.pdf"))})
    assert adapter.submit(page) == f"{HOST}/api/v1/careers/apply/submit 200"


def test_posting_urls():
    adapter = HRMDirect()
    key = f"https://apply.clearcompany.com/jobs/{JOB_ID}"
    for url in (
        APPLY,
        f"{APPLY}?source=CJB-0",
        f"{HOST}/careers/jobs/{JOB_ID.upper()}/apply/{GONE}",
        f"https://apply.clearcompany.com/acmesite?noheader=False&jobId={JOB_ID}",
    ):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == key
    old = "https://acme.hrmdirect.com/employment/job-opening.php?req=123&req_loc=4"
    assert adapter.detect(old, None)
    assert adapter.account_scope(old) == "acme"
    assert adapter.account_scope(APPLY) == "acme"
    assert not adapter.detect("https://apply.clearcompany.com/acme", None)
    assert not adapter.detect("https://acme.hrmdirect.com/employment/job-openings.php", None)
