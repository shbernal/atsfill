"""The SuccessFactors adapter on tests/pages/successfactors, served as an Acme career site and
its candidate pages on a data center host."""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from atsfill.adapters.successfactors import SuccessFactors
from atsfill.core import browser
from atsfill.core.adapter import Refused, SignUpDetails
from atsfill.core.results import Answer, FieldType
from tests import testuser

PAGES = Path(__file__).parent / "pages/successfactors"
POSTING = "https://careers.acme.com/job/Detroit-Portfolio-Manager-MI-48201/1234567/"
DC = "https://career5.successfactors.eu"
PROFILE = f"{DC}/career?company=acme&career_ns=job_listing_summary&navBarLevel=MY_PROFILE"
SESSION = {"signed_in": False}  # the candidate pages' session, kept here by the fixture


def serve(route) -> None:
    url = route.request.url
    if "/job/" in url:
        route.fulfill(path=PAGES / "posting.html")
    elif "login_ns=register" in url:
        route.fulfill(path=PAGES / "signup.html")
    elif "/career?company=acme" in url:
        route.fulfill(content_type="text/html", body="<p>Career site</p>")
    elif "/careers?company=acme" in url:
        route.fulfill(
            path=PAGES / ("apply.html" if SESSION["signed_in"] else "signin.html"),
            headers={"Set-Cookie": "careerSiteCompanyId=acme; Path=/", "Content-Type": "text/html"},
        )
    else:
        route.fulfill(path=PAGES / "apply.html")


@pytest.fixture
def page(tmp_path):
    SESSION["signed_in"] = False
    with browser.open_page(tmp_path / "data", seconds=300) as page:
        page.context.route("https://careers.acme.com/**", serve)
        page.context.route(f"{DC}/**", serve)
        yield page


def signed_in(page) -> None:
    SESSION["signed_in"] = True


class Profile:
    """A request with a session's cookies, answering the profile page signed in or not."""

    def __init__(self, cookies: list[dict], signed_in: bool) -> None:
        self.cookies, self.signed_in, self.asked = cookies, signed_in, []

    def storage_state(self) -> dict:
        return {"cookies": self.cookies}

    def get(self, url: str):
        self.asked.append(url)
        body = '<a onclick="signOut()">Sign Out</a>' if self.signed_in else '<input id="username">'
        return type("Response", (), {"ok": True, "text": lambda _: body})()


COMPANY = {"name": "careerSiteCompanyId", "value": "acme", "domain": "career5.successfactors.eu"}
SITE = {"name": "JSESSIONID", "value": "x", "domain": "careers.acme.com"}


@pytest.mark.parametrize(
    ("cookies", "profile", "expected"),
    [([COMPANY, SITE], True, True), ([COMPANY], False, False), ([SITE], True, False)],
)
def test_signed_in(cookies, profile, expected):
    request = Profile(cookies, profile)
    assert SuccessFactors().signed_in(request, "careers.acme.com") is expected  # ty: ignore[invalid-argument-type]
    assert request.asked == ([PROFILE] if COMPANY in cookies else [])


def test_detect_and_posting():
    adapter = SuccessFactors()
    assert adapter.detect(POSTING, None)
    assert adapter.detect("https://jobs.acme.com/Acme-Austria/job/Wien-Clerk/1390197733/", None)
    assert not adapter.detect("https://careers.acme.com/search/?q=clerk", None)
    assert adapter.posting(POSTING + "?utm_source=x") == "https://careers.acme.com/job/1234567"
    assert adapter.account_scope(POSTING) == "careers.acme.com"
    assert adapter.login_url("careers.acme.com") == POSTING


ADA = SignUpDetails(name="Ada Placeholder", phone="+12125550123", country="US")


def test_sign_up(page):
    page.goto(POSTING)
    SuccessFactors().sign_up(page, "ada@example.com", "Aa1!aaaaaaaaaaaa", ADA)
    assert page.url.startswith(f"{DC}/portalcareer")
    sent = parse_qs(urlsplit(page.url).query)
    assert sent["fbclc_ituCode"] == ["US"]  # of the +1 countries, the one the details name
    assert sent["fbclc_phoneNumber"] == ["2125550123"]
    assert sent["fbclc_country"] == ["US"]


