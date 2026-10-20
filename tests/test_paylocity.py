"""The Paylocity adapter on tests/pages/paylocity, served as recruiting.paylocity.com."""

import io
from pathlib import Path

import pytest

from atsfill.adapters import paylocity
from atsfill.adapters.paylocity import Paylocity
from atsfill.core import browser, matching
from atsfill.core.adapter import Unsupported
from atsfill.core.results import FieldType

PAGE = Path(__file__).parent / "pages/paylocity/apply.html"
JOB = "https://recruiting.paylocity.com/Recruiting/Jobs/Details/1234"


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route(
            "https://recruiting.paylocity.com/**",
            lambda route: (
                route.fulfill(path=PAGE)
                if "/Jobs/Apply/" in route.request.url
                else route.fulfill(content_type="text/html", body="<h1>Job</h1>")
            ),
        )
        yield page


@pytest.fixture
def opened(page) -> tuple[Paylocity, list]:
    adapter = Paylocity()
    page.goto(JOB + "?utm_source=board")
    assert adapter.detect(page.url, page)
    return adapter, adapter.read_form(page)


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def test_detection_and_posting():
    adapter = Paylocity()
    assert adapter.detect(JOB, None)
    assert not adapter.detect("https://recruiting.paylocity.com/Recruiting/Jobs/All/abc", None)
    apply = "https://recruiting.paylocity.com/Recruiting/Jobs/Apply/1234"
    assert adapter.posting(apply) == adapter.posting(JOB + "?utm_source=board")


def test_read_form(opened):
    fields = {f.id: f for f in opened[1]}
    assert list(fields) == [
        "resume",
        "info.firstName",
        "info.lastName",
        "info.middleName",
        "info.preferredName",
        "info.cellPhone",
        "info.smsOptedIn",
        "public-site-address-address-1",
        "public-site-address-county",
        "public-site-address-us-state",
        "info.haveYouWorkedWithUsBefore",
        "info.priorEmploymentPositionHeld",
        "info.priorEmploymentStartDate",
        "info.priorEmploymentEndDate",
        "info.howDidYouHearAboutUs",
        "info.desiredSalaryDescription",
        "info.skills",
        "coverLetter",
        "workHistory.companyName.0",
        "txt-workHistory-startDate-0",
        "txt-workHistory-endDate-0",
        "workHistory.currentlyWorkingHere.0",
        "references.name.0",
        "references.referenceType.0",
        "acknowledgements.eeoGender",
        "acknowledgements.racialOrEthnicGroup",
        "screener.501",
        "screener.502",
        "screener.503",
        "applyAcknowledgement",
    ]  # the country, an optional date of birth and the optional education entry are left out
    assert fields["info.firstName"].required and not fields["info.middleName"].required
    assert fields["info.cellPhone"].type is FieldType.PHONE
    assert fields["info.smsOptedIn"].options == ["Yes", "No"]
    assert fields["info.smsOptedIn"].label.startswith("We may use SMS")
    assert fields["public-site-address-address-1"].required
    assert fields["public-site-address-address-1"].max_length == 50
    assert fields["public-site-address-us-state"].options == ["DC", "TX"]
    prior = fields["info.priorEmploymentStartDate"]
    assert prior.type is FieldType.DATE and not prior.required
    assert "is Yes" in prior.label
    assert fields["info.howDidYouHearAboutUs"].options == ["Online Job Board", "Other"]
    assert fields["txt-workHistory-startDate-0"].label == "Start Date (MM/YYYY)"
    assert fields["workHistory.currentlyWorkingHere.0"].options == ["No", "Yes"]
    assert fields["references.name.0"].label == "Reference 1: Name"
    assert fields["references.referenceType.0"].options == ["Personal", "Professional"]
    assert fields["screener.501"].required and fields["screener.501"].options == ["Yes", "No"]
    assert fields["screener.502"].type is FieldType.MULTI_CHOICE
    assert fields["screener.503"].label == "Why this role?"
    assert fields["applyAcknowledgement"].required


def test_read_api_lists_the_same_fields(opened, monkeypatch):
    monkeypatch.setattr(
        paylocity.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(PAGE.read_bytes())
    )
    assert Paylocity().read_api(JOB) == opened[1]


def test_every_answer_reads_back_across_steps(opened, page, resume):
    adapter, fields = opened
    answers = {
        "resume": resume,
        "info.firstName": "Ada",
        "info.lastName": "Placeholder",
        "info.cellPhone": "+12025550143",
        "info.smsOptedIn": "Yes",
        "public-site-address-address-1": "1 Example Street",
        "public-site-address-county": "Example County",
        "public-site-address-us-state": "TX",
        "info.haveYouWorkedWithUsBefore": "Yes",
        "info.priorEmploymentPositionHeld": "Clerk",
        "info.priorEmploymentStartDate": "2019-02-03",
        "info.priorEmploymentEndDate": "2020-04-05",
        "info.howDidYouHearAboutUs": "Other",
        "info.desiredSalaryDescription": "50000",
        "info.skills": "Bookkeeping",
        "coverLetter": resume,
        "workHistory.companyName.0": "Acme",
        "txt-workHistory-startDate-0": "01/2020",
        "txt-workHistory-endDate-0": "03/2022",
        "workHistory.currentlyWorkingHere.0": "No",
        "references.name.0": "Jane Doe",
        "references.referenceType.0": "Professional",
        "acknowledgements.eeoGender": "I choose not to self-identify",
        "acknowledgements.racialOrEthnicGroup": "Two or More Races",
        "screener.501": "Yes",
        "screener.502": ["Days", "Weekends"],
        "screener.503": "Placeholder",
        "applyAcknowledgement": "Yes",
    }
    assert not matching.problems(fields, answers)
    adapter.fill(page, answers)
    assert page.locator("#step-2").is_visible()
    checks = matching.check(fields, answers, adapter.read_back(page))
    assert [c for c in checks if not c.match] == []


def test_a_required_unknown_field_is_unsupported():
    data = paylocity._page_data(PAGE.read_text())
    info = data["customJobApplication"]["sections"][0]["fields"]
    next(f for f in info if f["name"] == "dateOfBirth")["isRequired"] = True
    with pytest.raises(Unsupported, match="dateOfBirth"):
        paylocity._form(data)
