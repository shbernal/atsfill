"""Workday (`<tenant>.wd<n>.myworkdayjobs.com/<site>/job/…` and
`wd<n>.myworkdaysite.com/recruiting/<tenant>/<site>/job/…`), as docs/adapters/workday.md
observes.

Applying needs an account, one per tenant: the account scope. The form is a wizard of pages,
each saved to a draft application as it is left; only the current page is in the DOM. The
read walks the pages that save with what they hold, the posting's questionnaires and apply
flow standing for the pages it does not reach; a page behind a consent's tick only a fill
reaches, on the user's answer; the read-back is each page's values, read before it is saved.
"""

import re
import time
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import matching
from atsfill.core.adapter import (
    Blocked,
    Gap,
    NotTaken,
    Refused,
    SignUpDetails,
    Unsupported,
    known_gap,
)
from atsfill.core.browser import Locator, Page, PageRequest, Request, Response, Timeout
from atsfill.core.results import Answer, AnyOf, Condition, Field, FieldType, Is, SideEffect

JOBS_HOST = re.compile(r"^(?P<tenant>[a-z0-9-]+)\.(?P<dc>wd\d+)\.myworkdayjobs\.com$")
SITE_HOST = re.compile(r"^(?P<dc>wd\d+)\.myworkdaysite\.com$")
LOCALE = r"(?:/[a-z]{2}-[A-Z]{2})?"
JOBS_PATH = re.compile(LOCALE + r"/(?P<site>[^/]+)(?P<rest>/.*)?$")
SITE_PATH = re.compile(LOCALE + r"/recruiting/(?P<tenant>[^/]+)/(?P<site>[^/]+)(?P<rest>/.*)?$")
JOB = re.compile(r"^/(?:job/[^/]+|details)/(?P<slug>[^/?#]+)")
SIGN_IN_TIMEOUT_S = 30
SIGN_IN_POLL_MS = 500
PRESS_TIMEOUT_MS = 5_000
PRESSES = 3
FORM_TIMEOUT_MS = 30_000
STEP_TIMEOUT_MS = 30_000
MENU_TIMEOUT_MS = 5_000
UPLOAD_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
STEP_POLL_MS = 300
SETTLE_MS = 1_000
SETTLED = 3  # counts in a row the same
FOLLOW_UP_ROUNDS = 4  # follow-ups of follow-ups
RENDER_TRIES = 5
MAX_PAGES = 10
PROMPT_LEVELS = 3  # Escapes to close a list
MAX_PROMPT_OPTIONS = 40  # a longer list (the country phone codes) is read from the API
NEXT = "[data-automation-id=pageFooterNextButton]"
APPLIED = "[data-automation-id=alreadyApplied]"
FIELD = '[data-automation-id^="formField-"]'
STEP = "[data-automation-id=progressBarActiveStep]"
ERRORS = "[data-automation-id=errorHeading], [data-automation-id=inputAlert]"
UPLOADED = "[data-automation-id=file-upload-item-name]"
DELETE_FILE = "[data-automation-id=delete-file]"
PROMPT_OPTION = "[data-automation-id=activeListContainer] [data-automation-id=promptOption]:visible"
PICKED = "[data-automation-id=selectedItem]"
DELETE_PICK = "[data-automation-id=DELETE_charm]"
SEARCH = "input[data-uxi-widget-type=selectinput]"
BACK = "[data-automation-id=backButton]:visible"
ADDRESS = "address--"
PHONE_CODE_ID = "phoneNumber--countryPhoneCode"
PHONE_CODE = f'[data-fkit-id="{PHONE_CODE_ID}"]'
PHONE_CODES = "/values/phone/countryCodes"
# An answer to a question posts the questionnaire's definition; the page renders its reply
# over the answers given meanwhile
DEFINITION = "/definition?"
# A field whose value before any answer is the site's, not the account's: the country is the
# browser's locale's, and decides the address fields read
SITE_DEFAULTS = {"country--country", PHONE_CODE_ID}
PHONE_NUMBER = "phoneNumber--phoneNumber"
RESUME_ID = "resumeAttachments--attachments"
REVIEW = "Review"
MY_INFORMATION = "My Information"
EXPERIENCE = "My Experience"
# Each questionnaire's page: `Application Questions`, or `… 1 of 2` and `… 2 of 2`
QUESTIONNAIRES = [
    ("primary", "questionnaireId", "Application Questions"),
    ("secondary", "secondaryQuestionnaireId", "Application Questions 2 of 2"),
]
SELECT_ONE = "Select One"
YES_NO = ["Yes", "No"]
TAG = re.compile(r"<[^>]+>")
RESUME = re.compile(r"resume|\bcv\b", re.I)
SKILLS = re.compile(r"skills\b", re.I)
SKILLS_ID = "skills--skills"
# The lists atsfill fills the first entry of: each part's id, label, rendering and the apply
# flow's section that shows it (None: always shown, required with the list)
type EntryList = tuple[str, re.Pattern[str], list[tuple[str, str, str, str | None]]]
ENTRY_LISTS: list[EntryList] = [
    ("workExperience", re.compile(r"work (experience|history)", re.I), [
        ("jobTitle", "Job Title", "text", None),
        ("companyName", "Company", "text", None),
        ("location", "Location", "text", "Location"),
        ("currentlyWorkHere", "I currently work here", "box", None),
        ("startDate", "From", "month", None),
        ("endDate", "To", "month", None),
        ("roleDescription", "Role Description", "text", "Role Description"),
    ]),
    ("education", re.compile(r"education", re.I), [
        ("school", "School or University", "school", None),
        ("degree", "Degree", "degrees", None),
        ("fieldOfStudy", "Field of Study", "fieldsOfStudy", "Field of Study"),
        ("gradeAverage", "Overall Result (GPA)", "text", "Overall Result (GPA)"),
        ("firstYearAttended", "From", "year", "From"),
        ("lastYearAttended", "To (Actual or Expected)", "year", "To (Actual or Expected)"),
    ]),
]  # fmt: skip
# The school: a search prompt where the tenant lists schools, else a text input; one field
SCHOOL_IDS = {"education--schoolName": "education--school"}
BRACKETED = re.compile(r"\(([^()]+)\)")
ENTRY_ID = re.compile(r"^(workExperience|education)-(\d+)--")
ADD = "[data-automation-id=add-button]"
ENTRIES = re.compile(r"(work|education|skill|website|language|certification|portfolio)", re.I)
ENTRY_PART = re.compile(r"number|date|attachments", re.I)  # a field of an entry, not a list
QUESTION_TYPES = {  # by a question's type's descriptor
    "Multiple Choice - Single Select": FieldType.CHOICE,
    "Multiple Choice - Multi Select": FieldType.MULTI_CHOICE,
    "Text": FieldType.TEXT,
    "Number": FieldType.TEXT,
    "Date": FieldType.DATE,
    "Upload Attachments": FieldType.FILE,
}
KINDS = {
    "text": FieldType.TEXT,
    "list": FieldType.CHOICE,
    "radio": FieldType.CHOICE,
    "box": FieldType.CHOICE,
    "boxes": FieldType.MULTI_CHOICE,
    "prompt": FieldType.TYPEAHEAD,
    "date": FieldType.DATE,
    "month": FieldType.TEXT,
    "year": FieldType.TEXT,
    "file": FieldType.FILE,
}
EMAIL = "[data-automation-id=email]"
WITH_EMAIL = "[data-automation-id=SignInWithEmailButton]"  # beside Google, LinkedIn…
# What the sign-in and sign-up pages say, by the reason it is
MESSAGES = [
    (re.compile(r"verify your account", re.I), Blocked, "email_link"),
    (re.compile(r"password must|password requirements", re.I), Refused, "password_rule"),
    (re.compile(r"wrong email address or password|account might be locked", re.I), Refused,
     "wrong_password"),
]  # fmt: skip


