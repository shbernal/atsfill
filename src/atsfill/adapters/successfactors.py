"""SAP SuccessFactors Recruiting (`<career site>[/<subsite>]/job/<slug>/<id>/`), as
docs/adapters/successfactors.md observes.

A posting is on the employer's career site; its apply link leads to the candidate pages on a
SuccessFactors data center host, where applying needs an account, one per employer: the
account scope is the career site's host. The application is one page of sections; the
fields are read from it, each by its input's name, else its label.
"""

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from atsfill.core.adapter import (
    Blocked,
    Gap,
    NotTaken,
    Refused,
    SignUpDetails,
    Unsupported,
    known_gap,
)
from atsfill.core.browser import Locator, Page, PageRequest, Request, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

JOB = re.compile(r"^(?:/[^/]+)?/job/[^/]+/(?P<id>\d{5,})/?$")
DC_HOST = re.compile(r"^career\d*\.(?:sapsf|successfactors)\.(?:eu|com|cn)$")
APPLY = "a[href*='/talentcommunity/apply/']"
COMPANY_COOKIE = "careerSiteCompanyId"
PROFILE = "/career?company={company}&career_ns=job_listing_summary&navBarLevel=MY_PROFILE"
JOB_REQ = "career_job_req_id"  # the job the apply link posts, the candidate pages' own id
APPLICATION = "/career?company={company}&career_ns=job_application&career_job_req_id={req}"
GUIDED = "body.cxApplyEnabled"  # a site whose application is a step-by-step one, not sections
HOME = "body.careerUserLoggedIn"  # any candidate page signed in, the candidate home's too
# The body's class for an account; a signed-out page also holds a log-out link and its label
SIGNED_IN = re.compile(r"<body[^>]*\bclass=[\"'][^\"']*\bcareerUserLoggedIn\b")
FIELDS = (Path(__file__).with_suffix(".js")).read_text()
# What the candidate pages show once loaded
SIGN_IN = "#username"
COUNTRY = "fbclc_country"  # its options' values are ISO 3166 alpha-2 codes
PHONE, CALLING_CODE = "#fbclc_phoneNumber", "#fbclc_ituCode"  # `CA`: `Canada (+1)`
SIGN_UP = "#fbclc_userName"
PASSCODE = "#passcode"  # an OTP some sign-ups ask before the account exists
QUICK_APPLY = "#fbqa_apply"
FORM = ".RCMFormField"
LANDED = f"{SIGN_IN}, {SIGN_UP}, {FORM}"
REGISTER = "a[href*='login_ns=register']"
SIGN_IN_LINK = "a[href*='login_ns=login'], a[href*='career_ns=login']"
SIGN_IN_MODAL = "a[onclick*=openSignInModal]"  # a Quick Apply page's
# An error a sign-up shows; the password rules beside the field show whatever is typed
REFUSAL = re.compile(r"required|exist|already|invalid|bereits|erforderlich|ungültig", re.I)
ACTIVATE = re.compile(r"activat|verify your|verification e-?mail|bestätig", re.I)
PRIVACY = "#dataPrivacyId"
VISIBILITY = "input[name=fbclc_searPref]"  # who may see the profile
APPLIED_ONLY = "2"  # its option for only the recruiters of the jobs applied to
EXPAND = "[id$=_expandAllSections]"
SUBMIT = "[id$=_submitBtn]"
OVERLAY = "#sfOverlayMgr"  # a confirmation dialog
ERROR = "[role=alert]:visible"  # a validation message area, shown
LANDING_TIMEOUT_MS = 45_000
SIGN_IN_TIMEOUT_MS = 30_000
LIST_TIMEOUT_MS = 5_000
OPEN_TRIES = 3
UPLOAD_TIMEOUT_S = 30
POLL_MS = 500
SETTLE_MS = 1_000
SUBMIT_TIMEOUT_MS = 60_000
NO_SELECTION = "No Selection"
YES_NO = ["Yes", "No"]
TYPES = {
    "text": FieldType.TEXT,
    "date": FieldType.DATE,
    "picklist": FieldType.CHOICE,
    "select": FieldType.CHOICE,
    "radio": FieldType.CHOICE,
    "checkbox": FieldType.CHOICE,
    "file": FieldType.FILE,
}


def _id(element_id: str) -> str:
    """A CSS selector for a juic id, which holds a colon."""
    return f'[id="{element_id}"]'


