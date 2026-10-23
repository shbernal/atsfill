"""The Zoho Recruit adapter on tests/pages/zohorecruit, served as a job page and its form API."""

import json
from pathlib import Path

import pytest

from atsfill.adapters.zohorecruit import ZohoRecruit, _form
from atsfill.core import browser, matching
from atsfill.core.adapter import Blocked, Unsupported
from atsfill.core.results import Answer, FieldType
from tests import testuser

PAGES = Path(__file__).parent / "pages/zohorecruit"
JOB = "https://acme.zohorecruit.com/jobs/Careers/1234567/Placeholder-Role?source=CareerSite"
API = (
    "https://acme.zohorecruit.com/recruit/v2/public/Job_Openings/1234567/apply/"
    "100000000000000001?pagename=Careers&source=CareerSite"
)
FORM = json.loads((PAGES / "form.json").read_text())


def serve(route) -> None:
    url = route.request.url
    if url == API:
        route.fulfill(json=FORM)
    elif route.request.method == "POST":
        route.fulfill(status=201, json={"code": "success"})
    elif "/7654321" in url:
        route.fulfill(content_type="text/html", body="<h1>This job is no longer available</h1>")
    else:
        route.fulfill(path=PAGES / "job.html")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.zohorecruit.FORM_TIMEOUT_MS", 5_000)
        mp.setattr("atsfill.adapters.zohorecruit.COOKIE_TIMEOUT_MS", 1_000)
        page.context.route("https://acme.zohorecruit.com/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    return str(testuser.resume(tmp_path / "CV Ada.pdf"))


def opened(page) -> tuple[ZohoRecruit, list]:
    adapter = ZohoRecruit()
    page.goto(JOB)
    assert adapter.detect(JOB, None)
    return adapter, adapter.read_form(page)


def test_read_form(page):
    _, fields = opened(page)
    by_id = {f.id: f for f in fields}
    assert list(by_id) == [
        "First_Name",
        "Salutation",
        "Last_Name",
        "Email",
        "Mobile",
        "Date_of_Birth",
        "Languages",
        "Skill_Set",
        "Experience_in_Years",
        "Batch",
        "Certificate_Link",
        "Additional_Info",
        "Experience_Details.I_currently_work_here",
        "Experience_Details.Company",
        "Experience_Details.Work_Duration.from",
        "Experience_Details.Work_Duration.to",
        "RESUME",
        "$photo_id",
        "100000000000000050",
        "100000000000000051",
        "100000000000000054",
    ]
    assert by_id["Salutation"].options == ["Mr.", "Ms."]
    assert by_id["Mobile"].type is FieldType.PHONE
    assert by_id["Date_of_Birth"].type is FieldType.DATE
    assert by_id["Languages"].type is FieldType.MULTI_CHOICE
    assert by_id["Skill_Set"].label == "Skill Set (comma-separated)"
    assert by_id["Experience_in_Years"].label == "Experience in Years (a number)"
    assert by_id["Certificate_Link"].label == (
        "Certificate Link (asked when 'Batch' is not No Batch)"
    )
    assert not by_id["Certificate_Link"].required
    assert by_id["Experience_Details.I_currently_work_here"].options == ["Yes", "No"]
    assert by_id["Experience_Details.Work_Duration.to"].label == (
        "Experience Details: Work Duration end (MM/YYYY)"
        " (asked when 'Experience Details: I currently work here' is No)"
    )
    assert by_id["RESUME"].required
    assert by_id["RESUME"].accept == [".doc", ".docx", ".odt", ".pdf", ".rtf"]
    assert by_id["100000000000000050"].label == "How many years of experience do you have?"
    assert by_id["100000000000000051"].options == ["Yes", "No"]
    assert by_id["100000000000000054"].type is FieldType.CHOICE


def test_read_api_lists_the_same_fields(page):
    _, fields = opened(page)
    shape = lambda fs: [(f.id, f.label, f.type, f.required, f.options) for f in fs]  # noqa: E731
    assert shape(c.field for c in _form(FORM)[0]) == shape(fields)


def test_every_answer_reads_back(page, resume, tmp_path):
    adapter, fields = opened(page)
    answers: dict[str, Answer] = {
        "First_Name": "Ada",
        "Salutation": "Ms.",
        "Last_Name": "Placeholder",
        "Email": "ada@example.com",
        "Mobile": "+12025550143",
        "Date_of_Birth": "1990-11-20",
        "Languages": ["English", "French"],
        "Skill_Set": "Python, Placeholder skill with a long name",
        "Experience_in_Years": "3",
        "Batch": "Batch 1",
        "Certificate_Link": "https://example.com",
        "Additional_Info": "Placeholder answer.",
        "Experience_Details.I_currently_work_here": "No",
        "Experience_Details.Company": "Acme",
        "Experience_Details.Work_Duration.from": "01/2020",
        "Experience_Details.Work_Duration.to": "05/2024",
        "RESUME": resume,
        "$photo_id": str(testuser.photo(tmp_path / "photo.png")),
        "100000000000000050": "3",
        "100000000000000051": "No",
        "100000000000000054": "30 days",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_a_field_its_answer_hides_is_not_filled(page):
    adapter, _ = opened(page)
    adapter.fill(page, {"Batch": "No Batch", "Certificate_Link": "https://example.com"})
    read_back = adapter.read_back(page)
    assert read_back["Batch"] == "No Batch"
    assert "Certificate_Link" not in read_back


def test_the_test_user_leaves_out_what_its_answers_hide(tmp_path):
    fields = [c.field for c in _form(FORM)[0]]
    answers = testuser.answers(fields, tmp_path / "cv.pdf")
    assert answers["Batch"] == "No Batch"
    assert "Certificate_Link" not in answers
    assert answers["Experience_Details.I_currently_work_here"] == "Yes"
    assert "Experience_Details.Work_Duration.to" not in answers


def test_a_posting_gone(page):
    page.goto("https://acme.zohorecruit.com/jobs/Careers/7654321")
    with pytest.raises(Unsupported, match="posting_gone"):
        ZohoRecruit().read_form(page)


def test_an_unknown_field_type_is_listed_unfillable():
    form = json.loads(json.dumps(FORM))
    form["webforms"][0]["sections"][0]["fields"][0]["data_type"] = "autonumber"
    first = _form(form)[0][0].field
    assert first.id == "First_Name"
    assert first.unfillable == "unhandled_rendering: First_Name is a autonumber"


def test_a_field_missing_from_the_page_is_listed_unfillable(page):
    page.goto(JOB)
    page.evaluate("() => document.getElementById('rec-form_100000000000000012').remove()")
    fields = {f.id: f for f in ZohoRecruit().read_form(page)}
    assert fields["Last_Name"].unfillable == "unhandled_rendering: Last_Name is not on the page"
    assert fields["First_Name"].unfillable is None


def test_a_form_with_a_captcha_is_not_sent(page):
    adapter, _ = opened(page)
    with pytest.raises(Blocked, match="captcha"):
        adapter.submit(page)


def test_submit_returns_the_api_answer(page):
    adapter, _ = opened(page)
    adapter._captcha = False  # a form without the CAPTCHA section
    assert adapter.submit(page) == (
        "https://acme.zohorecruit.com/recruit/v2/public/Job_Openings/1234567/apply/"
        "100000000000000001 201"
    )


def test_posting_urls():
    adapter = ZohoRecruit()
    key = "https://acme.zohorecruit.com/recruit/v2/public/Job_Openings/1234567"
    for url in (
        JOB,
        "https://acme.zohorecruit.com/jobs/Careers/1234567",
        "https://acme.zohorecruit.com/jobs/Careers/1234567?utm_source=x",
    ):
        assert adapter.detect(url, None)
        assert adapter.posting(url) == key
    assert adapter.detect("https://acme.zohorecruit.eu/jobs/Karriere/1234567", None)
    assert not adapter.detect("https://acme.zohorecruit.com/jobs/Careers", None)
    assert not adapter.detect("https://www.example.com/jobs/Careers/1234567", None)