@dataclass(frozen=True)
class Site:
    """Where a tenant's career site is served: its host, the path before the site, the site."""

    tenant: str
    host: str
    prefix: str  # "" on myworkdayjobs.com, `/recruiting/<tenant>` on myworkdaysite.com
    site: str
    rest: str  # the path after the site: `/job/<location>/<slug>`, `/details/<slug>`…

    @property
    def dc(self) -> str:
        """The data centre, `wd<n>`."""
        return next(label for label in self.host.split(".") if re.fullmatch(r"wd\d+", label))

    @property
    def base(self) -> str:
        return f"https://{self.host}{self.prefix}/{self.site}"


def site(url: str) -> Site | None:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if (jobs := JOBS_HOST.match(host)) and (path := JOBS_PATH.match(parts.path)):
        return Site(jobs["tenant"], host, "", path["site"], path["rest"] or "")
    if SITE_HOST.match(host) and (path := SITE_PATH.match(parts.path)):
        prefix = f"/recruiting/{path['tenant']}"
        return Site(path["tenant"], host, prefix, path["site"], path["rest"] or "")
    return None


def _profile_hosts(request: Request, scope: str | None) -> list[str]:
    """The Workday hosts a request's cookies are for: the tenant's own, or a shared
    myworkdaysite.com host."""
    hosts = {c["domain"].lstrip(".") for c in request.storage_state()["cookies"]}
    return sorted(
        h for h in hosts
        if SITE_HOST.match(h) or ((m := JOBS_HOST.match(h)) and m["tenant"] == scope)
    )  # fmt: skip


