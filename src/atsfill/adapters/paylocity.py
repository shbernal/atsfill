"""Paylocity Recruiting (`recruiting.paylocity.com/Recruiting/Jobs/...`), as
docs/adapters/paylocity.md observes.

The apply page carries the whole form's configuration in `window.pageData`: the sections and
their fields, the screener questions and the option lists. `read_api` fetches it over HTTP,
`read_form` takes it from the page, and both list the fields from it. The form is one page or
a wizard of steps that render one at a time, so `fill` keeps each step's read-back before
moving on; nothing reaches the employer before Submit.
"""

import json
import re
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from atsfill.core.adapter import Gap, Unsupported, known_gap
from atsfill.core.browser import HTTP_TIMEOUT_S, Locator, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOST = "recruiting.paylocity.com"
JOB = re.compile(r"^/Recruiting/Jobs/(?:Details|Apply)/(\d+)", re.IGNORECASE)
APPLY = f"https://{HOST}/Recruiting/Jobs/Apply/"
PAGE_DATA = "window.pageData ="
NEXT = "[data-automation-id=btnNext]"
SUBMIT = "[data-automation-id=btnSubmit]"
STEP = "#appDetailDiv .type-bold.float-right"  # "Step 2 of 5"
FORCED_RESUME = ".modal.in #btn-forceResume"
FORM_TIMEOUT_MS = 30_000
COOKIE_TIMEOUT_MS = 5_000
STEP_TIMEOUT_MS = 20_000
UPLOAD_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
STATE_TIMEOUT_MS = 5_000
RENDER_MS = 500  # for a new step to render its controls
KEY_DELAY_MS = 30
ADDRESS_KEY_DELAY_MS = 80  # its suggestion list drops keys typed while it loads
STATE_KEY_DELAY_MS = 40
TAG_KEY_DELAY_MS = 20
YES_NO = ["Yes", "No"]
ADDRESS = "public-site-address"  # an address block: its inputs' ids start with this
COMPANY = "public-candidate-work-history-address-0"


@dataclass(frozen=True)
class Spec:
    """One control a pageData field renders as. `required` None takes the field's own."""

    dom: str  # the element id; a radio group's name; `{n}` for an entry's index
    label: str
    kind: str  # a key of TYPES
    required: bool | None = None
    options: str | list[str] | None = None  # a list, or the pageData key of one
    when: tuple[str, str] | None = None  # asked only when (control, answer)
    max_length: int | None = None


def _yes_no(dom: str, label: str) -> Spec:
    return Spec(dom, label, "dropdown", options=YES_NO)


