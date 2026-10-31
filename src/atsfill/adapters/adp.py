"""ADP Workforce Now's career center (`workforcenow.adp.com/mascsr/.../recruitment.html`), as
docs/adapters/adp.md observes.

Each employer keeps its own candidate profile, without a password: every sign-in is an emailed
OTP, a sign-up the employer's talent community step. Signed in, Apply opens the application's
wizard, one page whose Next moves step by step on the client. The read lists Personal
Information from the open step and the rest from the wizard's meta; the fill answers each step
up to the attestation, the read-back being each step's values before its Next.
"""

import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from atsfill.core import matching, otp
from atsfill.core.adapter import (
    Blocked,
    NotTaken,
    Refused,
    SignedOut,
    SignUpDetails,
    Unsupported,
)
from atsfill.core.browser import Locator, Page, Request, Timeout
from atsfill.core.results import Answer, Field, FieldType, Is, SideEffect

HOST = "workforcenow.adp.com"
CAREER_CENTER = f"https://{HOST}/mascsr/default/mdf/recruitment/recruitment.html"
SIGNED_IN = f"https://{HOST}/mascsr/applicant/mdf/recruitment/postLogin.html"
EVENTS = f"https://{HOST}/mascsr/applicant/careercenter-auth/events/staffing"
APPLICANTS = f"{EVENTS}/v2/job-applicants"
META = f"{EVENTS}/v1/job-application.submit/meta"
PAGE_TIMEOUT_MS = 60_000
STEP_TIMEOUT_MS = 30_000
SIGNED_IN_TIMEOUT_MS = 60_000
UPLOAD_TIMEOUT_MS = 60_000
SUBMIT_TIMEOUT_MS = 90_000
SETTLE_MS = 1_500  # a step re-renders, a list opens
OPTION_TIMEOUT_MS = 10_000
KEY_DELAY_MS = 40
YES_NO = ["Yes", "No"]
DATE = "%m/%d/%Y"
NEXT = "#ja_sv_cw_next_footer_btn"
PREVIOUS = "#ja_sv_cw_prev_footer_btn"
SUBMIT = "#submitApplication"
ATTEST = "input[name=self_att_agree_chk]"
SIGNATURE = "attestation.signature"
OPT_OUT = "resume.ai_opt_out"
LATE = {SIGNATURE: OPTION_TIMEOUT_MS, OPT_OUT: UPLOAD_TIMEOUT_MS}  # boxes shown a moment after
PERSON = "/jobApplication/applicant/person"
PAY = "/jobApplication/requestedPayRate"

# Personal Information as the step shows it: the field's id, its box, its type
PERSONAL = [
    ("first_name", "#personalInfomationFirstName", FieldType.TEXT),
    ("middle_name", "#personalInfomationMiddleName", FieldType.TEXT),
    ("last_name", "#personalInfomationLastName", FieldType.TEXT),
    ("email", "#personalInfomationEmail", FieldType.TEXT),
    ("mobile_phone", "#personalInfomationMobileNumberError", FieldType.PHONE),
    ("home_phone", "#personalInfomationMobileNumberErrorMessage", FieldType.PHONE),
    ("country", "#PersonalAddress_country", FieldType.CHOICE),
    ("address_line1", "#PersonalAddress_address_line1", FieldType.TEXT),
    ("address_line2", "#PersonalAddress_address_line2", FieldType.TEXT),
    ("address_line3", "#PersonalAddress_address_line3", FieldType.TEXT),
    ("city", "#PersonalAddress_city", FieldType.TEXT),
    ("state", "#PersonalAddress_state", FieldType.CHOICE),
    ("postal_code", "#PersonalAddress_postalCode", FieldType.TEXT),
    ("county", "#PersonalAddress_county", FieldType.TEXT),
]
LABELS = {"home_phone": "Home Phone Number", "mobile_phone": "Mobile Number*"}
SHOWN_BY_COUNTRY = {"county"}  # once a country with counties is picked
ADDRESS_MAX = 30  # "Address Line 1 must not exceed 30 characters"
FIXED = {"personal.email"}  # the verified email, read only
QUESTIONS = "#question_0, #questionYes_0, #desiredSalaryId, #refferalOptions, #commentTextArea"
NOT_FIELDS = re.compile(r"select-filter-input|react-select-.*")  # a list's own inputs

# Voluntary Self-ID: its lists and radio groups, each with the meta's indicator that shows it
# and its code list; a radio's value is its code
SELF_ID = [
    ("gender", "Gender", "#vsidGender", "GENDER", "genderCode"),
    ("ethnicity", "Ethnicity", "#vsidEthinicity", "RACE", "ethnicityCode"),
    ("race", "Race", "#vsidRace", "RACE", "raceCode"),
    ("veteran", "Protected Veteran Status", "sdf-radio-group:has(#veteranStatusIdYes)",
     "VSID_VeteranEnabledIndicator", "militaryStatusCode"),
    ("disability", "Disability Status", "sdf-radio-group:has(#disabilityStatusIdYes)",
     "VSID_DisabilityEnabledIndicator", "disabilityTypeCodes"),
]  # fmt: skip