class Workday:
    name = "workday"
    behind_consent = "the pages after a consent's tick, which the draft does not keep"
    per_employer = True
    asks: set[str] = set()
    known_gaps = {
        "experience_entries": Gap.FLOW,
        "long_wizard": Gap.FLOW,
        "other_country": Gap.FLOW,
    }
    side_effects = [
        SideEffect("sign_up", "employer", "the account is created in the employer's tenant"),
        SideEffect("load", "hiring_system", "opening the wizard creates a draft application"),
        SideEffect(
            "fill", "hiring_system", "each page, the resume too, is saved to the draft when left"
        ),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._sites: dict[str, Site] = {}  # by tenant, the last posting's
        self._fields: dict[str, Field] = {}
        self._listed: set[str] = set()  # the ids the read listed, unfillable ones too
        self._walked: set[str] = set()  # the pages the read reached
        self._walled = False  # no read gets past the page this one stopped on
        self._behind = False  # the fill stopped on a page behind that one, for `revealed`
        self._phone_codes = ""
        self._values: dict[str, Answer] = {}
        self._apply = ""

    def detect(self, url: str, page: Page | None) -> bool:
        found = site(url)
        return found is not None and bool(JOB.match(found.rest))

    def posting(self, url: str) -> str:
        """The job's slug on its tenant's site, whatever the host, locale or location."""
        found = site(url)
        if found is None or not (job := JOB.match(found.rest)):
            return url
        return f"https://{found.tenant}.{found.dc}.myworkdayjobs.com/{found.site}/details/{job['slug']}"

    def account_scope(self, url: str) -> str | None:
        found = site(url)
        if found is None:
            return None
        self._sites[found.tenant] = found
        return found.tenant

    def login_url(self, scope: str | None) -> str:
        """The site's login page; the site is the last posting's for the tenant."""
        if scope not in self._sites:
            raise ValueError(f"no Workday posting seen for the tenant {scope!r}")
        return f"{self._sites[scope].base}/login"

    def signed_in(self, request: Request, scope: str | None) -> bool:
        """The tenant's `userprofile` holds the account's email; `{}` for a visitor."""
        for host in _profile_hosts(request, scope):
            response = request.get(f"https://{host}/wday/cxs/{scope}/x/userprofile")
            if response.ok and response.json().get("emailAddress"):
                return True
        return False

    def sign_in(self, page: Page, email: str, password: str) -> None:
        _email_form(page)
        _submit(page, "signInSubmitButton", email, password)

    def sign_up(self, page: Page, email: str, password: str, details: SignUpDetails) -> None:
        _email_form(page)
        page.locator("[data-automation-id=createAccountLink]").click()
        page.locator("[data-automation-id=verifyPassword]").fill(password)
        consent = page.locator("[data-automation-id=createAccountCheckbox]")
        if consent.count():
            consent.check()
        _submit(page, "createAccountSubmitButton", email, password)

    def browser_read(self, page: Page) -> list[Field]:
        """Walks the wizard as far as its pages save with what they hold, reading each page
        it reaches: from the page, My Experience from the apply flow's sections and then the
        page, and the questions from the questionnaires, which also stand for those pages when
        they are not reached. A page after the first that does not save is read once it does."""
        found = site(page.url)
        if found is None or not (job := JOB.match(found.rest)):
            raise RuntimeError(f"{page.url} is not a Workday posting")
        api = f"https://{found.host}/wday/cxs/{found.tenant}/{found.site}"
        calypso = f"https://{found.host}/wday/calypso/cxs"
        posting = _json(page, f"{api}/job/{job['slug']}").get("jobPostingInfo") or {}
        if not posting.get("posted"):  # `canApply` is false too with a draft in progress
            raise Unsupported("posting_gone: the posting takes no applications")
        flow = _json(
            page,
            f"{calypso}/jobapplication/{found.tenant}/jobpostings/{job['slug']}"
            "/applyflowpages?applyDetail=applyManually",
        )
        values = f"{calypso}/jobapplication/{found.tenant}/values"
        from_api = {EXPERIENCE: _experience(self, page, flow, values)}
        for kind, key, step in QUESTIONNAIRES:
            if questionnaire := posting.get(key):
                url = f"{calypso}/common/{found.tenant}/questionnaire/{questionnaire}"
                from_api[step] = _questions(kind, _json(page, url).get("questions") or [])
        self._apply = f"{page.url.split('?')[0].split('/apply')[0]}/apply/applyManually"
        page.goto(self._apply)
        _wait_for_page(page)
        seen: dict[str, list[Field]] = {}
        self._walked, self._walled, self._behind = set(), False, False
        self._phone_codes = f"{calypso}/jobapplication/{found.tenant}{PHONE_CODES}"
        for _ in range(MAX_PAGES):
            step = _step(page)
            if step == REVIEW:  # repeats the answers, an upload without its input
                break
            self._walked.add(step)
            if not _questions_page(step):
                listed = {f.id for f in from_api.get(step, [])}
                seen[step] = _read(page, self._phone_codes, listed)
            try:
                _save_and_continue(page)
            except NotTaken:
                self._walled = _only_a_tick_missing(_page_fields(page))
                break
        steps = [p["descriptor"] for p in flow.get("data") or []]
        steps += [s for s in seen if s not in steps]
        fields = [
            f for s in steps for f in seen.get(s, []) + from_api.get(_questionnaire_step(s), [])
        ]
        self._fields = {f.id: f for f in fields if not f.unfillable}
        self._listed = {f.id for f in fields}
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        """Fills each page of the wizard from the first and saves it, up to Review, or up to a
        page behind the consent: the next call goes on from there with what `revealed` listed."""
        if not self._behind:
            page.goto(self._apply)
            _wait_for_page(page)
            self._values = {}
        self._behind = False
        pending: set[PageRequest] = set()
        page.on("request", lambda r: pending.add(r) if DEFINITION in r.url else None)
        for done in ("requestfinished", "requestfailed"):
            page.on(done, lambda r: pending.discard(r))
        for _ in range(MAX_PAGES):
            if _step(page) == REVIEW:
                return
            _add_entries(page, answers)
            filled: set[str] = set()
            for _ in range(FOLLOW_UP_ROUNDS):  # an answer shows its follow-up questions
                todo = [r for r in _rendered(page, answers) if r["id"] in answers]
                todo = [r for r in todo if r["id"] in self._fields]
                # a reply that came after its wait rendered over the answers given meanwhile
                todo = [r for r in todo if r["id"] not in filled or _lost(r, answers[r["id"]])]
                if not todo:
                    break
                for raw in todo:
                    _fill(page, self._fields[raw["id"]], raw, answers[raw["id"]])
                    filled.add(raw["id"])
                    if raw["kind"] != "text":
                        _until_rendered(page, pending)
                page.wait_for_timeout(SETTLE_MS)
            self._unread(page)
            if self._behind:
                return
            self._values |= {r["id"]: r["value"] for r in _filled(page, answers)}
            if self._values.get(PHONE_NUMBER) and (code := _phone_code(page)):
                self._values[PHONE_NUMBER] = f"+{code} {self._values[PHONE_NUMBER]}"
            _save_and_continue(page)
        raise Unsupported(known_gap(self, "long_wizard", f"more than {MAX_PAGES} pages"))

    def _unread(self, page: Page) -> None:
        """Stops on fields the read did not list, optional ones too. A page the read did not
        reach is read once the pages before it are saved; where no read can get there, `revealed`
        lists it. On one it read, an answer opened the field (a country's address, a referral's
        name): the page stops unsaved, so no read would list it."""
        unread = _not_listed(_page_fields(page), self._listed)
        if not unread:
            return
        step, labels = _step(page), ", ".join(r["label"] for r in unread)
        if step not in self._walked and self._walled:
            self._behind = True
        elif step not in self._walked:
            raise NotTaken(f"{step} shows fields the read did not: {labels}; read again")
        elif step == MY_INFORMATION and any(r["id"].startswith(ADDRESS) for r in unread):
            detail = f"the country answered shows fields the read did not: {labels}"
            raise Unsupported(known_gap(self, "other_country", detail))
        else:
            raise Unsupported(f"unhandled_rendering: an answer showed {labels}, not listed")

    def revealed(self, page: Page) -> list[Field]:
        """The fields of the page behind the consent the fill stopped on, none once it ended."""
        if not self._behind:
            return []
        fields = _read(page, self._phone_codes, self._listed)
        self._fields |= {f.id: f for f in fields if not f.unfillable}
        self._listed |= {f.id for f in fields}
        return fields

    def read_back(self, page: Page) -> dict[str, Answer]:
        """Each page's values, read before it was saved; one file as its name."""
        values: dict[str, Answer] = {}
        for k, v in self._values.items():
            if k not in self._fields or v in (None, "", []):
                continue
            one_file = self._fields[k].type is FieldType.FILE and len(v) == 1
            values[k] = v[0] if one_file else v
        return values

    def submit(self, page: Page) -> str | None:
        with page.expect_response(
            lambda r: r.request.method in ("POST", "PUT") and "/submit" in r.url,
            timeout=SUBMIT_TIMEOUT_MS,
        ) as sent:
            page.locator(NEXT).click()
        response = sent.value
        if response.ok:
            return f"{response.request.method} {urlsplit(response.url).path} {response.status}"
        raise RuntimeError(f"{response.url} answered {response.status}")


def _email_form(page: Page) -> None:
    """Opens the email form where the page offers social accounts first."""
    form = page.locator(f"{EMAIL}:visible, {WITH_EMAIL}:visible").first
    form.wait_for(timeout=FORM_TIMEOUT_MS)
    if page.locator(WITH_EMAIL).is_visible():
        page.locator(WITH_EMAIL).click()
        page.locator(EMAIL).wait_for(timeout=FORM_TIMEOUT_MS)


def _submit(page: Page, button: str, email: str, password: str) -> None:
    """Fills the email and password, presses the button through the overlay that takes its
    clicks, and waits to leave the login page or for what the page says."""
    page.locator(EMAIL).fill(email)
    page.locator("[data-automation-id=password]").fill(password)
    press = page.locator(
        f"[data-automation-id=noCaptchaWrapper]:has([data-automation-id={button}])"
        " [data-automation-id=click_filter]"
    )
    said = page.locator("[data-automation-id=errorMessage], [data-automation-id=inputAlert]")
    requested: list[PageRequest] = []
    page.on("request", lambda r: requested.append(r))
    for _ in range(PRESSES):  # the first press after typing can post nothing
        if not _on_login(page) or any(_redirected(r) for r in requested):
            break
        try:
            with page.expect_response(_posts_account, timeout=PRESS_TIMEOUT_MS):
                press.click()
            break
        except Timeout:
            if said.count():  # a field the page refused without posting
                break
    deadline = time.monotonic() + SIGN_IN_TIMEOUT_S
    while time.monotonic() < deadline:
        redirects = [_redirected(r) for r in requested]
        if "/login" in redirects:
            raise Refused("email_in_use: the sign-up went back to the login page")
        if "/login/warning" in redirects:
            raise Blocked("email_link: the sign-up sent a verification email")
        if not _on_login(page):
            return
        if said.count():
            text = " ".join(said.all_inner_texts()).strip()
            for pattern, error, code in MESSAGES:
                if pattern.search(text):
                    raise error(f"{code}: {text}")
            raise Unsupported(f"unhandled_rendering: the login page says {text!r}")
        page.wait_for_timeout(SIGN_IN_POLL_MS)
    raise Unsupported("unhandled_rendering: the login page answered nothing")


def _on_login(page: Page) -> bool:
    return urlsplit(page.url).path.endswith("/login")


def _redirected(request: PageRequest) -> str | None:
    """Where a sign-up redirected to, after the site: `/login` for an email that has an
    account, `/login/warning` once a verification email is sent; None for another request."""
    before = request.redirected_from
    if before is None or not urlsplit(before.url).path.endswith("/register"):
        return None
    found = site(request.url)
    return found.rest if found else None


def _posts_account(response: Response) -> bool:
    return response.request.method == "POST" and bool(
        re.search(r"/(login|register)$", urlsplit(response.url).path)
    )


def _json(page: Page, url: str) -> Any:
    """A Workday API's answer, fetched by the page: same origin, the session's cookies."""
    return page.evaluate(
        """async url => {
            const response = await fetch(url, {headers: {Accept: "application/json"}});
            return response.ok ? response.json() : {};
        }""",
        url,
    )


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(TAG.sub(" ", html))).strip()