def test_sign_up_back_on_the_career_site_signed_in(page, monkeypatch):
    adapter = SuccessFactors()
    monkeypatch.setattr(adapter, "signed_in", lambda request, scope: True)  # its profile page
    page.goto(POSTING)
    adapter.sign_up(page, "back@example.com", "Aa1!aaaaaaaaaaaa", ADA)
    assert urlsplit(page.url).path == "/career"


def test_sign_up_without_the_phone_the_site_requires_is_refused(page):
    page.goto(POSTING)
    with pytest.raises(Refused, match="^sign_up_details: no phone$"):
        SuccessFactors().sign_up(
            page, "ada@example.com", "Aa1!aaaaaaaaaaaa", SignUpDetails(name="Ada Placeholder")
        )


def test_sign_up_refused(page):
    page.goto(POSTING)
    with pytest.raises(Refused, match="email_in_use"):
        SuccessFactors().sign_up(page, "taken@example.com", "Aa1!aaaaaaaaaaaa", ADA)


def test_sign_in(page):
    page.goto(POSTING)
    SuccessFactors().sign_in(page, "ada@example.com", "Aa1!aaaaaaaaaaaa")
    assert page.url.startswith(f"{DC}/portalcareer")


def test_wrong_password(page, monkeypatch):
    monkeypatch.setattr("atsfill.adapters.successfactors.SIGN_IN_TIMEOUT_MS", 2_000)
    page.goto(POSTING)
    with pytest.raises(Refused, match="wrong_password: Invalid username or password"):
        SuccessFactors().sign_in(page, "ada@example.com", "wrong")


def test_browser_read(page):
    signed_in(page)
    page.goto(POSTING)
    fields = {f.id: f for f in SuccessFactors().browser_read(page)}
    assert list(fields) == [
        "resume",
        "cover-letter",
        "firstName",
        "cellPhone",
        "country",
        "state",
        "have-you-previously-worked-for-acme",
        "custSalary",
        "gender",
        "notice-period",
        "i-agree-to-be-contacted-about-other-jobs",
    ]  # an entry of a list section is not read
    assert fields["resume"].type is FieldType.FILE and fields["resume"].required
    assert not fields["cover-letter"].required
    assert fields["firstName"].prefilled == "Ada"
    assert fields["firstName"].max_length == 1024
    assert fields["country"].options == ["Argentina", "United States"]
    assert fields["country"].prefilled == "Argentina"
    assert fields["state"].type is FieldType.TYPEAHEAD  # its items follow the country
    assert fields["have-you-previously-worked-for-acme"].options == ["No", "Yes"]
    assert fields["custSalary"].type is FieldType.TEXT and fields["custSalary"].required
    assert fields["gender"].options == ["Female", "Male", "Not specified"]
    assert fields["gender"].default == "Not specified"
    assert fields["notice-period"].options == ["One month", "Three months"]
    assert fields["i-agree-to-be-contacted-about-other-jobs"].options == ["Yes", "No"]
    assert not any(f.unfillable for f in fields.values())


def test_fill_and_read_back(page, tmp_path):
    signed_in(page)
    page.goto(POSTING)
    adapter = SuccessFactors()
    adapter.browser_read(page)
    resume = str(testuser.resume(tmp_path / "resume.pdf"))
    letter = str(testuser.resume(tmp_path / "letter.pdf"))
    answers: dict[str, Answer] = {
        "resume": resume,
        "cover-letter": letter,  # replaces the file the profile holds
        "cellPhone": "+12025550143",
        "country": "United States",
        "state": "District of Columbia",
        "have-you-previously-worked-for-acme": "No",
        "custSalary": "50000",
        "gender": "Female",
        "notice-period": "One month",
        "i-agree-to-be-contacted-about-other-jobs": "Yes",
    }
    adapter.fill(page, answers)
    back = adapter.read_back(page)
    files = {"resume": "resume.pdf", "cover-letter": "letter.pdf"}
    assert back == answers | files | {"firstName": "Ada"}