@dataclass
class Step:
    name: str
    marker: str  # a box only this step shows
    fields: list[Field] = field(default_factory=list)


class ADP:
    name = "adp"
    per_employer = True  # a candidate profile per employer: the scope is its career center
    asks = {"name", "phone"}
    known_gaps: dict = {}
    side_effects = [
        SideEffect("sign_up", "employer", "login --create joins the employer's talent community"),
        SideEffect("load", "employer", "Apply leaves a draft application, Pending Submission"),
        SideEffect("upload", "employer", "the résumé is uploaded to the employer's ADP"),
        SideEffect("submit", "employer", "Submit sends the application"),
    ]

    def __init__(self) -> None:
        self._steps: list[Step] = []
        self._boxes: dict[str, str] = {}  # each field's box, by field id
        self._values: dict[str, Answer] = {}
        self._codes: dict[str, dict[str, str]] = {}  # a radio's label by its value, by field id
        self._amounts: set[str] = set()  # the page shows them formatted: `50,000.00`
        self._shown: dict[str, None] = {}  # the fields each step showed, in order

    def detect(self, url: str, page: Page | None) -> bool:
        return _posting(url) is not None

    def posting(self, url: str) -> str:
        found = _posting(url)
        if found is None:
            return url
        cid, cc_id, job = found
        return f"{CAREER_CENTER}?{urlencode({'cid': cid, 'ccId': cc_id, 'jobId': job})}"

    def account_scope(self, url: str) -> str:
        """The employer's career center, `<cid>.<ccId>`: its sign-in page needs both."""
        found = _posting(url) or _career_center(url)
        return f"{found[0]}.{found[1]}" if found else ""

    def login_url(self, scope: str | None) -> str:
        cid, _, cc_id = (scope or "").partition(".")
        query = urlencode({"cid": cid, "ccId": cc_id, "lang": "en_US", "selectedMenuKey": "SignIn"})
        return f"{CAREER_CENTER}?{query}"

    def signed_in(self, request: Request, scope: str | None) -> bool | None:
        """Signed out, the candidate profile's endpoint redirects to ADP's login page."""
        cid = (scope or "").partition(".")[0]
        response = request.get(f"{APPLICANTS}?cid={cid}", max_redirects=0)
        if 300 <= response.status < 400:
            return False
        return True if response.ok else None

    def sign_in_by_otp(self, page: Page, email: str) -> None:
        _sign_in(page, email, None)

    def sign_up_by_otp(self, page: Page, email: str, details: SignUpDetails) -> None:
        _sign_in(page, email, details)

    def browser_read(self, page: Page) -> list[Field]:
        """Opens the wizard on its first step; Personal Information from the page, the
        questionnaire, the desired pay, Voluntary Self-ID and the attestation from the meta."""
        _open(page)
        query = parse_qs(urlsplit(page.url).query)
        cid, cc_id, job = query["cid"][0], query["ccId"][0], query["jobId"][0]
        meta = page.evaluate(_FETCH, [f"{META}?cid={cid}&ccId={cc_id}&lang=en_US", job])
        control = (meta.get("data") or {}).get("control") or {}
        transforms = _transforms(meta)
        shown = page.locator("body").inner_text()
        self._boxes, self._codes, self._amounts = {}, {}, set()
        indicators = {
            (n := i["nameCode"]).get("codeValue") or n.get("shortName"): i.get("indicatorValue")
            for i in control.get("customIndicators") or []
        }
        asked = self._questions(control, transforms)
        self._steps = [
            Step("Personal Information", PERSONAL[0][1], self._personal(page, transforms)),
            Step("Resume", "input[type=file]", [self._resume(), self._opt_out()]),
        ]  # fmt: skip
        # A wizard with nothing to ask has no Questions step; one that asks nothing the meta
        # lists has no box of its own: Next moves past it
        if asked or "Questions" in shown:
            self._steps.append(Step("Questions", QUESTIONS if asked else "", asked))
        if "Voluntary Self-ID" in shown:
            fields = self._self_id(transforms, indicators)
            self._steps.append(Step("Voluntary Self-ID", "#vsidWhy, #vsidEthRace", fields))
        self._steps.append(Step("Review Your Application", ""))
        attest = [self._attestation(control), self._signature()]
        self._steps.append(Step("Self-Attest & Submit", SUBMIT, attest))
        return [f for s in self._steps for f in s.fields]

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        """Answers each step and moves on with Next, up to the attestation, which it agrees to
        without Submit."""
        self._values, self._shown = {}, {}
        answers = {f.id: f.prefilled for s in self._steps for f in s.fields
                   if f.prefilled is not None} | answers  # fmt: skip
        for step in self._steps:
            _wait_for_step(page, step)
            before = {key for key, _ in page.evaluate(_CONTROLS)}
            for f in step.fields:
                if f.id in answers and f.id not in FIXED:
                    self._set(page, f, answers[f.id])
            self._shown |= dict.fromkeys(self._shown_on(page, step, before))
            self._values |= self._read(page, step)
            if step.marker == SUBMIT:
                return
            _next(page, step)

    def shown_fields(self, page: Page) -> list[str]:
        """The fields each step showed once answered: the wizard holds one step at a time."""
        return list(self._shown)

    def _shown_on(self, page: Page, step: Step, before: set[str]) -> list[str]:
        """The open step's fields: those with an enabled box, then the controls its answers
        added that no field has. What it showed before them is not all fields, such as a
        preferred name's box, so a control counts only as an answer's."""
        boxes = {name for box in self._boxes.values() for name in re.findall(r"[\w-]+", box)}
        shown = [f.id for f in step.fields if _enabled(page.locator(self._boxes[f.id]).first)]
        for key, dom in page.evaluate(_CONTROLS):
            listed = key in boxes or dom in boxes or NOT_FIELDS.fullmatch(key)
            if key and key not in before and not listed:
                shown.append(key)
        return shown

    def read_back(self, page: Page) -> dict[str, Answer]:
        """Each step's values as it was left."""
        return dict(self._values)

    def submit(self, page: Page) -> str | None:
        """Submit, on the attestation; the wizard then closes on a confirmation."""
        page.locator(SUBMIT).click()
        try:
            page.locator(SUBMIT).wait_for(state="detached", timeout=SUBMIT_TIMEOUT_MS)
        except Timeout:
            raise RuntimeError(f"Submit stayed on the attestation: {_errors(page)}") from None
        page.wait_for_timeout(SETTLE_MS)
        return f"Submit closed the wizard: {page.locator('body').inner_text()[:200]!r}"

    def _personal(self, page: Page, transforms: dict[str, Any]) -> list[Field]:
        """The step's boxes, required where their label carries the asterisk; the profile's
        values as prefilled."""
        fields = []
        for name, selector, type_ in PERSONAL:
            box = page.locator(selector)
            field_id = f"personal.{name}"
            if name in SHOWN_BY_COUNTRY and not box.count() and "personal.country" in self._boxes:
                self._boxes[field_id] = selector
                label = name.replace("_", " ").title()
                when = Is("personal.country", None)
                fields.append(Field(field_id, label, type_, False, shown_when=when))
                continue
            if not box.count():
                continue
            self._boxes[field_id] = selector
            label = LABELS.get(name) or _label(page, selector) or name.replace("_", " ").title()
            required = label.endswith("*") or box.first.get_attribute("aria-required") == "true"
            options = None
            if name == "country":
                options = [o for o in _react_options(page, box.first) if o]
            elif name == "state":
                options = _states(transforms)
            value = _value(page, selector, type_)
            fields.append(
                Field(
                    field_id, label.rstrip("*").strip(), type_, required, options,
                    prefilled=value or None,
                    max_length=ADDRESS_MAX if name.startswith("address_line") else None,
                )
            )  # fmt: skip
        return fields

    def _resume(self) -> Field:
        self._boxes["resume"] = "input[type=file]"
        return Field("resume", "Resume", FieldType.FILE, True, accept=[".pdf", ".doc", ".docx"])

    def _opt_out(self) -> Field:
        """The opt-out an employer's uploaded résumé shows, in no meta: listed on every form,
        its box left alone unless answered."""
        self._boxes[OPT_OUT] = "input[name=candidateProfileRelevanceOptionDivId]"
        label = (
            "I want to opt out from having my resume reviewed by artificial intelligence as "
            "part of the application process."
        )
        return Field(OPT_OUT, label, FieldType.CHOICE, False, YES_NO, default="No",
                     shown_when=Is("resume"))  # fmt: skip

    def _questions(self, control: dict[str, Any], transforms: dict[str, Any]) -> list[Field]:
        """The questionnaire, each question its `#question_<n>` by position; then the desired
        pay and the comment where the meta does not hide them."""
        fields = []
        questions = (control.get("questionnaire") or {}).get("questions") or []
        for n, q in enumerate(questions):
            field_id = f"question.{q['questionID']}"
            label = _text(q.get("questionText") or q["questionID"])
            required = bool(q.get("requiredIndicator"))
            options = [o.get("answerValue") or o["answerID"] for o in q.get("answerOptions") or []]
            box = f"#question_{n}"
            match q.get("answerTypeCode"):
                case "Yes/No Buttons":
                    box = f"sdf-radio-group:has(#questionYes_{n})"
                    f = Field(field_id, label, FieldType.CHOICE, required, YES_NO)
                case "Comment" | "Text Field":
                    f = Field(field_id, label, FieldType.TEXT, required)
                case "Number Field":
                    self._amounts.add(field_id)
                    f = Field(field_id, label, FieldType.TEXT, required)
                case "Drop List":
                    f = Field(field_id, label, FieldType.CHOICE, required, options)
                case "Multi-Select Menu":
                    f = Field(field_id, label, FieldType.MULTI_CHOICE, required, options)
                case "Date Field":
                    f = Field(field_id, label, FieldType.DATE, required)
                case "Money Field":  # an amount, then its currency
                    self._amounts.add(field_id)
                    f = Field(field_id, label, FieldType.TEXT, required)
                    currency = f"{field_id}.currency"
                    self._boxes[currency] = f"#question_currency_type_{n}"
                    fields += [f, Field(currency, f"{label} (currency)", FieldType.CHOICE,
                                        required, [_spaced(o) for o in options])]  # fmt: skip
                    self._boxes[field_id] = box
                    continue
                case other:
                    reason = f"unhandled_rendering: {label!r} is a {other} question"
                    f = Field(field_id, label, FieldType.TEXT, required, unfillable=reason)
            self._boxes[field_id] = box
            fields.append(f)
        pay = transforms.get(PAY) or {}
        if pay and not pay.get("hidden"):
            required = not pay.get("optional")
            rates = _codes(transforms, f"{PAY}/annualRateAmount/nameCode") or ["Annually", "Hourly"]
            currencies = [  # as the list shows them: `United States Dollar ( USD )`
                f"{i['longName']} ( {i['codeValue']} )"
                for i in _items(transforms, f"{PAY}/annualRateAmount/currencyCode")
            ]
            self._amounts.add("pay.amount")
            self._boxes |= {
                "pay.rate": "#salaryOptions sdf-radio-group",
                "pay.amount": "#desiredSalaryId",
                "pay.currency": "#add_info_select_box",
            }
            fields += [
                Field("pay.rate", "Desired salary: rate", FieldType.CHOICE, required, rates,
                      default="Annually"),
                Field("pay.amount", "What is your desired salary?", FieldType.TEXT, required),
                Field("pay.currency", "Desired salary: currency", FieldType.CHOICE, required,
                      currencies or None),
            ]  # fmt: skip
        referral = transforms.get("/jobApplication/applicantReferralIndicator") or {}
        if referral and not referral.get("hidden"):
            self._boxes |= {  # Yes shows who: an employee's name or a position ID
                "referral": "#refferalOptions:has(#yes)",
                "referral.by": "#refferalOptions:has(#employeeName)",
                "referral.who": "#add_info_text_box",
            }
            label = "Did any of our employees inform you about this position?"
            yes = Is("referral", ["Yes"])
            fields += [
                Field("referral", label, FieldType.CHOICE, not referral.get("optional"), YES_NO,
                      default="No"),
                Field("referral.by", "Please provide Employee Name or Position ID.",
                      FieldType.CHOICE, True, ["Employee Name", "Position ID"], shown_when=yes),
                Field("referral.who", "Employee name or position ID", FieldType.TEXT, True,
                      shown_when=yes),
            ]  # fmt: skip
        comment = transforms.get("/jobApplication/comment") or {}
        if comment and not comment.get("hidden"):
            self._boxes["comment"] = "#commentTextArea"
            label = "Do you want to tell us anything else about yourself?"
            fields.append(Field("comment", label, FieldType.TEXT, not comment.get("optional")))
        return fields

    def _self_id(self, transforms: dict[str, Any], indicators: dict[str, bool]) -> list[Field]:
        """The questions the employer's indicators enable, every answer optional: the step
        moves on without any."""
        fields = []
        for name, label, box, indicator, codes in SELF_ID:
            if not indicators.get(indicator):
                continue
            items = _items(transforms, f"{PERSON}/{codes}")
            if name == "veteran":  # the page offers three of the four: Y, N, D
                items = [i for i in items if i["codeValue"] != "X"]
            options = [i["longName"] for i in items]
            field_id = f"self_id.{name}"
            if box.startswith("sdf-radio-group"):
                self._codes[field_id] = {i["codeValue"]: i["longName"] for i in items}
            self._boxes[field_id] = box
            when = Is("self_id.ethnicity", ["Not Hispanic or Latino"]) if name == "race" else None
            fields.append(Field(field_id, label, FieldType.CHOICE, False, options, shown_when=when))
        return fields

    def _attestation(self, control: dict[str, Any]) -> Field:
        said = " ".join(_text(a.get("messageTxt") or "") for a in control.get("attestations") or [])
        label = "Yes, I agree to sign electronically." + (f" ({said[:300]}…)" if said else "")
        self._boxes["attestation"] = ATTEST
        return Field("attestation", label, FieldType.CHOICE, True, YES_NO)

    def _signature(self) -> Field:
        """The name agreeing shows beneath the box, in no meta; the page's own words."""
        self._boxes[SIGNATURE] = "#electronicSignature"
        return Field(SIGNATURE, "Please type your full name.", FieldType.TEXT, True,
                     shown_when=Is("attestation", ["Yes"]))  # fmt: skip

    def _set(self, page: Page, f: Field, answer: Answer) -> None:
        box = page.locator(self._boxes[f.id]).first
        if f.id in LATE:
            with suppress(Timeout):
                box.wait_for(state="attached", timeout=LATE[f.id])
        if not box.count():
            return  # not shown: its condition is unmet
        if f.type is FieldType.FILE:
            _upload(page, box, answer if isinstance(answer, str) else answer[0])
            return
        if isinstance(answer, list):
            for one in answer:
                _react_pick(page, box, f, one)
            return
        tag = box.evaluate("e => e.tagName")
        if box.locator("wfn-radio-button").count():
            box.locator(f'wfn-radio-button[value="{answer}"]').click()
        elif tag == "SDF-RADIO-GROUP":
            _radio(box, f, answer, self._codes.get(f.id, {}))
        elif tag == "SDF-SELECT-SIMPLE":
            _sdf_pick(page, box, f, answer)
        elif tag == "WFN-DROPDOWN-LIST":
            _wfn_pick(page, box, f, answer)
        elif box.get_attribute("type") == "checkbox":
            if box.is_checked() != (answer == "Yes"):  # the box is hidden: its label clicks
                page.locator(f'label[for="{box.get_attribute("id")}"]').click()
        elif box.get_attribute("role") == "combobox":
            _react_pick(page, box, f, answer)
        elif f.type is FieldType.PHONE:  # cleared, it keeps its country's code: `+1`
            box.fill("")
            kept = re.sub(r"\D", "", box.input_value())
            digits = re.sub(r"\D", "", answer)
            typed = digits.removeprefix(kept) if kept else answer
            box.press_sequentially(typed, delay=KEY_DELAY_MS)
        elif f.type is FieldType.DATE:
            box.fill("")
            typed = datetime.fromisoformat(answer).strftime(DATE)
            box.press_sequentially(typed, delay=KEY_DELAY_MS)
            box.press("Tab")
        else:
            box.fill(answer)
            if f.id == "personal.address_line1":  # a place search: its suggestions stay closed
                box.press("Escape")
        page.wait_for_timeout(SETTLE_MS if f.type is FieldType.CHOICE else 0)

    def _read(self, page: Page, step: Step) -> dict[str, Answer]:
        values: dict[str, Answer] = {}
        for f in step.fields:
            if f.id in FIXED and f.prefilled is not None:
                values[f.id] = f.prefilled
            elif not f.unfillable and (value := _value(page, self._boxes[f.id], f.type)):
                if isinstance(value, str) and f.id in self._codes:
                    value = self._codes[f.id].get(value, value)
                elif isinstance(value, str) and f.id in self._amounts:
                    value = _amount(value)
                elif isinstance(value, str) and f.id == "personal.state":
                    value = value.partition(" - ")[2] or value  # `DC - District of Columbia`
                values[f.id] = value
            elif f.id == "pay.currency" and _fixed_dollar(page):
                values[f.id] = next((o for o in f.options or [] if o.endswith("( USD )")), "USD")
        return values