def _questions_page(step: str) -> bool:
    return step.startswith("Application Questions")


def _questionnaire_step(step: str) -> str:
    """The key a page's API fields are under: `… 1 of 2` is the primary questionnaire's."""
    return re.sub(r" 1 of \d+$", "", step)


def _not_listed(raws: list[dict[str, Any]], listed: set[str]) -> list[dict[str, Any]]:
    """The fields not in `listed`, but for a later entry of a list: the draft's, numbered anew
    by each load, and no answer's."""
    return [r for r in raws if r["id"] not in listed and not ENTRY_ID.match(r["id"])]


def _only_a_tick_missing(raws: list[dict[str, Any]]) -> bool:
    """Whether a page holds all it requires but a box: the draft keeps a page's answers but not
    its consent's tick, so no read saves that page, whatever the fills before it saved."""
    empty = [r for r in raws if r["required"] and r["value"] in (None, "", [], "No")]
    return bool(empty) and all(r["kind"] == "box" for r in empty)


def _read(page: Page, phone_codes: str, listed: set[str]) -> list[Field]:
    """The fields the page shows that are not in `listed`, a dropdown's or a short prompt's
    options read by opening it, the country phone codes from `phone_codes`, the API's list."""
    raws = _not_listed(_page_fields(page), listed)
    for raw in raws:
        box = page.locator(f'[data-fkit-id="{raw["dom"]}"]')
        if raw["kind"] == "list":
            raw["options"] = _options(page, raw)
        elif raw["id"] == PHONE_CODE_ID:
            raw["options"] = [c["descriptor"] for c in _json(page, phone_codes)]
        elif raw["kind"] == "prompt":
            raw["options"] = _prompt_options(page, box)
    return [f for raw in raws if (f := _field(raw)) is not None]