WORKED_BEFORE = ("info.haveYouWorkedWithUsBefore", "Yes")
INFO: dict[str, list[Spec]] = {
    "name": [
        Spec("info.firstName", "First Name", "text", True),
        Spec("info.lastName", "Last Name", "text", True),
        Spec("info.middleName", "Middle Name", "text", False),
        Spec("info.preferredName", "Preferred First Name", "text", False),
    ],
    "emailAddress": [Spec("info.email", "Email Address", "text")],
    "cellPhone": [Spec("info.cellPhone", "Mobile Number", "phone")],
    "homePhone": [Spec("info.phone", "Home Phone Number", "phone")],
    "smsOptedIn": [_yes_no("info.smsOptedIn", "")],
    "appliedBefore": [_yes_no("info.haveYouAppliedWithUsBefore", "")],
    "workedHereBefore": [
        _yes_no(WORKED_BEFORE[0], ""),
        Spec("info.priorEmploymentPositionHeld", "Position", "text", False, when=WORKED_BEFORE),
        Spec("info.priorEmploymentStartDate", "Start Date", "date", False, when=WORKED_BEFORE),
        Spec("info.priorEmploymentEndDate", "End Date", "date", False, when=WORKED_BEFORE),
    ],
    "address": [
        Spec(f"{ADDRESS}-address-1", "Address Line 1", "address", max_length=50),
        Spec(f"{ADDRESS}-county", "County", "text", max_length=50),
    ],
    "address2": [Spec(f"{ADDRESS}-address-2", "Address Line 2", "text", max_length=50)],
    "city": [Spec(f"{ADDRESS}-city", "City", "text", max_length=50)],
    "state": [Spec(f"{ADDRESS}-us-state", "State", "state", options="statesOptions")],
    "postalCode": [Spec(f"{ADDRESS}-zip", "Zip Code", "text", max_length=20)],
    "dateAvailable": [Spec("info.dateAvailableToStart", "Available to Start", "date")],
    "linkedInUrl": [Spec("info.linkedIn", "LinkedIn Profile URL", "text")],
    "howDidYouHear": [Spec("info.howDidYouHearAboutUs", "", "radio", options="values")],
    "referredBy": [Spec("info.referredBy", "Referred by", "text")],
    "skills": [Spec("info.skills", "Skills", "tags")],
    "uploadCoverLetter": [Spec("btn-coverLetter", "Cover Letter", "file")],
    "uploadAdditionalFiles": [Spec("btn-additionalFiles", "Additional Files", "file")],
}
SALARY = {  # by desiredSalaryInputType
    1: [
        Spec(
            "info.desiredSalaryType",
            "Desired Salary Type",
            "dropdown",
            options="desiredSalaryTypeOptions",
        ),  # fmt: skip
        Spec("info.minimumDesiredSalary", "Desired Salary (minimum)", "text"),
        Spec("info.maximumDesiredSalary", "Desired Salary (maximum)", "text"),
    ],
    2: [Spec("info.desiredSalaryDescription", "Desired Salary", "text")],
}
# Fields that are no answer of their own: the acknowledgement (ACKNOWLEDGE, always added), the
# E-Verify message, the switch that shows the conviction questions.
NOT_ANSWERS = {"acknowledgement", "eVerify", "priorConviction"}
GAPS = {  # pageData field name: the known gap it is
    "country": "country",
    **dict.fromkeys(
        ("dni", "ethnicity", "genderIdentity", "personalPronoun", "sexualOrientation"),
        "expanded_identity",
    ),
    "dateOfBirth": "date_of_birth",
    "startEndSalary": "salary_history",
    "referenceAddress": "reference_address",
    "gpa": "gpa",
}
CONTACT = ("workHistory.mayWeContactSupervisor.0", "Yes")
GRADUATED = ("educationHistory.didYouGraduate.0", "Yes")
WORK: dict[str, list[Spec]] = {
    "companyName": [Spec("workHistory.companyName.0", "Company Name", "text")],
    "position": [Spec("workHistory.position.0", "Position", "text")],
    "companyPhone": [Spec("workHistory.companyPhone.0", "Company Phone Number", "phone")],
    "responsibilities": [Spec("workHistory.responsibilities.0", "Responsibilities", "text")],
    "startEndDate": [
        Spec("txt-workHistory-startDate-0", "Start Date (MM/YYYY)", "month"),
        Spec("txt-workHistory-endDate-0", "End Date (MM/YYYY)", "month"),
    ],
    "reasonForLeaving": [Spec("workHistory.reasonForLeaving.0", "Reason for Leaving", "text")],
    "companyLocation": [
        Spec(f"{COMPANY}-address-1", "Company Address Line 1", "address", max_length=50),
        Spec(f"{COMPANY}-address-2", "Company Address Line 2", "text", False, max_length=50),
        Spec(f"{COMPANY}-city", "Company City", "text", max_length=50),
        Spec(f"{COMPANY}-county", "Company County", "text", max_length=50),
        Spec(f"{COMPANY}-us-state", "Company State", "state", options="statesOptions"),
        Spec(f"{COMPANY}-zip", "Company Zip Code", "text", max_length=20),
    ],
    "currentlyWorkingHere": [
        Spec(
            "workHistory.currentlyWorkingHere.0",
            "Currently Working Here",
            "check",
            False,
            ["No", "Yes"],
        )  # fmt: skip
    ],
    "mayWeContact": [_yes_no(CONTACT[0], "May We Contact for Reference?")],
    "contactSupervisorName": [
        Spec("workHistory.supervisorName.0", "Supervisor Name", "text", False, when=CONTACT)
    ],
    "contactSupervisorPosition": [
        Spec("workHistory.supervisorPosition.0", "Supervisor Position", "text", False, when=CONTACT)
    ],
    "contactSupervisorPhone": [
        Spec(
            "workHistory.supervisorPhone.0", "Supervisor Phone Number", "phone", False, when=CONTACT
        )
    ],  # fmt: skip
    "contactSupervisorEmail": [
        Spec(
            "workHistory.supervisorEmail.0", "Supervisor Email Address", "text", False, when=CONTACT
        )
    ],  # fmt: skip
}
EDUCATION: dict[str, list[Spec]] = {
    "certsOrAwards": [
        Spec("educationHistory.certificationsAndAwards", "Certifications & Awards", "tags")
    ],
    "schoolName": [Spec("educationHistory.name.0", "School Name", "text")],
    "areaOfStudy": [Spec("educationHistory.areaOfStudy.0", "Area of Study", "text")],
    "didYouGraduate": [
        Spec(GRADUATED[0], "Did you Graduate?", "dropdown", options="graduationOptions")
    ],
    "degreeObtained": [
        Spec(
            "educationHistory.degreeId.0",
            "Degree Obtained",
            "dropdown",
            False,
            "degreeOptions",
            GRADUATED,
        )
    ],  # fmt: skip
    "schoolType": [
        Spec("educationHistory.type.0", "School Type", "dropdown", options="schoolTypeOptions")
    ],
    "schoolLocation": [
        Spec("educationHistory.city.0", "School City", "text"),
        Spec("educationHistory.state.0", "School State", "dropdown", options="statesOptions"),
    ],
}
REFERENCE: dict[str, list[Spec]] = {
    "referenceName": [Spec("references.name.{n}", "Name", "text")],
    "referenceEmail": [Spec("references.email.{n}", "Email Address", "text")],
    "referencePhoneNumber": [Spec("references.phone.{n}", "Phone Number", "phone")],
    "referencePersonalOrWork": [
        Spec(
            "references.referenceType.{n}",
            "Personal or Work Reference?",
            "dropdown",
            options="referenceTypeOptions",
        )
    ],  # fmt: skip
    "referenceYearsKnown": [
        Spec(
            "references.yearsKnown.{n}",
            "Years Known",
            "dropdown",
            options=["1 year", *(f"{n} years" for n in range(2, 6))],
        )
    ],  # fmt: skip
}
ACKNOWLEDGEMENTS: dict[str, list[Spec]] = {
    "eeoGenderEthnicity": [
        Spec("acknowledgements.eeoGender", "Gender", "dropdown", options="genderOptions"),
        Spec(
            "acknowledgements.racialOrEthnicGroup",
            "Race and/or Ethnicity",
            "dropdown",
            options="raceOptions",
        ),
    ],  # fmt: skip
    "ofccp": [
        Spec(
            "acknowledgements.militaryService",
            "Veteran Status",
            "dropdown",
            options="militaryServiceOptions",
        )
    ],  # fmt: skip
    "eeoDisability": [
        Spec(
            "acknowledgements.disability",
            "Disability Status",
            "dropdown",
            options="disabilityOptions",
        )
    ],  # fmt: skip
    "priorFelony": [_yes_no("acknowledgements.priorFelony", "")],
    "priorMisdemeanor": [_yes_no("acknowledgements.priorMisdemeanor", "")],
    "authorizedToWork": [_yes_no("acknowledgements.authorizedToWorkInUs", "")],
}
# Fields shown only when the form also has another, which switches them on.
SHOWN_WITH = {
    "priorFelony": "priorConviction",
    "priorMisdemeanor": "priorConviction",
    **{f"contactSupervisor{k}": "mayWeContact" for k in ("Name", "Position", "Phone", "Email")},
}
SECTIONS = {
    "info": INFO,
    "workHistory": WORK,
    "educationHistory": EDUCATION,
    "references": REFERENCE,
    "acknowledgements": ACKNOWLEDGEMENTS,
    "expandedIdentityQuestions": {},
}
ENTRIES = ("workHistory", "educationHistory")  # one entry shown when required, none else
ENTRY = re.compile(r"[.-]0(-|$)")  # an entry's control, by its index
RESUME = Spec("btn-resume", "Resume", "file")
ACKNOWLEDGE = Spec("applyAcknowledgement", "I have read and accept the above acknowledgement",
                   "check", True, YES_NO)  # fmt: skip
