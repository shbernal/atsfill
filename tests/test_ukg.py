"""The UKG adapter on tests/pages/ukg, served as an Acme tenant's application form."""

import json
from pathlib import Path

import pytest

from atsfill.adapters.ukg import UKG
from atsfill.core import browser
from atsfill.core.results import FieldType, Is, OptionSet

PAGE = Path(__file__).parent / "pages/ukg/apply.html"
BOARD = "https://recruiting2.ultipro.com/acme1000acme/JobBoard/00000000-0000-4000-8000-000000000001"
OPPORTUNITY = "11111111-1111-4111-8111-111111111111"
POSTING = f"{BOARD}/OpportunityDetail?opportunityId={OPPORTUNITY}"
APPLY = f"{BOARD}/OpportunityApply?opportunityId={OPPORTUNITY}"
STATES = {
    "c-us": [{"Code": "DC", "Name": "District of Columbia"}, {"Code": "NY", "Name": "New York"}],
    "c-ca": [{"Code": "ON", "Name": "Ontario"}],
}


def serve(route) -> None:
    url = route.request.url
    if "/Candidate/GetStates" in url or "/GetStates?" in url:
        country = url.split("countryId=")[1].split("&")[0]
        route.fulfill(content_type="application/json", body=json.dumps(STATES.get(country, [])))
    else:
        route.fulfill(path=PAGE)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://recruiting2.ultipro.com/**", serve)
        yield page


@pytest.fixture
def resume(tmp_path) -> str:
    path = tmp_path / "CV Ada.pdf"
    path.write_bytes(b"%PDF-1.4")
    return str(path)


def opened(page) -> tuple[UKG, dict]:
    adapter = UKG()
    assert adapter.account_scope(POSTING) == "acme1000acme"
    page.goto(POSTING)
    return adapter, {f.id: f for f in adapter.browser_read(page)}


def test_detect_and_posting_key():
    adapter = UKG()
    assert adapter.detect(POSTING, None)
    assert adapter.detect(POSTING.replace("recruiting2.ultipro.com", "acme.rec.pro.ukg.net"), None)
    assert not adapter.detect(f"{BOARD}/OpportunityDetail", None)  # no opportunity
    assert not adapter.detect("https://recruiting.ultipro.com/acme/Careers", None)
    upper = APPLY.replace("acme1000acme", "ACME1000ACME") + "&utm_source=x"
    assert adapter.posting(upper) == POSTING


def test_login_url_comes_back_to_the_form():
    adapter = UKG()
    adapter.account_scope(POSTING)
    url = adapter.login_url("acme1000acme")
    assert url.startswith(f"{BOARD}/Account/Login?redirectUrl=/acme1000acme/JobBoard/")
    assert "OpportunityApply" in url
    with pytest.raises(ValueError):
        UKG().login_url("acme1000acme")


def test_browser_read_lists_each_rendering(page):
    _, fields = opened(page)
    assert page.url == APPLY
    assert fields["FirstName"].prefilled == "Ada" and fields["FirstName"].unfillable is None
    assert "PreferredName" in fields and fields["PreferredName"].unfillable  # read-only, empty
    assert fields["Prefix"].options == ["Dr.", "Mr."]
    assert fields["Phone"].type is FieldType.PHONE and fields["Phone"].required
    assert fields["Country"].options == ["Canada", "United States", "Cayman Islands"]
    state = fields["State"]
    assert state.required and state.options == []
    assert state.option_sets == [
        OptionSet(Is("Country", ["Canada"]), ["Ontario"]),
        OptionSet(Is("Country", ["United States"]), ["District of Columbia", "New York"]),
        OptionSet(Is("Country", ["Cayman Islands"]), []),
    ]
    assert list(fields).index("State") == list(fields).index("City") + 1
    assert fields["WillingToRelocate"].options == ["Yes", "No"]
    assert fields["documents"].type is FieldType.FILE and fields["documents"].required
    assert fields["availability_days"].options == ["Sun", "Mon", "Sat"]
    assert fields["notes"].max_length == 4000
    assert fields["ApplicantSource"].required
    assert fields["employeereferral"].options == ["Yes", "No"]
    assert not fields["employeereferral"].required
    assert fields["AvailableStartDate"].type is FieldType.DATE
    assert fields["AvailableStartDate"].default == "2026-10-25"
    assert fields["MultipleChoiceResponse0"].label == "Are you 18 years of age or older?"
    assert fields["MultipleChoiceResponse0"].required
    assert fields["TextResponse1"].label == "Why Acme?" and fields["TextResponse1"].required
    assert not fields["NumericResponse2"].required
    assert "InModal" not in fields


def test_fill_and_read_back(page, resume):
    adapter, _ = opened(page)
    answers = {
        "Prefix": "Dr.",
        "Phone": "+12025550143",
        "Country": "United States",
        "AddressLine1": "1 Placeholder Way",
        "City": "Washington",
        "State": "District of Columbia",
        "PostalCode": "20001",
        "WillingToRelocate": "Yes",
        "documents": resume,
        "availability_days": ["Mon", "Sat"],
        "notes": "Evenings",
        "ApplicantSource": "Indeed",
        "employeereferral": "No",
        "AvailableStartDate": "2026-12-01",
        "MultipleChoiceResponse0": "Yes",
        "TextResponse1": "Because",
        "NumericResponse2": "3",
    }
    adapter.fill(page, answers)
    shown = adapter.read_back(page)
    assert shown == {**answers, "documents": "CV Ada.pdf", "FirstName": "Ada"}