def _wait_for_page(page: Page) -> None:
    """Waits for the wizard's page, or for what stands before it."""
    page.locator(f"{NEXT}, {APPLIED}").first.wait_for(timeout=FORM_TIMEOUT_MS)
    if page.locator(APPLIED).count():
        raise Unsupported("posting_gone: the account already applied to this posting")
    if _step(page) == REVIEW:
        return
    page.locator(FIELD).first.wait_for(timeout=FORM_TIMEOUT_MS)
    counts, deadline = [-1], time.monotonic() + FORM_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:  # sections render one after another, some seconds apart
        page.wait_for_timeout(SETTLE_MS)
        counts.append(page.locator(FIELD).count())
        if len(set(counts[-SETTLED:])) == 1 and len(counts) > SETTLED:
            return


def _step(page: Page) -> str:
    """The wizard's current page, `My Information`…"""
    return page.locator(STEP).inner_text().strip().splitlines()[-1].strip()


def _page_fields(page: Page) -> list[dict[str, Any]]:
    """The page's fields, a list's first entry's parts as `<list>--<part>`: the page numbers
    entries its own way (`workExperience-8--jobTitle`)."""
    raws, first = page.evaluate(_FIELDS), {}
    for raw in raws:
        raw["dom"] = raw["id"]  # the page's, to find the field by
        entry = ENTRY_ID.match(raw["id"])
        if entry and first.setdefault(entry[1], entry[2]) == entry[2]:
            raw["id"] = ENTRY_ID.sub(r"\1--", raw["id"])
            raw["id"] = SCHOOL_IDS.get(raw["id"], raw["id"])
    return raws


def _add_entries(page: Page, answers: dict[str, Answer]) -> None:
    """Adds a list's first entry where answers are given for it and the page shows none."""
    shown = {r["id"] for r in _page_fields(page)}
    groups = page.locator("[role=group][aria-labelledby]").all()
    for prefix, pattern, _ in ENTRY_LISTS:
        answered = any(a.startswith(f"{prefix}--") for a in answers)
        if not answered or any(i.startswith(f"{prefix}--") for i in shown):
            continue
        # `Work-Experience-section`, `Work-History-(Optional)-section`
        for group in (g for g in groups if pattern.match(_group_name(g))):
            if group.locator(ADD).count():
                group.locator(ADD).first.click()
                page.locator(f'[data-fkit-id^="{prefix}-"]').first.wait_for(timeout=FORM_TIMEOUT_MS)
            break


def _group_name(group: Locator) -> str:
    return (group.get_attribute("aria-labelledby") or "").replace("-", " ")


def _rendered(page: Page, answers: dict[str, Answer]) -> list[dict[str, Any]]:
    """The page's fields once each answered one shows its control: an upload's comes last."""
    for _ in range(RENDER_TRIES):
        raws = _page_fields(page)
        if all(r["kind"] for r in raws if r["id"] in answers):
            break
        page.wait_for_timeout(SETTLE_MS)
    return raws


def _filled(page: Page, answers: dict[str, Answer]) -> list[dict[str, Any]]:
    """The page's fields once each answered one shows a value: a picked item can leave the
    page for a moment as it renders again."""
    for _ in range(RENDER_TRIES):
        raws = _page_fields(page)
        if all(r["value"] not in (None, "", []) for r in raws if r["id"] in answers):
            break
        page.wait_for_timeout(SETTLE_MS)
    return raws


def _lost(raw: dict[str, Any], answer: Answer) -> bool:
    """Whether the page dropped a field's answer; an upload still attaching shows no value."""
    return bool(answer) and raw["kind"] != "file" and raw["value"] in (None, "", [])


def _field(raw: dict[str, Any]) -> Field | None:
    kind, options = KINDS.get(raw["kind"]), raw["options"]
    if raw["kind"] == "box":
        options = YES_NO
    if raw["id"] == PHONE_NUMBER:
        kind = FieldType.PHONE
    if raw["kind"] == "prompt" and options:  # a list of one value to search
        kind = FieldType.CHOICE
    if kind is None:
        reason = f"unhandled_rendering: {raw['label'] or raw['id']!r} is a {raw['kind']}"
        return Field(raw["id"], raw["label"], FieldType.TEXT, raw["required"], unfillable=reason)
    value = raw["value"] if raw["value"] not in (None, "", [], "No") else None
    site = raw["id"] in SITE_DEFAULTS
    return Field(
        raw["id"], raw["label"], kind, raw["required"], options,
        prefilled=None if site else value, default=value if site else None,
        notice=raw["notice"],
    )  # fmt: skip


def _experience(adapter: Workday, page: Page, flow: dict[str, Any], values: str) -> list[Field]:
    """My Experience from the apply flow's sections: the resume, skills, the first entry of the
    work experience and the education lists, their degrees and fields of study from `values`,
    the API's lists. A section's own fields are sections too, hidden or required each; the other
    lists (websites, languages, certifications) atsfill does not fill, a known gap where one is
    required."""
    sections = [
        s
        for p in flow.get("data") or []
        if p.get("descriptor") == EXPERIENCE
        for s in p.get("sections") or []
    ]
    shown = {s["label"]: bool(s["required"]) for s in sections if not s.get("hidden")}
    fields = []
    for label, required in shown.items():
        if RESUME.search(label):
            fields.append(Field(RESUME_ID, label, FieldType.FILE, required))
        elif SKILLS.match(label):
            fields.append(Field(SKILLS_ID, label, FieldType.TYPEAHEAD, required))
        elif entry := next((e for e in ENTRY_LISTS if e[1].match(label)), None):
            fields += _entry(page, entry, required, shown, values)
        elif ENTRIES.match(label) and not ENTRY_PART.search(label):
            reason = known_gap(adapter, "experience_entries", f"{label} is a list of entries")
            fields.append(
                Field(f"myExperience--{label}", label, FieldType.TEXT, required, unfillable=reason)
            )
    return fields