FILE_IDS = {"btn-resume": "resume", "btn-coverLetter": "coverLetter",
            "btn-additionalFiles": "additionalFiles"}  # fmt: skip
TYPES = {
    "text": FieldType.TEXT,
    "address": FieldType.TEXT,
    "month": FieldType.TEXT,
    "tags": FieldType.TEXT,
    "phone": FieldType.PHONE,
    "date": FieldType.DATE,
    "dropdown": FieldType.CHOICE,
    "state": FieldType.CHOICE,
    "radio": FieldType.CHOICE,
    "check": FieldType.CHOICE,
    "screener": FieldType.CHOICE,
    "screener_multi": FieldType.MULTI_CHOICE,
    "screener_text": FieldType.TEXT,
    "file": FieldType.FILE,
}


@dataclass(frozen=True)
class Control:
    kind: str
    dom: str
    options: list[str] | None = None


class Paylocity:
    name = "paylocity"
    known_gaps = dict.fromkeys(GAPS.values(), Gap.RENDERING)
    side_effects = [
        SideEffect("upload", "hiring_system", "an attached file is stored by Paylocity at once"),
        SideEffect("fill", "hiring_system", "each step's answers go to Paylocity for validation"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]  # fmt: skip

    def __init__(self) -> None:
        self._form: list[tuple[Field, Control]] = []
        self._values: dict[str, Answer] = {}  # the read-back of the steps left behind

    def detect(self, url: str, page: Page | None) -> bool:
        return _job(url) is not None

    def posting(self, url: str) -> str:
        job = _job(url)
        return f"https://{HOST}/Recruiting/Jobs/Details/{job}" if job else url

    def read_api(self, url: str) -> list[Field] | None:
        if (job := _job(url)) is None:
            return None
        request = urllib.request.Request(APPLY + job, headers={"User-Agent": "atsfill"})
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
                html = response.read().decode()
            return [f for f, _ in _form(_page_data(html))]
        except OSError, ValueError, KeyError, TypeError, Unsupported:
            return None

    def read_form(self, page: Page) -> list[Field]:
        job = _job(page.url)
        if job and "/apply/" not in page.url.lower():
            page.goto(APPLY + job)
        try:
            page.locator(f"{NEXT}, {SUBMIT}").first.wait_for(
                state="attached", timeout=FORM_TIMEOUT_MS
            )
            form = _form(_page_data(page.content()))
        except Timeout, ValueError, KeyError:
            raise Unsupported("posting_gone: no application form on the page") from None
        self._form = [(f, c) for f, c in form if not f.unfillable]
        return [f for f, _ in form]

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        self._values = {}
        page.locator("form, #app").first.wait_for(timeout=FORM_TIMEOUT_MS)
        _dismiss_cookies(page)
        if "resume" in answers:  # first: a parsed resume fills fields the answers then override
            _upload_resume(page, _one(answers["resume"]))
        while True:
            for field, control in self._form:
                if field.id in answers and field.id != "resume" and _present(page, control):
                    _fill(page, control, answers[field.id])
            self._values.update(self._read(page))
            if not page.locator(NEXT).count() or not _next(page):
                return

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = {**self._values, **self._read(page)}
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        before = page.url
        page.locator(SUBMIT).click()
        page.wait_for_url(lambda url: url != before, timeout=SUBMIT_TIMEOUT_MS)
        return page.url

    def _read(self, page: Page) -> dict[str, Answer]:
        values: dict[str, Answer] = {}
        for field, control in self._form:
            if _present(page, control) and (value := _read(page, control)) is not None:
                values[field.id] = value
        return values


def _job(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.hostname != HOST or not (match := JOB.match(parts.path)):
        return None
    return match[1]


def _page_data(html: str) -> dict[str, Any]:
    """The apply page's `window.pageData`, from its inline script."""
    start = html.index(PAGE_DATA) + len(PAGE_DATA)
    return json.JSONDecoder().raw_decode(html[start:].lstrip())[0]


def _form(data: dict[str, Any]) -> list[tuple[Field, Control]]:
    """The fields and their controls, in the page's order, from the apply page's pageData."""
    custom = data["customJobApplication"]
    out: list[tuple[Field, Control]] = []
    labels: dict[str, str] = {}

    def add(spec: Spec, field: dict[str, Any], prefix: str = "", n: int = 0) -> None:
        dom = spec.dom.format(n=n)
        options = spec.options
        if options == "values":
            options = [v["value"] for v in field["values"]]
        elif isinstance(options, str):
            listed = [o for o in data.get(options) or [] if o.get("text")]
            options = [o["value" if spec.kind == "state" else "text"] for o in listed]
        label = prefix + (spec.label or _plain(field["displayName"]))
        required = field["isRequired"] if spec.required is None else spec.required
        if spec.when:
            trigger, value = spec.when
            label += f" (asked when {labels.get(trigger, trigger)!r} is {value})"
        labels[dom] = label
        field_id = FILE_IDS.get(dom, dom)
        out.append((Field(field_id, label, TYPES[spec.kind], bool(required), options,
                          spec.max_length),
                    Control(spec.kind, dom, options)))  # fmt: skip

    add(RESUME, {"isRequired": bool(custom.get("requireResume") or data.get("requireResume"))})
    for section in custom["sections"]:
        if not section["isIncluded"]:
            continue
        name = section["name"]
        entries = name not in ENTRIES or section["isOneRequired"]
        count = 1
        if name == "references":
            if not custom.get("referencesRequired"):
                continue
            count = custom.get("referencesRequiredCount") or 1
        vocabulary = SECTIONS.get(name)
        included = {f["name"] for f in section["fields"] if f["isIncluded"]}
        for n in range(count):
            prefix = f"Reference {n + 1}: " if name == "references" else ""
            for field in section["fields"]:
                if not field["isIncluded"]:
                    continue
                specs = (vocabulary or {}).get(field["name"])
                if field["name"] == "desiredSalary":
                    specs = SALARY.get(custom.get("desiredSalaryInputType"))
                if specs is None:
                    if entries and field["name"] not in NOT_ANSWERS:
                        out.append(_unfillable(name, field, prefix, n, vocabulary is None))
                    continue
                if SHOWN_WITH.get(field["name"], field["name"]) not in included:
                    continue
                for spec in specs:
                    if entries or not ENTRY.search(spec.dom):  # the section's own fields show
                        add(spec, field, prefix, n)
    screener = data.get("screener") or {}
    for i, question in enumerate(screener.get("questions") or []):
        kind = question.get("questionType")
        answers = [a["title"] for a in question.get("answers") or []]
        if kind == "multi":
            kind = "screener_multi" if question.get("allowsMultipleResponses") else "screener"
            dom = f"multiQuestionAnswer{question['screenerQuestionId']}"
        elif kind == "text":
            kind, answers, dom = "screener_text", None, f"screener-question-{i}-textarea"
        else:
            field_id = f"screener.{question['screenerQuestionId']}"
            reason = f"unhandled_rendering: {field_id} is a {kind} screener question"
            field = Field(field_id, _plain(question["title"]), FieldType.TEXT,
                          bool(question.get("isRequired")), unfillable=reason)  # fmt: skip
            out.append((field, Control("unfillable", field_id)))
            continue
        field_id = f"screener.{question['screenerQuestionId']}"
        out.append((Field(field_id, _plain(question["title"]), TYPES[kind],
                          bool(question.get("isRequired")), answers),
                    Control(kind, dom, answers)))  # fmt: skip
    add(ACKNOWLEDGE, {"isRequired": True})
    return out


def _unfillable(
    section: str, field: dict[str, Any], prefix: str, n: int, unknown_section: bool
) -> tuple[Field, Control]:
    """A field of a shown section the adapter does not fill: a known gap, else unhandled."""
    name = field["name"]
    field_id = f"{section}.{name}" + (f".{n}" if section == "references" else "")
    label = prefix + _plain(field["displayName"])
    if (gap := GAPS.get(name)) and not unknown_section:
        reason = known_gap(Paylocity(), gap, f"{field_id} is {label!r}")
    else:
        where = (
            f"in an unknown section {section!r}" if unknown_section else "a field it does not know"
        )
        reason = f"unhandled_rendering: {field_id} is {where}"
    field_ = Field(field_id, label, FieldType.TEXT, bool(field["isRequired"]), unfillable=reason)
    return field_, Control("unfillable", field_id)


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _one(answer: Answer) -> str:
    return answer if isinstance(answer, str) else answer[0]


def _by_id(page: Page, dom: str) -> Locator:
    """The control: the last step reviews the others' answers in elements with their ids."""
    return page.locator(f"input[id='{dom}'], textarea[id='{dom}'], .rw-dropdownlist[id='{dom}']")


def _present(page: Page, control: Control) -> bool:
    match control.kind:
        case "radio":
            return page.locator(f"input[name='{control.dom}']").count() > 0
        case "screener" | "screener_multi":
            label = page.locator(f"label[for='{control.dom}_0']")
            return label.count() > 0 and label.first.is_visible()
        case "file":
            return _by_id(page, control.dom).count() > 0 or (
                control.dom == "btn-resume" and page.locator("#remove-resume").count() > 0
            )
        case "state":
            return page.locator(f"#{control.dom}-select-wrapper").count() > 0
        case _:
            target = _by_id(page, control.dom)
            return target.count() == 1 and target.is_visible()


def _dismiss_cookies(page: Page) -> None:
    close = page.locator("#onetrust-banner-sdk .onetrust-close-btn-handler")
    with suppress(Timeout):
        close.first.wait_for(state="visible", timeout=COOKIE_TIMEOUT_MS)
        close.first.click()


def _upload_resume(page: Page, path: str) -> None:
    forced = page.locator(FORCED_RESUME)
    if forced.count():
        target = forced
    else:
        parse = page.locator("#useAttachedResumeToFillOutApplication")
        if parse.count() and parse.is_checked():
            parse.uncheck()
        target = page.locator("#btn-resume")
        if not target.count():
            return
    target.set_input_files(path)
    with suppress(Timeout):
        page.locator("#remove-resume").wait_for(timeout=UPLOAD_TIMEOUT_MS)
    page.locator(".modal.in").wait_for(state="detached", timeout=UPLOAD_TIMEOUT_MS)


def _next(page: Page) -> bool:
    """Goes on to the next step; False when the page stays (an answer it does not accept)."""
    step = page.locator(STEP)
    before = step.inner_text() if step.count() else None
    page.locator(NEXT).click()
    try:
        page.wait_for_function(
            """before => {
                const s = document.querySelector("#appDetailDiv .type-bold.float-right");
                return (s ? s.textContent : null) !== before;
            }""",
            arg=before,
            timeout=STEP_TIMEOUT_MS,
        )
    except Timeout:
        return False
    page.wait_for_timeout(RENDER_MS)
    return True


def _pick(page: Page, dom: str, value: str) -> None:
    """A react-widgets dropdown: its list opens on a click."""
    _by_id(page, dom).click()
    options = page.locator(f"[id='{dom}__listbox'] [role=option]")
    for n in range(options.count()):
        if options.nth(n).inner_text().strip().rstrip("*") == value:
            options.nth(n).click()
            return
    page.keyboard.press("Escape")


def _fill(page: Page, control: Control, answer: Answer) -> None:
    value = _one(answer)
    target = _by_id(page, control.dom)
    match control.kind:
        case "text" | "phone" | "screener_text":
            target.fill(value)
            target.blur()
        case "address":  # a suggestion list that drops keys typed while it loads
            target.click()
            target.fill("")
            target.press_sequentially(value, delay=ADDRESS_KEY_DELAY_MS)
            page.keyboard.press("Escape")
            if target.input_value() != value:
                target.fill(value)
            target.blur()
        case "date":
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                y, m, d = value.split("-")
                target.click()
                target.press_sequentially(f"{m}/{d}/{y}", delay=KEY_DELAY_MS)
                target.blur()
        case "month":
            target.click()
            target.press_sequentially(value, delay=KEY_DELAY_MS)
            target.blur()
        case "dropdown":
            _pick(page, control.dom, value)
        case "state":
            page.locator(f"#{control.dom}-select-wrapper").click()
            page.keyboard.type(value, delay=STATE_KEY_DELAY_MS)
            item = page.locator(
                f"#{control.dom}-dropdown-list-container [role=listitem][title='{value}']"
            )
            try:
                item.first.click(timeout=STATE_TIMEOUT_MS)
            except Timeout:
                page.keyboard.press("Escape")
        case "radio":
            page.locator(f"input[name='{control.dom}'][value='{value}']").check(force=True)
        case "check":
            if target.is_checked() != (value == "Yes"):
                target.check(force=True) if value == "Yes" else target.uncheck(force=True)
        case "tags":  # takes typed keys only
            target.click()
            target.press_sequentially(value, delay=TAG_KEY_DELAY_MS)
            target.press("Enter")
        case "file":
            target.set_input_files(answer if isinstance(answer, list) else value)
            listed = target.locator("xpath=..").locator("tbody td span")
            with suppress(Timeout):
                listed.filter(has_text=Path(value).name).first.wait_for(timeout=UPLOAD_TIMEOUT_MS)
        case "screener" | "screener_multi":
            values = [answer] if isinstance(answer, str) else answer
            for n, option in enumerate(control.options or []):
                box = page.locator(f"[id='{control.dom}_{n}']")
                if (option in values) != box.is_checked():
                    page.locator(f"label[for='{control.dom}_{n}']").click()


def _read(page: Page, control: Control) -> Answer | None:
    target = _by_id(page, control.dom)
    match control.kind:
        case "date":  # a native date input, else a masked MM/DD/YYYY one
            value = target.input_value()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return value
            digits = re.findall(r"\d+", value)
            if len(digits) != 3:
                return None
            m, d, y = (int(x) for x in digits)
            with suppress(ValueError):
                return date(y, m, d).isoformat()
            return None
        case "dropdown":
            text = target.locator(".rw-input").first.inner_text().strip().rstrip("*")
            return "" if text == "--" else text
        case "state":
            text = page.locator(f"#{control.dom}-select-wrapper .input-select-input-single-value")
            value = text.first.inner_text().strip() if text.count() else ""
            return value if value in (control.options or []) else ""
        case "radio":
            checked = page.locator(f"input[name='{control.dom}']:checked")
            return checked.first.get_attribute("value") if checked.count() else ""
        case "check":
            return "Yes" if target.is_checked() else "No"
        case "tags":
            tags = target.locator("xpath=..").locator(".react-tagsinput-tag")
            return tags.first.inner_text().strip() if tags.count() else ""
        case "file":
            if control.dom == "btn-resume":
                name = page.locator(".rounded-box:has(#remove-resume) .no-wrap")
                return name.first.inner_text().strip() if name.count() else ""
            names = target.locator("xpath=..").locator("tbody td span")  # the files listed
            return names.first.inner_text().strip() if names.count() else ""
        case "screener" | "screener_multi":
            picked = [
                option
                for n, option in enumerate(control.options or [])
                if page.locator(f"[id='{control.dom}_{n}']").is_checked()
            ]
            if control.kind == "screener_multi":
                return picked
            return picked[0] if picked else ""
        case _:
            return target.input_value()