# The open step's controls: what names each, and its id
_CONTROLS = """() => [...document.querySelectorAll(
  "input, textarea, select, sdf-select-simple, wfn-dropdown-list"
)].map(e => [e.name || e.id, e.id])"""
# The meta lists the questionnaire of the posting its header names
_FETCH = """async ([url, requisition]) => {
  const headers = {requisitionoid: requisition, 'x-requested-with': 'XMLHttpRequest'};
  const response = await fetch(url, {credentials: 'include', headers});
  if (!response.ok) throw new Error(`${url} answered ${response.status}`);
  return response.json();
}"""
# Signed out, the candidate profile's endpoint redirects to ADP's login page
_HOLDS = """url => fetch(url, {credentials: 'include', redirect: 'manual'}).then(r => r.ok)"""


def _posting(url: str) -> tuple[str, str, str] | None:
    """The employer (`cid`), its career center (`ccId`) and the posting (`jobId`)."""
    found = _career_center(url)
    job = parse_qs(urlsplit(url).query).get("jobId")
    return (*found, job[0]) if found and job else None


def _career_center(url: str) -> tuple[str, str] | None:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if parts.hostname != HOST or "/recruitment/" not in parts.path:
        return None
    if not (query.get("cid") and query.get("ccId")):
        return None
    return query["cid"][0], query["ccId"][0]