def _entry(
    page: Page,
    entry: EntryList,
    required: bool,
    shown: dict[str, bool],
    values: str,
) -> list[Field]:
    """The first entry of a list: each of its parts the posting shows, required where the list
    is and the part is, `<list>--<part>` as its id."""
    prefix, _, parts = entry
    name = {"workExperience": "Work Experience", "education": "Education"}[prefix]
    ids = {f"{prefix}--{p}" for p, _, _, section in parts if section is None or section in shown}
    fields = []
    for part, label, kind, section in parts:
        if section is not None and section not in shown:
            continue
        needed = required and (section is None or shown[section])
        fid, label = f"{prefix}--{part}", f"{name}: {label}"
        current = f"{prefix}--currentlyWorkHere"
        match kind:
            case "month" | "year":
                suffix = " (MM/YYYY)" if kind == "month" else " (year)"
                ended = Is(current, ["No"]) if part == "endDate" and current in ids else None
                fields.append(Field(fid, label + suffix, FieldType.TEXT, needed, shown_when=ended))
            case "box":  # unticked before any answer
                fields.append(Field(fid, label, FieldType.CHOICE, False, YES_NO, default="No"))
            case "school":
                fields.append(Field(fid, label, FieldType.TYPEAHEAD, needed))
            case "degrees" | "fieldsOfStudy":  # a dropdown, a prompt: the API's list either way
                listed = _json(page, f"{values}/educations/{kind}")
                if listed and all("href" in v for v in listed):  # a long list, in groups
                    first = urlsplit(listed[0]["href"])
                    url = f"{urlsplit(values).scheme}://{urlsplit(values).netloc}{first.path}"
                    options = [v["descriptor"] for v in _json(page, f"{url}?{first.query}")]
                    fields.append(Field(fid, label, FieldType.TYPEAHEAD, needed, options or None))
                else:
                    options = [v["descriptor"] for v in listed]
                    fields.append(Field(fid, label, FieldType.CHOICE, needed, options or None))
            case _:
                fields.append(Field(fid, label, FieldType.TEXT, needed))
    return fields


def _questions(kind: str, items: list[dict[str, Any]]) -> list[Field]:
    """A questionnaire's questions. One an answer branches to is asked only after it: shown
    when any question branching to it holds one of those answers, itself a follow-up at
    times."""
    branches: dict[str, dict[str, list[str]]] = {}  # a follow-up's parents and their answers
    for item in items:
        for answer in item.get("possibleAnswers") or []:
            if follow_up := (answer.get("branchingQuestion") or {}).get("id"):
                picks = branches.setdefault(follow_up, {}).setdefault(item["id"], [])
                picks.append(answer["answerText"])
    fields = []
    for item in items:
        fid, text = f"{kind}Questionnaire--{item['id']}", _text(item.get("body") or "")
        descriptor = item.get("type", {}).get("descriptor")
        if descriptor == "Number":
            text += " (a number)"
        held: list[Condition] = [
            Is(f"{kind}Questionnaire--{parent}", picks)
            for parent, picks in branches.get(item["id"], {}).items()
        ]
        when = None if not held else held[0] if len(held) == 1 else AnyOf(held)
        options = [a["answerText"] for a in item.get("possibleAnswers") or []] or None
        required = bool(item.get("required"))
        field_type = QUESTION_TYPES.get(descriptor)
        if field_type is None:
            reason = f"unhandled_rendering: {fid} is a question of type {descriptor}"
            fields.append(Field(fid, text, FieldType.TEXT, required, unfillable=reason,
                                shown_when=when))  # fmt: skip
            continue
        if field_type not in (FieldType.CHOICE, FieldType.MULTI_CHOICE):
            options = None
        fields.append(Field(fid, text, field_type, required, options, shown_when=when))
    return fields


def _fill(page: Page, field: Field, raw: dict[str, Any], answer: Answer) -> None:
    box = page.locator(f'[data-fkit-id="{raw["dom"]}"]')
    match raw["kind"], answer:
        case "text", str():
            control = box.locator("textarea, input[type=text]").first
            if field.type is FieldType.PHONE:
                answer = _national(page, answer)
            control.fill("")
            control.press_sequentially(answer)  # some pages miss a value filled at once
            control.blur()  # the page takes the value on leaving the field
        case "list", str():
            _pick(page, field, box, answer)
        case "radio" | "boxes" | "box", _:
            wanted = {answer} if isinstance(answer, str) else set(answer)
            for option in box.locator("input[type=radio], input[type=checkbox]").all():
                name = "Yes" if raw["kind"] == "box" else _option_label(option)
                checked = any(matching.matches(field, w, name) for w in wanted)
                if raw["kind"] == "box":
                    checked = answer == "Yes"
                if option.is_checked() != checked and (checked or raw["kind"] != "radio"):
                    option.click()
        case "prompt", str():
            _prompt(page, field, box, answer)
        case "date" | "month" | "year", str():
            if raw["kind"] == "date":
                year, month, day = answer.split("-")
            elif raw["kind"] == "month":
                (month, year), day = answer.split("/"), ""
            else:
                year, month, day = answer, "", ""
            for part, value in (("Month", month), ("Day", day), ("Year", year)):
                section = box.locator(f"[data-automation-id=dateSection{part}-input]")
                if section.count():
                    section.focus()  # a display over it takes clicks
                    section.press("Control+a")  # the draft's value
                    section.press_sequentially(value)
        case "file", _:
            files = [answer] if isinstance(answer, str) else answer
            delete = box.locator(DELETE_FILE)
            while (count := delete.count()) > 0:  # the draft's or the account profile's files
                delete.first.click()
                deadline = time.monotonic() + UPLOAD_TIMEOUT_MS / 1000
                while delete.count() == count and time.monotonic() < deadline:
                    page.wait_for_timeout(STEP_POLL_MS)
            box.locator("input[type=file]").set_input_files(files)
            for path in files:
                name = path.rsplit("/", 1)[-1]
                box.locator(UPLOADED, has_text=name).first.wait_for(timeout=UPLOAD_TIMEOUT_MS)
        case kind, _:
            reason = f"unhandled_rendering: cannot fill {field.id}, a {kind}, with {answer!r}"
            raise Unsupported(reason)


def _option_label(option: Locator) -> str:
    return option.evaluate(
        """e => (document.querySelector(`label[for="${CSS.escape(e.id)}"]`)
              ?? e.closest("label") ?? e.parentElement).innerText.trim()"""
    )