def _company(request: Request) -> list[tuple[str, str]]:
    """The data center hosts and company ids a session's cookies are for."""
    return sorted(
        {
            (c["domain"].lstrip("."), c["value"])
            for c in request.storage_state()["cookies"]
            if c["name"] == COMPANY_COOKIE and DC_HOST.match(c["domain"].lstrip("."))
        }
    )


def _posts_job(request: PageRequest) -> bool:
    """Whether the apply link's request posts a job to the candidate pages."""
    return (
        request.method == "POST"
        and bool(DC_HOST.match(urlsplit(request.url).hostname or ""))
        and JOB_REQ in (request.post_data or "")
    )


def _calling_code(select: Locator, details: SignUpDetails) -> tuple[str, str]:
    """The calling code's option for the E.164 phone, the one of `country` among the countries
    sharing it, and the national number that follows the code."""
    options = select.evaluate("s => [...s.options].map(o => [o.value, o.textContent])")
    phone = (details.phone or "").removeprefix("+")
    codes = [
        (len(m[1]), value == details.country, value)
        for value, text in options
        if (m := re.search(r"\(\+(\d+)\)", text)) and phone.startswith(m[1])
    ]
    if not codes:
        raise Unsupported(f"unhandled_rendering: the sign-up offers no calling code for {phone}")
    length, _, value = max(codes, key=lambda c: c[:2])  # the first of the best
    return value, phone[length:]


