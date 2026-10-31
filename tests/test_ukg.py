"""The UKG adapter on tests/pages/ukg, served as an Acme tenant's application form."""

import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from atsfill.adapters.ukg import FORM, UKG, _register
from atsfill.core import browser
from atsfill.core.adapter import Refused, SignUpDetails
from atsfill.core.results import FieldType, Is, OptionSet

PAGE = Path(__file__).parent / "pages/ukg/apply.html"
REGISTER_PAGE = PAGE.with_name("register.html")
BOARD = "https://recruiting2.ultipro.com/acme1000acme/JobBoard/00000000-0000-4000-8000-000000000001"
OPPORTUNITY = "11111111-1111-4111-8111-111111111111"
POSTING = f"{BOARD}/OpportunityDetail?opportunityId={OPPORTUNITY}"
APPLY = f"{BOARD}/OpportunityApply?opportunityId={OPPORTUNITY}"
REGISTER = "https://recruiting2.ultipro.com/ACME1000ACME/AuthCode/Register"
SIGN_IN_PAGE = PAGE.with_name("signin.html")
SIGN_IN = "https://signin-us.ultipro.com/u/login"
STATES = {
    "c-us": [{"Code": "DC", "Name": "District of Columbia"}, {"Code": "NY", "Name": "New York"}],
    "c-ca": [{"Code": "ON", "Name": "Ontario"}],
}


def serve(route) -> None:
    url = route.request.url
    if "/Candidate/GetStates" in url or "/GetStates?" in url:
        country = url.split("countryId=")[1].split("&")[0]
        route.fulfill(content_type="application/json", body=json.dumps(STATES.get(country, [])))
    elif "/AuthCode/Register" in url:
        route.fulfill(path=REGISTER_PAGE)
    else:
        route.fulfill(path=PAGE)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.context.route("https://recruiting2.ultipro.com/**", serve)
        page.context.route(SIGN_IN, lambda route: route.fulfill(path=SIGN_IN_PAGE))
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


def test_the_details_step_takes_the_phone_and_declines_the_optional_consents(page):
    page.goto(REGISTER)
    _register(page, SignUpDetails(name="Jane Doe", phone="+12125550123"))
    sent = json.loads(unquote(urlsplit(page.url).fragment))
    assert sent == {
        "firstName": "Jane",
        "lastName": "Doe",
        "phoneNumber": "+12125550123",
        "registrationDetails-candidate-match-checkbox": False,
        "registrationDetails-sms-checkbox": False,
        "registrationDetails-privacy-checkbox": True,
    }


def test_a_sign_in_left_at_the_details_step_is_finished_with_the_details(page):
    page.goto(SIGN_IN)
    with pytest.raises(Refused, match="^sign_up_details: .*--details"):
        UKG().sign_in(page, "jane@example.com", "pw")
    page.goto(SIGN_IN)
    details = SignUpDetails(name="Jane Doe", phone="+12125550123")
    UKG().finish_sign_up(page, "jane@example.com", "pw", details)
    sent = json.loads(unquote(urlsplit(page.url).fragment))
    assert (sent["firstName"], sent["phoneNumber"]) == ("Jane", "+12125550123")


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
    referred = Is("employeereferral", ["Yes"])
    assert list(fields)[list(fields).index("employeereferral") + 1 :][:3] == [
        "ReferralName",
        "ReferralEmail",
        "ReferralPhone",
    ]
    assert all(fields[f"Referral{d}"].shown_when == referred for d in ("Name", "Email", "Phone"))
    assert fields["ReferralName"].required and not fields["ReferralEmail"].required
    assert fields["ReferralName"].max_length == 100
    assert fields["AvailableStartDate"].type is FieldType.DATE
    assert fields["AvailableStartDate"].default == "2026-10-25"
    assert fields["MultipleChoiceResponse0"].label == "Are you 18 years of age or older?"
    assert fields["MultipleChoiceResponse0"].required
    assert fields["TextResponse1"].label == "Why Acme?" and fields["TextResponse1"].required
    assert not fields["NumericResponse2"].required
    assert "InModal" not in fields
    decline = "I decline to say"
    assert fields["Gender"].options == ["Male", "Female", decline] and fields["Gender"].required
    assert not any("decline" in f for f in fields)
    race = fields["EthnicOrigin"]
    assert race.shown_when == Is("HispanicOrigin", ["Not Hispanic/Latino"])
    assert race.options == ["White", "Asian", decline]
    assert list(fields).index("EthnicOrigin") == list(fields).index("HispanicOrigin") + 1


def test_a_read_only_contact_panel_lists_no_state(page):
    """With the address optional, the contact information is the account's, its Country and
    State in a closed edit dialog."""
    adapter = UKG()
    adapter.account_scope(POSTING)
    page.goto(APPLY)
    page.evaluate("document.getElementById('Country').closest('.form-group').hidden = true")
    fields = {f.id: f for f in adapter.browser_read(page)}
    assert "Country" not in fields and "State" not in fields


def test_race_counts_as_shown_only_once_the_ethnic_origin_shows_it(page):
    adapter, fields = opened(page)
    assert "EthnicOrigin" in fields
    assert "EthnicOrigin" not in adapter.shown_fields(page)
    adapter.fill(page, {"HispanicOrigin": "Not Hispanic/Latino"})
    assert "EthnicOrigin" in adapter.shown_fields(page)


def test_the_referrers_details_count_as_shown_once_a_referral_shows_them(page):
    adapter, _ = opened(page)
    assert "ReferralName" not in adapter.shown_fields(page)
    adapter.fill(page, {"employeereferral": "Yes", "ReferralName": "Jane Doe"})
    assert {"ReferralName", "ReferralEmail", "ReferralPhone"} <= set(adapter.shown_fields(page))
    assert adapter.read_back(page)["ReferralName"] == "Jane Doe"


def test_an_uploaded_documents_own_box_is_no_field(page, resume):
    adapter, fields = opened(page)
    adapter.fill(page, {"documents": resume})
    assert set(adapter.shown_fields(page)) <= set(fields)


def test_the_state_counts_as_shown_once_a_country_shows_it(page):
    adapter, fields = opened(page)
    assert "State" in fields
    assert "State" not in adapter.shown_fields(page)
    adapter.fill(page, {"Country": "United States"})
    assert "State" in adapter.shown_fields(page)


def test_a_question_the_read_did_not_list_counts_as_shown(page):
    adapter, fields = opened(page)
    page.locator(FORM).evaluate(
        "f => f.insertAdjacentHTML('beforeend', '<input id=TextResponse9>')"
    )
    assert set(adapter.shown_fields(page)) - set(fields) == {"TextResponse9"}


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
        "NumericResponse2": "50000",  # shown 50,000
        "Gender": "I decline to say",
        "HispanicOrigin": "Not Hispanic/Latino",
        "EthnicOrigin": "Asian",
    }
    page.locator("#Gender").select_option(label="Male")
    adapter.fill(page, answers)
    assert page.locator("#NumericResponse2").input_value() == "50,000"
    assert page.locator("#Gender").input_value() == ""  # the decline box cleared it
    shown = adapter.read_back(page)
    assert shown == {**answers, "documents": "CV Ada.pdf", "FirstName": "Ada"}
    adapter.fill(page, {"Gender": "Female", "HispanicOrigin": "I decline to say"})
    shown = adapter.read_back(page)
    assert (shown["Gender"], shown["HispanicOrigin"]) == ("Female", "I decline to say")
    assert "EthnicOrigin" not in shown