def _phone_code(page: Page) -> str | None:
    """The country code the form shows beside the phone number, `44` for `(+44)`."""
    box = page.locator(PHONE_CODE)
    code = re.search(r"\(\+(\d+)\)", box.inner_text()) if box.count() else None
    return code[1] if code else None


def _national(page: Page, number: str) -> str:
    """The number without the country code the form shows beside it, which must be the
    number's own."""
    code, digits = _phone_code(page), re.sub(r"[^\d+]", "", number)
    if code and digits.startswith(f"+{code}"):
        return digits[len(code) + 1 :]
    if code and digits.startswith("+"):
        raise NotTaken(f"{number!r} is not a +{code} number: answer {PHONE_CODE_ID} for it")
    return number


def _until_rendered(page: Page, pending: set[PageRequest]) -> None:
    page.wait_for_timeout(STEP_POLL_MS)  # the request goes out a moment after the answer
    deadline = time.monotonic() + STEP_TIMEOUT_MS / 1000
    while pending and time.monotonic() < deadline:
        page.wait_for_timeout(STEP_POLL_MS)


def _pick(page: Page, field: Field, box: Locator, answer: str) -> None:
    """A dropdown: its options are in the listbox the button controls."""
    listbox = _open(page, box.locator("button[aria-haspopup=listbox]").first)
    for option in listbox.locator("[role=option]:not([aria-disabled=true])").all():
        if matching.matches(field, answer, option.inner_text().strip()):
            option.click()
            return
    page.keyboard.press("Escape")
    raise NotTaken(f"no option {answer!r} in {field.id}")


def _open(page: Page, button: Locator) -> Locator:
    """Opens a dropdown and waits for its options, which load a moment later."""
    for press in range(PRESSES):  # a click right after typing elsewhere can go unheard
        button.click()
        try:  # some tenants name the listbox only once it opens
            page.wait_for_function(
                "b => b.getAttribute('aria-controls')",
                arg=button.element_handle(),
                timeout=MENU_TIMEOUT_MS / PRESSES,
            )
            break
        except Timeout:
            if press == PRESSES - 1:
                raise
    listbox = page.locator(f"[id='{button.get_attribute('aria-controls')}']")
    listbox.locator(f"[role=option]:not(:text-is('{SELECT_ONE}'))").first.wait_for(
        timeout=MENU_TIMEOUT_MS
    )
    return listbox


def _options(page: Page, raw: dict[str, Any]) -> list[str]:
    listbox = _open(
        page, page.locator(f'[data-fkit-id="{raw["dom"]}"] button[aria-haspopup=listbox]').first
    )
    names = [
        o.inner_text().strip()
        for o in listbox.locator("[role=option]:not([aria-disabled=true])").all()
    ]
    page.keyboard.press("Escape")
    return [n for n in names if n and n != SELECT_ONE]


def _prompt(page: Page, field: Field, box: Locator, answer: str) -> None:
    """A search prompt: typing and Enter list the items that match, at any depth. A prompt of
    one item has it replaced; skills, of many, get one more."""
    if _picked(field, box, answer):
        return
    if field.id != SKILLS_ID:  # every other prompt holds one item
        for picked in box.locator(PICKED).all():
            picked.locator(DELETE_PICK).click()
    # The search can miss a name with brackets that their content finds: `Citizen (Ireland)`
    for term in [answer, *BRACKETED.findall(answer)]:
        if _search(page, field, box, answer, term):
            return
    if _pick_in_list(page, field, box, answer):  # a search can miss an item the list holds
        return
    raise NotTaken(f"no item {answer!r} in {field.id}")


def _search(page: Page, field: Field, box: Locator, answer: str, term: str) -> bool:
    """Types the term and Enter, which picks the only item that matches, else lists them."""
    search = _close_prompts(page, box)
    search.fill(term)  # a click would land on the picked item over it
    search.press("Enter")
    options = page.locator(PROMPT_OPTION)
    deadline = time.monotonic() + MENU_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        if _picked(field, box, answer):
            _close_prompts(page, box)
            return True
        for option in options.all():
            if matching.matches(field, answer, option.inner_text().strip()):
                option.click()
                _close_prompts(page, box)
                return True
        page.wait_for_timeout(STEP_POLL_MS)
    return False


def _pick_in_list(page: Page, field: Field, box: Locator, answer: str) -> bool:
    """Picks the item from the prompt's whole list, a category's items behind it."""
    search = _close_prompts(page, box)
    search.fill("")
    search.press("Enter")
    try:
        page.locator(PROMPT_OPTION).first.wait_for(timeout=MENU_TIMEOUT_MS)
    except Timeout:
        _close_prompts(page, box)
        return False
    for n, row in enumerate(page.evaluate(_PROMPT_ROWS)):
        if not row["nested"] and matching.matches(field, answer, row["name"]):
            page.locator(PROMPT_OPTION).nth(n).click()
            break
        if row["nested"]:
            page.locator(PROMPT_OPTION).nth(n).click()
            page.locator(BACK).wait_for(timeout=MENU_TIMEOUT_MS)
            names = [r["name"] for r in page.evaluate(_PROMPT_ROWS)]
            if found := [m for m, n in enumerate(names) if matching.matches(field, answer, n)]:
                page.locator(PROMPT_OPTION).nth(found[0]).click()
                break
            page.locator(BACK).click()
            page.locator(BACK).wait_for(state="hidden", timeout=MENU_TIMEOUT_MS)
    _close_prompts(page, box)
    return _picked(field, box, answer)


def _picked(field: Field, box: Locator, answer: str) -> bool:
    picked = box.locator(PICKED).all_inner_texts()
    return any(matching.matches(field, answer, p.strip()) for p in picked)


def _close_prompts(page: Page, box: Locator) -> Locator:
    """Closes any open prompt list; the box's search input."""
    search = box.locator(SEARCH)
    for _ in range(PROMPT_LEVELS):
        if not page.locator(PROMPT_OPTION).count():
            break
        search.press("Escape")
        page.mouse.click(1, 1)  # outside any list, which closes it
    return search