def _open(page: Page) -> None:
    """The posting signed in: Apply, or Complete Your Application on a draft, opens the wizard;
    a wizard opened past its first step goes back to it."""
    found = _posting(page.url)
    if found is None:
        raise RuntimeError(f"{page.url} is not an ADP Workforce Now posting")
    cid, cc_id, job = found
    query = urlencode({"cid": cid, "ccId": cc_id, "lang": "en_US", "jobId": job})
    page.goto(f"{SIGNED_IN}?{query}")
    apply = page.locator(
        "#recruitment_jobDescription_candidateApply:visible,"
        " #recruitment_jobDescription_complete:visible"
    )
    wizard = page.locator(NEXT)
    try:
        apply.or_(wizard).first.wait_for(timeout=PAGE_TIMEOUT_MS)
    except Timeout:
        if "login" in urlsplit(page.url).path:
            raise SignedOut("login_required: the posting sent to ADP's login") from None
        raise Unsupported("posting_gone: the posting shows no Apply") from None
    if not wizard.count():
        apply.first.click()
        wizard.wait_for(timeout=PAGE_TIMEOUT_MS)
    first = page.locator(PERSONAL[0][1])
    for _ in range(len(PERSONAL)):
        page.wait_for_timeout(SETTLE_MS)
        if first.count() or not page.locator(PREVIOUS).count():
            break
        page.locator(PREVIOUS).click()
    first.wait_for(timeout=STEP_TIMEOUT_MS)