class SuccessFactors:
    name = "successfactors"
    per_employer = True
    asks = {"name"}
    known_gaps = {
        "quick_apply_sign_up": Gap.FLOW,
        "guided_application": Gap.FLOW,
    }
    side_effects = [
        SideEffect("sign_up", "employer", "the account is created in the employer's tenant"),
        SideEffect("fill", "hiring_system", "an attachment is uploaded to the account's profile"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._postings: dict[str, str] = {}  # by career site host, the last posting's URL
        self._fields: dict[str, dict[str, Any]] = {}
        self._uploads: dict[str, str] = {}  # by field, the name of the file uploaded to it

    def detect(self, url: str, page: Page | None) -> bool:
        if not JOB.match(urlsplit(url).path):
            return False
        return page is None or page.locator(APPLY).count() > 0

    def posting(self, url: str) -> str:
        parts = urlsplit(url)
        job = JOB.match(parts.path)
        return f"https://{parts.hostname}/job/{job['id']}" if job else url

    def account_scope(self, url: str) -> str | None:
        host = (urlsplit(url).hostname or "").lower()
        if JOB.match(urlsplit(url).path):
            self._postings[host] = url
        return host

    def login_url(self, scope: str | None) -> str:
        return self._postings.get(scope or "", f"https://{scope}/")

    def signed_in(self, request: Request, scope: str | None) -> bool:
        """The profile page of the session's company marks its body for an account."""
        for host, company in _company(request):
            response = request.get(f"https://{host}{PROFILE.format(company=company)}")
            if response.ok and SIGNED_IN.search(response.text()):
                return True
        return False

    def sign_in(self, page: Page, email: str, password: str) -> None:
        self._candidate(page)
        if not page.locator(SIGN_IN).count():
            page.locator(SIGN_IN_LINK).first.click()
            page.locator(SIGN_IN).wait_for(timeout=LANDING_TIMEOUT_MS)
        page.fill(SIGN_IN, email)
        page.fill("#password", password)
        page.locator("button[onclick*=validateFields]").click()
        try:
            page.locator(FORM).first.wait_for(state="attached", timeout=SIGN_IN_TIMEOUT_MS)
        except Timeout:
            message = self._message(page)
            if ACTIVATE.search(message):
                raise Blocked(f"email_link: {message}") from None
            raise Refused(f"wrong_password: {message}") from None

    def sign_up(self, page: Page, email: str, password: str, details: SignUpDetails) -> None:
        """Creates the account from the career site's sign-up: the name, the country of
        residence from `country` (the list's first without it), the phone where the site
        requires one, its calling code picked from the number, and the profile visible only to
        the recruiters of the jobs applied to where the site asks; a missing phone is refused
        before anything is sent."""
        self._candidate(page)
        if page.locator(SIGN_IN).count():
            page.locator(REGISTER).first.click()
        elif page.locator(QUICK_APPLY).count():  # its sign-up sends the application too
            page.locator(SIGN_IN_MODAL).first.click()
            register = page.locator(f"{REGISTER}:visible")
            try:
                register.first.wait_for(timeout=LIST_TIMEOUT_MS)
            except Timeout:
                raise Unsupported(
                    known_gap(self, "quick_apply_sign_up", "its sign-in offers no sign-up")
                ) from None
            register.first.click()
        page.locator(SIGN_UP).wait_for(timeout=LANDING_TIMEOUT_MS)
        phone_asked = page.locator(f"{PHONE}[aria-required=true]:visible").count() > 0
        if phone_asked and not details.phone:
            raise Refused("sign_up_details: no phone")
        first, _, last = (details.name or "").strip().partition(" ")
        for selector, value in [
            (SIGN_UP, email),
            ("#fbclc_emailConf", email),
            ("#fbclc_pwd", password),
            ("#fbclc_pwdConf", password),
            ("#fbclc_fName", first),
            ("#fbclc_lName", last or first),
        ]:
            page.fill(selector, value)
        if phone_asked and details.phone:
            code, national = _calling_code(page.locator(CALLING_CODE), details)
            page.locator(CALLING_CODE).select_option(code)
            page.fill(PHONE, national)
        for select in page.locator("select:visible").all():  # the country, any other
            if not select.input_value():
                values = select.evaluate("s => [...s.options].map(o => o.value).filter(v => v)")
                wanted = details.country if select.get_attribute("id") == COUNTRY else None
                if wanted and wanted not in values:
                    raise Unsupported(f"unhandled_rendering: the sign-up's countries lack {wanted}")
                select.select_option(wanted or values[0])
        if page.locator(f"{VISIBILITY}:visible").count():
            least = page.locator(f"{VISIBILITY}[value='{APPLIED_ONLY}']")
            if not least.count():
                raise Unsupported("unhandled_rendering: the profile's visibility has no jobs only")
            least.check()
        if page.locator(PRIVACY).count():
            page.locator(PRIVACY).click()
            dialog = page.locator("[role=dialog]:visible").last
            dialog.locator("[id^=dlgButton]").first.click()
        page.locator("#fbclc_createAccountButton").click()
        deadline = time.monotonic() + SIGN_IN_TIMEOUT_MS / 1000
        while time.monotonic() < deadline:
            page.wait_for_timeout(POLL_MS)
            if page.locator(FORM).count():
                return
            if page.locator(f"{PASSCODE}:visible").count():
                raise Blocked("email_otp: the sign-up sent a passcode to the email")
            if not page.locator(f"{SIGN_UP}:visible").count():
                break
            if self._message(page, REFUSAL):
                break
        message = self._message(page, REFUSAL)
        if re.search(r"exist|already|bereits|registered", message, re.IGNORECASE):
            raise Refused(f"email_in_use: {message}")
        if re.search(r"password|kennwort", message, re.IGNORECASE):
            raise Refused(f"password_rule: {message}")
        if message:
            raise Unsupported(f"unhandled_rendering: the sign-up refused: {message}")
        text = page.locator("body").inner_text()
        if ACTIVATE.search(text):
            raise Blocked("email_link: the sign-up sent an activation email")
        if page.locator(f"{SIGN_IN}:visible").count():  # created, signed out
            self.sign_in(page, email, password)
            return
        if self.signed_in(page.request, None):  # created, back on the career site
            return
        raise Unsupported(f"unhandled_rendering: the sign-up led to {urlsplit(page.url).path}")

    def browser_read(self, page: Page) -> list[Field]:
        self._candidate(page)
        if not page.locator(FORM).count():
            if page.locator(f"{SIGN_IN}, {SIGN_UP}").count():
                raise Blocked("login_wall: the application asks to sign in")
            raise Unsupported(f"unhandled_rendering: no application form: {self._message(page)}")
        expand = page.locator(EXPAND)
        if expand.count() and expand.first.is_visible():
            expand.first.click()
            page.wait_for_timeout(SETTLE_MS)
        fields = []
        for d in page.evaluate(FIELDS):
            if d["kind"] == "picklist":  # a disabled one waits for its parent: no items yet
                d["options"] = [] if d["disabled"] else self._options(page, d)
            self._fields[d["key"]] = d
            fields.append(self._field(d))
        return fields

    def _field(self, d: dict[str, Any]) -> Field:
        kind = d["kind"]
        options = d["options"]
        if kind == "checkbox":
            options = YES_NO
        if kind == "picklist" and not options:  # a cascading list, filled once its parent is
            return Field(
                d["key"],
                d["label"],
                FieldType.TYPEAHEAD,
                d["required"],
                prefilled=d["value"] or None,
            )
        unfillable = None
        if kind == "unknown":
            unfillable = f"unhandled_rendering: {d['key']} is a control atsfill does not know"
        return Field(
            d["key"],
            d["label"],
            TYPES.get(kind, FieldType.TEXT),
            d["required"],
            options=options if TYPES.get(kind) == FieldType.CHOICE else None,
            max_length=d.get("max"),
            prefilled=(d["value"] or None) if kind not in ("checkbox", "radio") else None,
            default=d["value"] if kind in ("checkbox", "radio") and d["value"] else None,
            unfillable=unfillable,
        )

    def _options(self, page: Page, d: dict[str, Any]) -> list[str]:
        """A picklist's items, read by opening it."""
        items = self._open(page, d)
        options = [t for t in items.evaluate_all("as => as.map(a => a.title.trim())") if t]
        self._close(page, d)
        return [o for o in options if o not in (NO_SELECTION, d["placeholder"])]

    def _open(self, page: Page, d: dict[str, Any]) -> Locator:
        """Opens a picklist; a click can go unheard, or close a list another left open."""
        button = page.locator(_id(d["id"])).locator("xpath=following-sibling::button")
        items = page.locator(f"{_id(d['owns'])} li[role=option] a")
        for _ in range(OPEN_TRIES):
            button.click()
            try:
                items.first.wait_for(timeout=LIST_TIMEOUT_MS)
                return items
            except Timeout:
                continue
        return items

    def _close(self, page: Page, d: dict[str, Any]) -> None:
        if page.locator(f"{_id(d['owns'])}:visible").count():
            page.keyboard.press("Escape")
        if page.locator(f"{_id(d['owns'])}:visible").count():
            page.locator(_id(d["id"])).locator("xpath=following-sibling::button").click()

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        """Fills in the page's order, a country before its states. A widget re-renders with new
        ids as it changes, so each field is looked up again before it is filled."""
        if not self._fields:
            self.browser_read(page)
        for key in [k for k in self._fields if k in answers]:
            d = self._current(page, key)
            answer = answers[key]
            value = answer if isinstance(answer, str) else ", ".join(answer)
            kind = d["kind"]
            if kind == "file":
                self._uploads[key] = Path(value).name
            if value == d["value"] or self._uploads.get(key) in d.get("files", []):
                continue
            if d["id"] is None and kind != "radio":
                raise NotTaken(f"{key} shows no control to fill")
            if kind in ("text", "date"):
                target = page.locator(_id(d["id"]))
                target.fill(value)
                target.press("Tab")
            elif kind == "picklist":
                self._pick(page, d, value)
            elif kind == "select":
                page.locator(_id(d["id"])).select_option(label=value)
            elif kind == "radio":
                page.locator(_id(d["ids"][d["options"].index(value)])).check()
            elif kind == "checkbox":
                box = page.locator(_id(d["id"]))
                if box.is_checked() != (value == "Yes"):
                    box.click()
            elif kind == "file":
                self._upload(page, d, value)

    def _pick(self, page: Page, d: dict[str, Any], value: str) -> None:
        if d["disabled"]:
            raise NotTaken(f"{d['key']} stays disabled: its parent's answer offers no items")
        items = self._open(page, d)
        titles = [t.strip() for t in items.evaluate_all("as => as.map(a => a.title)")]
        wanted = next((i for i, t in enumerate(titles) if t == value), None)
        if wanted is None:
            folded = [t.casefold() for t in titles]
            wanted = next((i for i, t in enumerate(folded) if t.startswith(value.casefold())), None)
        if wanted is None:
            self._close(page, d)
            raise NotTaken(f"{d['key']} lists no {value!r}")
        items.nth(wanted).click()
        page.wait_for_timeout(SETTLE_MS)

    def _upload(self, page: Page, d: dict[str, Any], path: str) -> None:
        """The attachment's button opens a callout of sources, the device's a file input that
        uploads once set."""
        if self._current(page, d["key"])["multiple"]:  # it holds a file: deleted first
            page.locator(_id(self._current(page, d["key"])["id"])).click()
            confirm = page.locator(f"{OVERLAY} button.globalPrimaryButton:visible")
            confirm.first.wait_for(timeout=LIST_TIMEOUT_MS)
            confirm.first.click()
            deadline = time.monotonic() + UPLOAD_TIMEOUT_S
            while (now := self._current(page, d["key"]))["multiple"] or now["id"] is None:
                if time.monotonic() > deadline:  # deleting, it shows neither button
                    raise NotTaken(f"{d['key']} kept its file")
                page.wait_for_timeout(POLL_MS)
        source = page.locator("[role=dialog]:visible input[type=file]")
        for _ in range(OPEN_TRIES):  # a first click can only close a list left open
            page.locator(_id(self._current(page, d["key"])["id"])).click()
            try:
                source.last.wait_for(state="attached", timeout=LIST_TIMEOUT_MS)
                break
            except Timeout:
                continue
        source.last.set_input_files(path)
        deadline = time.monotonic() + UPLOAD_TIMEOUT_S
        while Path(path).name not in self._current(page, d["key"])["files"]:
            if time.monotonic() > deadline:
                raise NotTaken(f"{d['key']} did not show {Path(path).name} once uploaded")
            page.wait_for_timeout(POLL_MS)

    def _current(self, page: Page, key: str) -> dict[str, Any]:
        found = next((d for d in page.evaluate(FIELDS) if d["key"] == key), None)
        if found is None:
            raise NotTaken(f"{key} is no longer on the page")
        return found | {"options": self._fields[key]["options"]}

    def read_back(self, page: Page) -> dict[str, Answer]:
        """Each field's value; an attachment's, the file uploaded to it when it lists several."""
        values = {}
        for d in page.evaluate(FIELDS):
            uploaded = self._uploads.get(d["key"])
            value = uploaded if uploaded in d.get("files", []) else d["value"]
            if value:
                values[d["key"]] = value
        return values

    def submit(self, page: Page) -> str | None:
        page.locator(SUBMIT).first.click()
        page.wait_for_load_state(timeout=SUBMIT_TIMEOUT_MS)
        if message := self._message(page):
            raise Blocked(f"not_submitted: {message}")
        return page.url

    def _candidate(self, page: Page) -> None:
        """From the posting, follows its apply link to the candidate pages. The link posts the
        job there; signed in, some sites drop it and land on the candidate home, so the
        application is opened from the job the link posted."""
        host = urlsplit(page.url).hostname or ""
        posted: list[PageRequest] = []
        if not DC_HOST.match(host):
            apply = page.locator(APPLY)
            if not apply.count():
                raise Unsupported("posting_gone: the posting has no apply link")

            def keep(request: PageRequest) -> None:
                if _posts_job(request):
                    posted.append(request)

            page.on("request", keep)
            apply.first.evaluate("a => a.click()")  # some sites open a dialog on a real click
        try:
            page.locator(f"{LANDED}, {HOME}").first.wait_for(
                state="attached", timeout=LANDING_TIMEOUT_MS
            )
            page.wait_for_load_state(timeout=LANDING_TIMEOUT_MS)
            if posted and JOB_REQ not in page.url and not page.locator(LANDED).count():
                post = posted[0]
                parts = urlsplit(post.url)
                job = parse_qs(parts.query) | parse_qs(post.post_data or "")
                application = APPLICATION.format(company=job["company"][0], req=job[JOB_REQ][0])
                page.goto(f"https://{parts.hostname}{application}")
            page.locator(LANDED).first.wait_for(state="attached", timeout=LANDING_TIMEOUT_MS)
        except Timeout:
            if page.locator(GUIDED).count():
                raise Unsupported(
                    known_gap(self, "guided_application", "the application is a step-by-step one")
                ) from None
            raise Unsupported(
                f"unhandled_rendering: the apply link led to {urlsplit(page.url).path}"
            ) from None
        page.wait_for_timeout(SETTLE_MS)

    def _message(self, page: Page, only: re.Pattern | None = None) -> str:
        """The errors the page shows, those matching `only` when given."""
        texts = [" ".join(t.split()) for t in page.locator(ERROR).all_inner_texts()]
        return " ".join(t for t in texts if t and (only is None or only.search(t)))[:300]