def _prompt_options(page: Page, box: Locator) -> list[str] | None:
    """The items of a prompt's short list, a category's items in its place; None for a long
    list or a search."""
    search = _close_prompts(page, box)
    search.focus()
    search.press("Enter")  # on an empty search: the whole list
    options = page.locator(PROMPT_OPTION)
    try:
        options.first.wait_for(timeout=MENU_TIMEOUT_MS)
    except Timeout:
        return None
    rows = page.evaluate(_PROMPT_ROWS)
    if not rows or rows[0]["size"] > MAX_PROMPT_OPTIONS:
        _close_prompts(page, box)
        return None
    items = []
    for n, row in enumerate(rows):
        if not row["nested"]:
            items.append(row["name"])
            continue
        page.locator(PROMPT_OPTION).nth(n).click()  # by place: a name can hold a no-break space
        page.locator(BACK).wait_for(timeout=MENU_TIMEOUT_MS)
        items += [r["name"] for r in page.evaluate(_PROMPT_ROWS)]
        page.locator(BACK).click()
        page.locator(BACK).wait_for(state="hidden", timeout=MENU_TIMEOUT_MS)
    _close_prompts(page, box)
    return items


def _save_and_continue(page: Page) -> None:
    """Saves the page; raises with what the page says when it stays. A press can go unheard,
    like the login page's: one that changes nothing is made again."""
    before, shown = _step(page), _errors(page)
    for _ in range(PRESSES):
        page.locator(NEXT).click()
        deadline = time.monotonic() + STEP_TIMEOUT_MS / 1000 / PRESSES
        while time.monotonic() < deadline:
            if _step(page) != before:
                _wait_for_page(page)
                return
            if (errors := _errors(page)) and errors != shown:  # a former save's stay a while
                raise NotTaken(f"{before} was not saved: {errors}")
            page.wait_for_timeout(STEP_POLL_MS)
        if errors := _errors(page):
            raise NotTaken(f"{before} was not saved: {errors}")
    raise RuntimeError(f"{before} was not saved in {STEP_TIMEOUT_MS / 1000:.0f}s")


def _errors(page: Page) -> str:
    return re.sub(r"\s+", " ", " ".join(page.locator(ERRORS).all_inner_texts())).strip()


# Each field the page shows: its id, label, whether required (`*`), rendering, value, and the
# text a consent's box stands under.
_FIELDS = """() => {
  const text = e => (e?.innerText ?? "").replace(/\\s+/g, " ").trim();
  const labelOf = (f, e) => text(f.querySelector(`label[for="${CSS.escape(e.id)}"]`)
    ?? e.closest("label") ?? e.parentElement);
  return [...document.querySelectorAll('[data-automation-id^="formField-"][data-fkit-id]')]
    .filter(f => f.offsetParent !== null)
    .map(f => {
      const label = text(f.querySelector("label, legend"));
      const list = f.querySelector("button[aria-haspopup=listbox]");
      const prompt = f.querySelector("input[data-uxi-widget-type=selectinput]");
      const radios = [...f.querySelectorAll("input[type=radio]")];
      const boxes = [...f.querySelectorAll("input[type=checkbox]")];
      const file = f.querySelector("input[type=file]");
      const date = f.querySelector("[data-automation-id^=dateSection]");
      const typed = f.querySelector("textarea, input[type=text]");
      let kind = null, value = null, options = null;
      if (file) {
        kind = "file";
        value = [...f.querySelectorAll("[data-automation-id=file-upload-item-name]")].map(text);
      } else if (date) {
        // a date, a month (MM/YYYY) or a year, by the sections it shows
        const part = p => f.querySelector(`[data-automation-id=dateSection${p}-input]`)?.value;
        const [y, m, d] = [part("Year"), part("Month"), part("Day")];
        kind = d !== undefined ? "date" : m !== undefined ? "month" : "year";
        const pad = v => v.padStart(2, "0");
        value = kind === "date" ? (y && m && d ? `${y}-${pad(m)}-${pad(d)}` : null)
          : kind === "month" ? (y && m ? `${pad(m)}/${y}` : null) : (y || null);
      } else if (list) {
        kind = "list";
        value = text(list) === "Select One" ? null : text(list);
      } else if (prompt) {
        kind = "prompt";
        const picked = [...f.querySelectorAll("[data-automation-id=selectedItem]")].map(text);
        value = picked.length ? picked.join(", ") : null;
      } else if (radios.length) {
        kind = "radio";
        options = radios.map(e => labelOf(f, e));
        const checked = radios.find(e => e.checked);
        value = checked ? labelOf(f, checked) : null;
      } else if (boxes.length > 1) {
        kind = "boxes";
        options = boxes.map(e => labelOf(f, e));
        value = boxes.filter(e => e.checked).map(e => labelOf(f, e));
      } else if (boxes.length === 1) {
        kind = "box";
        value = boxes[0].checked ? "Yes" : "No";
      } else if (typed) {
        kind = "text";
        value = typed.value;
      }
      const notice = kind === "box" && f.closest("[role=group]")
        ?.querySelector("[data-automation-id=instructionalText]");
      return {id: f.dataset.fkitId, label: label.replace(/\\*$/, "").trim(),
              required: label.endsWith("*"), kind, value, options,
              notice: text(notice || null) || null};
    });
}"""


# The open prompt list's rows: each item's name, whether it opens a category, the list's size.
_PROMPT_ROWS = r"""() => [...document.querySelectorAll(
    "[data-automation-id=activeListContainer] [role=option]")]
  .filter(row => row.offsetParent !== null)
  .map(row => ({
    name: (row.querySelector("[data-automation-id=promptOption]")?.innerText ?? "")
      .replace(/\s+/g, " ").trim(),
    nested: !!row.querySelector("[data-uxi-multiselectlistitem-hassidecharm=true]"),
    size: Number(row.getAttribute("aria-setsize") || 0),
  }))
  .filter(row => row.name)"""