def _transforms(meta: dict[str, Any]) -> dict[str, Any]:
    """Each profile path's `optional`, `hidden` and code list."""
    found: dict[str, Any] = {}
    for entry in (meta.get("meta") or {}).get("/data/transforms") or []:
        found |= entry
    return found


def _spaced(currency: str) -> str:
    """A currency as its list shows it: `United States Dollar ( USD )` for the meta's `(USD)`."""
    return re.sub(r"\((\w+)\)$", r"( \1 )", currency)


def _items(transforms: dict[str, Any], path: str) -> list[dict[str, str]]:
    items = ((transforms.get(path) or {}).get("codeList") or {}).get("listItems") or []
    return [i for i in items if i.get("longName")]


def _codes(transforms: dict[str, Any], path: str) -> list[str]:
    return [i["longName"] for i in _items(transforms, path)]


def _states(transforms: dict[str, Any]) -> list[str]:
    """The states by name: the list shows `IL - Illinois` once the country is picked, the
    meta `US-Illinois`."""
    return [
        re.sub(r"^[A-Z]{2}-", "", s)
        for s in _codes(transforms, f"{PERSON}/address/countrySubdivisionLevel1")
    ]


def _text(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def _label(page: Page, selector: str) -> str:
    """The text of the label for a box, by its id."""
    box_id = selector.removeprefix("#")
    label = page.locator(f'label[for="{box_id}"]')
    if label.count():
        return " ".join(label.first.inner_text().split())
    return ""


def _enabled(box: Locator) -> bool:
    """A box the step shows and takes an answer in: race stays disabled until ethnicity allows."""
    return box.count() > 0 and not box.evaluate("e => e.disabled === true")


def _wait_for_step(page: Page, step: Step) -> None:
    if not step.marker:
        page.wait_for_timeout(SETTLE_MS)
        return
    try:
        page.locator(step.marker).first.wait_for(state="attached", timeout=STEP_TIMEOUT_MS)
    except Timeout:
        raise RuntimeError(f"the wizard is not on {step.name}") from None


def _next(page: Page, step: Step) -> None:
    """Next, once the step is left; the page's errors where it stays."""
    page.locator(NEXT).click()
    deadline = time.monotonic() + STEP_TIMEOUT_MS / 1000
    while step.marker and page.locator(step.marker).first.count():
        if time.monotonic() > deadline:
            raise NotTaken(f"{step.name} did not move on: {_errors(page)}")
        page.wait_for_timeout(SETTLE_MS)
    page.wait_for_timeout(SETTLE_MS)


def _errors(page: Page) -> str:
    """Each highlighted field's error under it."""
    said = page.locator("[role=alert]:visible, [id$=errorMessage]:visible").all_inner_texts()
    return "; ".join(dict.fromkeys(t.strip() for t in said if t.strip())) or "no error shown"


def _radio(group: Locator, f: Field, answer: str, codes: dict[str, str]) -> None:
    """Clicks the radio whose label matches, or whose code does where the labels are the meta's."""
    code = next((c for c, label in codes.items() if label == answer), None)
    for radio in group.locator("sdf-radio-button").all():
        value = radio.get_attribute("value") or ""
        if value == code or matching.matches(f, answer, radio.get_attribute("label") or value):
            radio.click()
            return
    raise NotTaken(f"{f.id} has no option {answer!r}")


def _fixed_dollar(page: Page) -> bool:
    """Some employers show no currency list beside the desired pay, `$` before it instead."""
    box = page.locator("#desiredSalaryId")
    if not box.count() or page.locator("#add_info_select_box").count():
        return False
    return box.evaluate("e => e.parentElement.innerText.trim()") == "$"


def _amount(shown: str) -> str:
    """An amount as typed: the page shows `50,000.00` for 50000."""
    plain = shown.replace(",", "")
    return plain.removesuffix(".00") if re.fullmatch(r"-?\d+\.\d\d", plain) else plain


def _react_options(page: Page, box: Locator) -> list[str]:
    """A react-select's options, from its menu opened and closed again."""
    box.click()
    options = page.locator("[id*=-option-]:visible")
    with suppress(Timeout):
        options.first.wait_for(timeout=OPTION_TIMEOUT_MS)
    found = [" ".join(t.split()) for t in options.all_inner_texts()]
    box.press("Escape")
    return found


def _react_pick(page: Page, box: Locator, f: Field, answer: str) -> None:
    """Types the answer into the react-select and picks the option that matches."""
    box.click()
    box.press_sequentially(answer.partition(" (")[0], delay=KEY_DELAY_MS)  # the filter is plain
    options = page.locator("[id*=-option-]:visible")
    deadline = time.monotonic() + OPTION_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        for option in options.all():
            if matching.matches(f, answer, " ".join(option.inner_text().split())):
                option.click()
                return
        page.wait_for_timeout(SETTLE_MS)
    box.press("Escape")
    shown = [" ".join(t.split()) for t in options.all_inner_texts()][:5]
    raise NotTaken(f"{f.id} has no option {answer!r} among {shown}")


_ITEMS = "es => es.map(e => (e.getAttribute('aria-label') || e.innerText).trim())"


def _sdf_pick(page: Page, select: Locator, f: Field, answer: str) -> None:
    """Opens the list and picks the option that matches; a long list shows its first options
    only, and its filter box narrows it to the answer."""
    select.locator("[part=frame]").first.click()
    items = page.locator("sdf-select-item:visible")  # the open list's, in a floating pane
    narrowed = False
    texts: list[str] = []
    deadline = time.monotonic() + OPTION_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        texts = items.evaluate_all(_ITEMS)  # at once: the list re-renders as it fills
        found = next((n for n, t in enumerate(texts) if t and matching.matches(f, answer, t)), None)
        if found is not None:
            items.nth(found).click()
            return
        search = page.locator("input#select-filter-input:visible")
        if not narrowed and search.count():
            search.press_sequentially(answer, delay=KEY_DELAY_MS)
            narrowed = True
        page.wait_for_timeout(SETTLE_MS)
    page.keyboard.press("Escape")
    raise NotTaken(f"{f.id} has no option {answer!r} among {texts[:5]}")


def _wfn_pick(page: Page, dropdown: Locator, f: Field, answer: str) -> None:
    dropdown.click()
    options = page.locator("[role=option]:visible")
    deadline = time.monotonic() + OPTION_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        for option in options.all():
            text = " ".join(option.inner_text().split())
            name = text.partition(" - ")[2] or text  # `IL - Illinois`
            if matching.matches(f, answer, text) or matching.matches(f, answer, name):
                option.click()
                return
        page.wait_for_timeout(SETTLE_MS)
    page.keyboard.press("Escape")
    raise NotTaken(f"{f.id} has no option {answer!r}")


def _upload(page: Page, box: Locator, path: str) -> None:
    """The résumé's file input; the step then lists the file by name."""
    box.set_input_files(path)
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    try:
        page.get_by_text(name).first.wait_for(timeout=UPLOAD_TIMEOUT_MS)
    except Timeout:
        raise NotTaken(f"resume did not take {name}: {_errors(page)}") from None


_VALUE = """e => {
  const pair = e.querySelector('wfn-radio-button');  // each holds the group's value
  if (pair) return pair.getAttribute('radio-group-value') || null;
  if (e.tagName === 'SDF-RADIO-GROUP') {
    const on = e.querySelector('sdf-radio-button[aria-checked="true"]');
    return on ? on.getAttribute('value') : null;
  }
  const select = e.tagName === 'WFN-DROPDOWN-LIST'  // a list around a select of its own
    ? e.shadowRoot?.querySelector('sdf-select-simple') : e;
  if (select?.tagName === 'SDF-SELECT-SIMPLE')
    return (select.shadowRoot ?? select).querySelector('#selected-label')?.textContent.trim()
      || null;
  if (e.type === 'checkbox') return e.checked ? 'Yes' : 'No';
  if (e.getAttribute('role') === 'combobox') {
    const box = e.closest('[class*="value-container"]') ?? e.parentElement.parentElement;
    const many = [...box.querySelectorAll('[class*="multi-value__label"]')];
    if (many.length) return many.map(m => m.textContent.trim());
    return box.querySelector('[class*="single-value"]')?.textContent.trim() || null;
  }
  return e.value || null;
}"""


def _value(page: Page, selector: str, type_: FieldType) -> Answer | None:
    box = page.locator(selector).first
    if not box.count():
        return None
    if type_ is FieldType.FILE:
        names = page.locator("text=/\\.(pdf|docx?)$/i").all_inner_texts()
        return names[0].strip() if names else None
    value = box.evaluate(_VALUE)
    if type_ is FieldType.PHONE and isinstance(value, str):
        digits = re.sub(r"\D", "", value)
        return f"+{digits}" if len(digits) > 3 else None  # a bare country code is empty
    if type_ is FieldType.DATE and isinstance(value, str) and value:
        with suppress(ValueError):
            return datetime.strptime(value, DATE).date().isoformat()
    return value


def _sign_in(page: Page, email: str, details: SignUpDetails | None) -> None:
    """Sign In's email, then the emailed code. An email the employer does not know is asked to
    join its talent community first: name and mobile number."""
    box = page.locator("#guestEmail")
    _consented(page, box)
    box.fill(email)
    page.get_by_role("button", name="Continue").first.click()
    first = page.locator("#guestFirstName")
    _code_step(page).or_(first).first.wait_for(timeout=STEP_TIMEOUT_MS)
    if first.count() and first.is_visible():
        if details is None:
            raise Refused("no_account")
        _join(page, email, details)
    _verify(page, email)


def _join(page: Page, email: str, details: SignUpDetails) -> None:
    assert details.name and details.phone  # in `asks`: the core gives them
    given, _, family = details.name.rpartition(" ")
    page.locator("#guestFirstName").fill(given or family)
    page.locator("#guestLastName").fill(family)
    page.locator("#guestEmail").fill(email)
    phone = page.locator("#login_view_phone")
    phone.fill("")
    phone.press_sequentially(details.phone, delay=KEY_DELAY_MS)  # E.164 picks the country
    page.get_by_role("button", name="Continue").first.click()
    _code_step(page).first.wait_for(timeout=STEP_TIMEOUT_MS)


def _consented(page: Page, step: Locator) -> None:
    """Some employers show a privacy notice before the step: Consent & Continue."""
    consent = page.get_by_text("Consent & Continue", exact=True)
    try:
        step.or_(consent).first.wait_for(timeout=PAGE_TIMEOUT_MS)
    except Timeout:
        raise Unsupported(f"unhandled_rendering: no ADP sign-in on {page.url}") from None
    if consent.count() and consent.first.is_visible():
        consent.first.click()
        step.wait_for(timeout=STEP_TIMEOUT_MS)


def _code_step(page: Page) -> Locator:
    return page.locator("#recruitment_login_guestLogin, #oneTimePassWord")


def _verify(page: Page, email: str) -> None:
    """ "Get your verification code": the email, Send, the code, Verify; signed in, the career
    center moves to its signed-in address, a moment before the session holds."""
    send = page.locator("#recruitment_login_guestLogin")
    if send.count() and send.is_visible():
        option = page.locator("#emailIdentifierId")
        if option.count():
            option.click(force=True)
        send.click()
    code = page.locator("#oneTimePassWord")
    code.wait_for(timeout=STEP_TIMEOUT_MS)
    otp.enter(code, email, "ADP's verification code")
    page.locator("#recruitment_login_verifyOTP").click()
    deadline = time.monotonic() + SIGNED_IN_TIMEOUT_MS / 1000
    while not page.url.startswith(SIGNED_IN):
        if time.monotonic() > deadline:
            raise Blocked(f"otp_rejected: ADP's verification code sent to {email}")
        page.wait_for_timeout(SETTLE_MS)
    cid = parse_qs(urlsplit(page.url).query)["cid"][0]
    while not page.evaluate(_HOLDS, f"{APPLICANTS}?cid={cid}"):
        if time.monotonic() > deadline:
            raise Blocked(f"otp_rejected: ADP's verification code sent to {email}")
        page.wait_for_timeout(SETTLE_MS)
