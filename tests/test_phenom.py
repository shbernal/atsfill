"""The Phenom adapter on tests/pages/phenom, served as careers.acme.example with its
configuration on Phenom's CDN."""

import io
import json
from pathlib import Path

import pytest

from atsfill.adapters import phenom
from atsfill.adapters.phenom import Phenom
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import FieldType

PAGES = Path(__file__).parent / "pages/phenom"
JOB = "https://careers.acme.example/us/en/job/ACMEUS1001EXTERNALENUS/Data-Analyst"
APPLY = "https://careers.acme.example/us/en/apply?jobSeqNo=ACMEUS1001EXTERNALENUS"
CONFIG = f"{phenom.CDN}/ACMEUS/workday/external/careersite/singlepage"
REFERENCES = json.loads((PAGES / "references.json").read_text())


def served(url: str, body: bytes | str | None) -> bytes | None:
    """What the fixture serves at `url`: a page, the configuration, a loader's answer."""
    if url.endswith("/applySubmit"):
        posted = json.loads(body or "{}")["formData"]
        return json.dumps(REFERENCES[posted["applyddokey"]]).encode()
    if url.startswith(CONFIG):
        name = url.rsplit("/", 1)[-1]
        return (PAGES / name).read_bytes() if (PAGES / name).exists() else None
    if url.startswith("https://careers.acme.example/"):
        return (PAGES / ("apply.html" if "/apply" in url else "job.html")).read_bytes()
    return None


def route(route):
    body = served(route.request.url, route.request.post_data)
    if body is None:
        route.fulfill(status=404)
    else:
        route.fulfill(body=body, headers={"Access-Control-Allow-Origin": "*"})


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://careers.acme.example/**", route)
        page.context.route(f"{phenom.CDN}/**", route)
        yield page


@pytest.fixture
def opened(page) -> tuple[Phenom, list]:
    adapter = Phenom()
    page.goto(JOB)
    assert adapter.detect(page.url, page)
    return adapter, adapter.read_form(page)


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def test_detection_and_posting():
    adapter = Phenom()
    assert adapter.detect(JOB, None)
    assert adapter.detect(APPLY + "&step=2", None)
    assert not adapter.detect("https://careers.acme.example/us/en/search-results", None)
    assert adapter.posting(JOB) == adapter.posting(APPLY + "&step=2&stepname=agreement")


def test_read_form_lists_every_step_from_the_configuration(opened):
    adapter, read = opened
    fields = {f.id: f for f in read}
    assert list(fields) == [
        "resume",
        "firstName",
        "country",
        "cntryFields.city",
        "state",
        "phoneWidget.countryPhoneCode",
        "phoneWidget.phoneNumber",
        "sourceType",
        "applicantSource",
        "nationality",
        "emailAgreement",
        "smsOptIn",
        "authorized",
        "contactBy",
        "startDate",
        "experienceData[0].title",
        "experienceData[0].fromTo.startDate",
        "experienceData[0].fromTo.endDate",
        "educationData[0].schoolName",
        "educationData[0].fromTo.startDate",
        "educationData[0].fromTo.endDate",
        "jsqData.Q1.a",
        "jsqData.Q1.a_dot_a",
        "comments",
        "custAgreement",
    ]  # no heading, hidden value, French-only box or entry shown only once Add is clicked
    resume = fields["resume"]
    assert resume.required  # a summary is asked without one, which is not read
    assert resume.accept == [".doc", ".docx", ".pdf", ".txt"]
    assert fields["firstName"].label == "First Name" and fields["firstName"].required
    assert fields["country"].options == ["Canada", "United States"]
    assert fields["state"].options == ["Texas", "Utah"]
    assert fields["state"].label == "State (options for 'Country' 'United States')"
    assert fields["phoneWidget.phoneNumber"].type is FieldType.PHONE
    assert fields["phoneWidget.phoneNumber"].required
    assert fields["sourceType"].options == ["Job Board", "Referral"]
    assert fields["applicantSource"].options == ["LinkedIn", "Indeed", "Employee"]
    assert fields["nationality"].options == ["American", "Canadian"]
    assert fields["emailAgreement"].options == ["Yes", "No"]
    assert fields["smsOptIn"].options == ["No", "Yes"] and not fields["smsOptIn"].required
    assert fields["smsOptIn"].label == "Text me about my application"
    assert fields["authorized"].options == ["Yes", "No"] and fields["authorized"].required
    assert fields["contactBy"].type is FieldType.MULTI_CHOICE
    assert fields["startDate"].type is FieldType.DATE
    assert fields["experienceData[0].fromTo.startDate"].label == "From (MM/YYYY)"
    assert not fields["experienceData[0].fromTo.endDate"].required
    assert fields["educationData[0].fromTo.endDate"].label == "To (year)"
    assert fields["educationData[0].schoolName"].type is FieldType.TYPEAHEAD
    assert fields["jsqData.Q1.a"].options == ["Yes", "No"]
    follow_up = fields["jsqData.Q1.a_dot_a"]
    asked = "(asked when 'Have you worked for Acme before?' is Yes)"
    assert follow_up.label == f"When did you leave? {asked}"
    assert not follow_up.required
    assert fields["comments"].type is FieldType.TEXT and not fields["comments"].required


