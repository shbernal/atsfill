"""The Ashby adapter on tests/pages/ashby, served as jobs.ashbyhq.com by a stand-in for its API."""

import copy
import json
from pathlib import Path

import pytest

from atsfill.adapters.ashby import Ashby
from atsfill.core import browser, matching
from atsfill.core.adapter import Blocked
from atsfill.core.results import FieldType

PAGES = Path(__file__).parent / "pages/ashby"
URL = "https://jobs.ashbyhq.com/acme/0b6c9f4e-1d1a-4c5e-9d43-7f0f3c1e2a10"
SURVEY_RENDER = "survey-render"  # the id survey saves come back under, not the posting's
UPLOAD = "https://jobs.ashbyhq.com/upload"  # Ashby hands out an S3 address


class Ashby_API:
    def __init__(self) -> None:
        self.job = json.loads((PAGES / "job.json").read_text())
        self.values: dict[str, object] = {}
        self.submit = {"applicationFormResult": {"__typename": "FormSubmitSuccess", "_": None}}

    def render(self, form: dict) -> dict:
        render = copy.deepcopy(form)
        for section in render["sections"]:
            for entry in section["fieldEntries"]:
                value = self.values.get(entry["field"]["path"])
                if isinstance(value, dict) and "filename" in value:
                    entry["fieldValue"] = {"__typename": "File", "filename": value["filename"]}
                elif value is not None:
                    entry["fieldValue"] = {"__typename": "JSONBox", "value": value}
        return render

    def handle(self, route) -> None:
        url = route.request.url
        if url == UPLOAD:
            return route.fulfill(status=204)
        if "/api/non-user-graphql" not in url:
            return route.fulfill(path=PAGES / "application.html")
        op = url.partition("?op=")[2]
        variables = route.request.post_data_json["variables"]
        match op:
            case "ApiJobPosting":
                data = {"jobPosting": self.job}
            case "ApiCreateFileUploadHandle":
                handle = {"handle": variables["filename"], "url": UPLOAD, "fields": {}}
                data = {"fileUploadHandle": handle}
            case "ApiSetFormValue" | "ApiSetFormValueToFile":
                value = variables.get("value", {"filename": variables.get("fileHandle")})
                self.values[variables["path"]] = value
                if variables["formRenderIdentifier"] == SURVEY_RENDER:
                    render = self.render(self.job["surveyForms"][0]) | {"id": SURVEY_RENDER}
                else:
                    render = self.render(self.job["applicationForm"])
                data = {op[3].lower() + op[4:]: render}
            case _:
                if isinstance(self.submit, str):
                    return route.fulfill(json={"errors": [{"message": self.submit}]})
                data = {"submitMultipleFormsAction": self.submit | {"messages": None}}
        route.fulfill(json={"data": data})


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with (
        pytest.MonkeyPatch.context() as mp,
        browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page,
    ):
        mp.setattr("atsfill.adapters.ashby.SETTLE_S", 0.3)  # shorter than the fixture's slow upload
        yield page


@pytest.fixture
def ashby(page):
    api = Ashby_API()
    page.context.route("https://jobs.ashbyhq.com/**", api.handle)
    yield Ashby(), api
    page.context.unroute("https://jobs.ashbyhq.com/**")


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "resume.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def test_browser_read_lists_the_visible_fields_of_every_form(ashby, page):
    adapter, _ = ashby
    page.goto(URL)
    fields = adapter.browser_read(page)
    assert [f.id for f in fields] == [
        "_systemfield_name",
        "_systemfield_resume",
        "_systemfield_location",
        "site",
        "start",
        "right",
        "level",
        "source",
        "langs",
        "age",
    ]
    types = {f.id: f.type for f in fields}
    assert types["start"] is FieldType.DATE
    assert types["_systemfield_location"] is FieldType.TYPEAHEAD
    assert types["site"] is FieldType.URL
    assert next(f for f in fields if f.id == "right").options == ["Yes", "No"]


def test_a_known_gap_and_an_unknown_type_are_listed_unfillable(ashby, page):
    adapter, api = ashby
    entries = api.job["applicationForm"]["sections"][0]["fieldEntries"]
    for path, kind in [("schools", "EducationHistory"), ("mood", "Slider")]:
        entries.append({"isRequired": False, "field": {"path": path, "title": path, "type": kind}})
    page.goto(URL)
    unfillable = {f.id: f.unfillable for f in adapter.browser_read(page) if f.unfillable}
    assert unfillable == {
        "schools": "unsupported_rendering: education_history: schools is a list of schools",
        "mood": "unhandled_rendering: mood is an Ashby Slider field",
    }


def test_every_answer_reads_back_from_ashby(ashby, page, resume):
    adapter, _ = ashby
    page.goto(URL)
    fields = adapter.browser_read(page)
    answers = {
        "_systemfield_name": "Ada Lovelace",
        "_systemfield_resume": resume,
        "_systemfield_location": "Paris",
        "site": "https://ada.example",
        "start": "2026-11-02",
        "right": "Yes",
        "level": "Fluent",
        "source": "LinkedIn",
        "langs": ["French", "English"],
        "age": "I prefer not to answer",
    }
    adapter.fill(page, answers)
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == []
    assert len(checks) == len(answers)


def test_submit_returns_the_evidence(ashby, page):
    adapter, _ = ashby
    page.goto(URL)
    adapter.browser_read(page)
    assert "FormSubmitSuccess" in (adapter.submit(page) or "")


def test_a_low_recaptcha_score_is_blocked(ashby, page):
    adapter, api = ashby
    api.submit = "RECAPTCHA_SCORE_BELOW_THRESHOLD"
    page.goto(URL)
    adapter.browser_read(page)
    with pytest.raises(Blocked, match="captcha_score"):
        adapter.submit(page)


def test_posting_and_detection():
    adapter = Ashby()
    assert adapter.detect(URL + "/application?utm_source=x", None)
    assert adapter.posting(URL + "/application?utm_source=x") == URL
    assert not adapter.detect("https://example.com/careers?ashby_jid=1", None)
    assert adapter.request_read("https://example.com/careers?ashby_jid=1") is None


def test_a_career_page_embedding_ashby(page):
    adapter = Ashby()
    jid = URL.rsplit("/", 1)[1]
    page.route(
        "https://careers.example.com/**",
        lambda r: r.fulfill(
            content_type="text/html",
            body=f'<iframe src="https://jobs.ashbyhq.com/acme/{jid}?embed=js"></iframe>',
        ),
    )
    page.route("https://jobs.ashbyhq.com/**", lambda r: r.fulfill(body=""))
    page_url = f"https://careers.example.com/jobs?ashby_jid={jid}"
    page.goto(page_url)
    assert adapter.detect(page_url, page)
    assert adapter.posting(page_url) == URL
    page.unroute_all()


def test_an_overlaid_submit_ends_overlaid(ashby, page):
    adapter, _ = ashby
    page.goto(URL)
    adapter.browser_read(page)
    page.evaluate("""() => {
        const cover = document.createElement("div");
        cover.className = "sheet";
        cover.style.cssText = "position:fixed;inset:0;z-index:9999;background:#fff";
        document.body.append(cover);
    }""")
    with pytest.raises(browser.Overlaid, match="sheet"):
        adapter.submit(page)