def test_read_api_lists_the_same_fields(opened, monkeypatch):
    def open_(opener, request, timeout):
        if (body := served(request.full_url, request.data)) is None:
            raise OSError(f"404 from {request.full_url}")
        return io.BytesIO(body)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", open_)
    assert Phenom().read_api(JOB) == opened[1]


def answers(resume: str) -> dict:
    return {
        "resume": resume,
        "firstName": "Ada",
        "country": "United States",
        "cntryFields.city": "London",
        "state": "Utah",
        "phoneWidget.countryPhoneCode": "United States of America (+1)",
        "phoneWidget.phoneNumber": "+12025550143",
        "sourceType": "Referral",
        "applicantSource": "Employee",
        "nationality": "Canadian",
        "emailAgreement": "Yes",
        "smsOptIn": "No",
        "authorized": "Yes",
        "contactBy": ["SMS"],
        "startDate": "2027-02-14",
        "experienceData[0].title": "Analyst",
        "experienceData[0].fromTo.startDate": "03/2019",
        "educationData[0].schoolName": "Acme",
        "educationData[0].fromTo.endDate": "2004",
        "jsqData.Q1.a": "Yes",
        "jsqData.Q1.a_dot_a": "2020",
        "comments": "Placeholder",
        "custAgreement": "Yes",
    }


def test_every_answer_reads_back_across_steps(opened, page, resume):
    adapter, fields = opened
    assert not matching.problems(fields, answers(resume))
    adapter.fill(page, answers(resume))
    assert "stepname=agreement" in page.url  # the last step, its submit left unclicked
    read_back = adapter.read_back(page)
    assert read_back["educationData[0].schoolName"] == "Acme University"
    checks = matching.check(fields, answers(resume), read_back)
    assert [c for c in checks if not c.match] == []


def test_a_follow_up_its_trigger_hides_has_no_read_back(opened, page, resume):
    adapter, fields = opened
    given = answers(resume) | {"jsqData.Q1.a": "No"}
    adapter.fill(page, given)
    checks = matching.check(fields, given, adapter.read_back(page))
    assert [(c.field_id, c.read_back) for c in checks if not c.match] == [
        ("jsqData.Q1.a_dot_a", None)
    ]


def test_an_apply_link_to_another_system_is_unsupported(page):
    page.route(
        JOB,
        lambda route: route.fulfill(
            content_type="text/html",
            body='<script>window.phApp = {}</script><a data-ph-at-id="apply-link"'
            ' href="https://acme.wd5.myworkdayjobs.com/External/job/1/apply">Apply</a>',
        ),
    )
    try:
        page.goto(JOB)
        with pytest.raises(Unsupported, match="external_step: .*myworkdayjobs"):
            Phenom().read_form(page)
    finally:
        page.unroute(JOB)


def test_a_first_step_other_than_the_form_is_a_known_gap(page, monkeypatch):
    monkeypatch.setattr(phenom, "FORM_TIMEOUT_MS", 500)
    body = "<script>history.replaceState({}, '', location.href + '&stepname=login')</script>"
    page.route(APPLY, lambda route: route.fulfill(content_type="text/html", body=body))
    try:
        page.goto(APPLY)
        with pytest.raises(Unsupported, match="unsupported_flow: first_step: .*'login'"):
            Phenom().read_form(page)
    finally:
        page.unroute(APPLY)
