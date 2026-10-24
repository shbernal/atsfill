"""Hireology (`careers.hireology.com/<careers site>/<job>/description`), as
docs/adapters/hireology.md observes.

The job page renders its application form from a public form API, which lists every field by
id, type and requirement. `read_api` fetches it over HTTP, `read_form` through the page's own
browser context, and both list the fields from it. Each field renders as one control whose id is
`<field id>-<n>`; nothing but the submit reaches Hireology.
"""

import json
import re
import urllib.request
from contextlib import suppress
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser
from atsfill.core.adapter import Gap, Unsupported
from atsfill.core.browser import HTTP_TIMEOUT_S, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, Is, SideEffect

HOST = "careers.hireology.com"
JOB = re.compile(r"^/[^/]+/(\d+)(?:/|$)")
API = "https://api.hireology.com/v2/public/application_forms/{job}"
APPLIED = re.compile(r"^https://api\.hireology\.com/v2/public/")
FORM = "#application"
SUBMIT = f"{FORM} #submit"
FORM_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
SHOWN_TIMEOUT_MS = 2_000
YES_NO = ["Yes", "No"]
FILE_TYPES = [".pdf", ".doc", ".docx"]
MONTH = " (MM/YYYY)"

# The page's labels of the fields the API names without one; a custom question carries its own.
LABELS = {
    "first_name": "First name",
    "last_name": "Last name",
    "email_address": "Email address",
    "home_phone": "Phone number",
    "sms_opt_in": "I would like to communicate with the hiring team via text message",
    "street_address": "Address",
    "city": "City",
    "state_id": "State/Province",
    "zip_code": "Zip/Postal Code",
    "linkedin": "LinkedIn URL",
    "candidate_referred": "Were you referred by anyone?",
    "referred_by": "Name of person who referred you",
    "resume": "Resume",
    "cover": "Cover letter",
    "desired_compensation": "Desired compensation",
    "desired_compensation_unit": "Desired compensation type",
    "start_new_position": "Timeframe to start new position",
    "earliest_availability": "What is your earliest available start date?",
    "career_objection": "What are your career objectives over the next 10 years?",
    "non_complete_in_place": "Do you have a non-compete agreement in place with your current"
    " employer?",
    "non_complete_in_place_reason": "Non-compete agreement details",
    "relocate": "Are you willing to relocate?",
    "relocate_reason": "Relocation details",
    "company_name": "Company name",
    "company_website": "Company website",
    "location_city": "City",
    "location_state_id": "State",
    "job_title_held": "Job title held",
    "start_date": "Start date",
    "end_date": "End date",
    "current": "Current",
    "supervisor_name": "Name of immediate supervisor",
    "supervisor_title": "Supervisor's title",
    "manage_others": "Did you manage others in this role?",
    "manage_others_text": "If yes, please explain",
    "position_likeness": "What did you like most about the position?",
    "position_dislikeness": "What did you like least about this position and why did you leave"
    " (or plan to leave)?",
    "highest_grade": "Highest level of education completed",
    "institute_name": "School name",
    "graduation_year": "Graduation year",
    "education_city": "City",
    "education_state_id": "State",
    "participation_extra_activities": "Did you participate in extracurricular activities?",
    "extra_activities": "Extracurricular activities",
    "part_time": "Did you work while in school?",
    "part_time_text": "Work while in school",
    "gained_academic_awards": "Did you receive academic honors or awards?",
    "academic_awards": "Academic honors or awards",
    "military": "Did you serve in any branch of the US Armed Services?",
    "army_branch": "Branch",
    "highest_rank": "Highest rank/grade",
    "joining_date": "Date entered",
    "discharge_date": "Date discharged",
    "currently_serving": "Currently serving",
    "honorable_discharge": "Did you receive an honorable discharge?",
    "nature_of_duty": "What was the nature of your duties?",
    "gender": "Gender",
    "is_hispanic": "Are you Hispanic or Latino?",
    "race": "Identify your race",
    "veteran_status": "Veteran status",
    "signature": "Signature",
}
# The page's heading of a section whose labels repeat others' ("City").
SECTIONS = {"experience": "Work history: ", "education": "Education: ", "military": "Military: "}
# A field the page shows only once another is answered, and on which answers: the school fields
# stay hidden until an education is picked.
SHOWN_BY: dict[str, Is] = {
    "referred_by": Is("candidate_referred", ["Yes"]),
    "non_complete_in_place_reason": Is("non_complete_in_place", ["Yes"]),
    "relocate_reason": Is("relocate", ["Yes"]),
    "extra_activities": Is("participation_extra_activities", ["Yes"]),
    "part_time_text": Is("part_time", ["Yes"]),
    "academic_awards": Is("gained_academic_awards", ["Yes"]),
    **{
        f: Is("military", ["Yes"])
        for f in (
            "army_branch",
            "highest_rank",
            "joining_date",
            "discharge_date",
            "currently_serving",
            "honorable_discharge",
            "nature_of_duty",
        )
    },
    **{
        f: Is("highest_grade", ["Did Not Complete High School"], negated=True)
        for f in ("institute_name", "graduation_year", "education_city", "education_state_id")
    },
}
TYPES = {
    "text": FieldType.TEXT,
    "email": FieldType.TEXT,
    "textarea": FieldType.TEXT,
    "month": FieldType.TEXT,
    "tel": FieldType.PHONE,
    "date": FieldType.DATE,
    "select": FieldType.CHOICE,
    "radio": FieldType.CHOICE,
    "checkbox": FieldType.CHOICE,
    "file": FieldType.FILE,
}

# The control a field renders as: the element whose id is `<field id>-<n>`, a radio group's
# and a month's wrapper included; `n` is 0, or 1 for the education entry.
_CONTROL = """(form, id) => {
  const own = new RegExp(`^${id.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&")}-\\\\d+$`);
  return [...form.querySelectorAll("[id]")].find(e => own.test(e.id))?.id ?? null;
}"""

# The form API's answer, fetched as the page fetches it.
_FETCH = """async url => {
  const response = await fetch(url);
  return response.ok ? {json: await response.json()} : {status: response.status};
}"""

# Picks a dropdown's option by its text: React keeps the value only from a change it sees.
_SELECT = """(el, text) => {
  const option = [...el.options].find(o => o.text.trim() === text);
  if (!option) return;
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(el, option.value);
  el.dispatchEvent(new Event("change", {bubbles: true}));
}"""

# What each present control shows, by the field's API type.
_READ_BACK = """(form, kinds) => {
  const out = {};
  for (const [id, kind] of Object.entries(kinds)) {
    const own = new RegExp(`^${id.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&")}-\\\\d+$`);
    const el = [...form.querySelectorAll("[id]")].find(e => own.test(e.id));
    if (!el) continue;
    if (kind === "radio") {
      const checked = el.querySelector("input:checked");
      out[id] = checked ? checked.closest("label").textContent.trim() : null;
    } else if (kind === "checkbox") out[id] = el.checked ? "Yes" : "No";
    else if (kind === "select") out[id] = el.value ? el.selectedOptions[0].text.trim() : null;
    else if (kind === "month") {
      const month = document.getElementById(`${el.id}-month`).value;
      const year = document.getElementById(`${el.id}-year`).value;
      out[id] = month || year ? `${month}/${year}` : null;
    } else if (kind === "file") {
      const shown = el.closest(".careers-file-input__container")
        ?.querySelector("[data-qa-id=careers-filename]");
      out[id] = shown ? shown.textContent.trim() : null;
    } else out[id] = el.value;
  }
  return out;
}"""


class Hireology:
    name = "hireology"
    known_gaps: dict[str, Gap] = {}
    side_effects = [SideEffect("submit", "employer", "the application reaches the employer")]

    def __init__(self) -> None:
        self._kinds: dict[str, str] = {}  # each field's API type, in the form's order

    def detect(self, url: str, page: Page | None) -> bool:
        return _job(url) is not None

    def posting(self, url: str) -> str:
        job = _job(url)
        return API.format(job=job) if job else url

    def read_api(self, url: str) -> list[Field] | None:
        if (job := _job(url)) is None:
            return None
        request = urllib.request.Request(API.format(job=job), headers={"User-Agent": "atsfill"})
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
                return [f for f, _ in _form(json.load(response))]
        except OSError, ValueError, KeyError, TypeError, Unsupported:
            return None

    def read_form(self, page: Page) -> list[Field]:
        job = _job(page.url)
        try:
            page.locator(SUBMIT).wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        api = page.evaluate(_FETCH, API.format(job=job))
        if "status" in api:
            raise Unsupported(f"posting_gone: the form API answered {api['status']}")
        form_el, out = page.locator(FORM), []
        for field, kind in _form(api["json"]):
            skip = field.unfillable or field.id in SHOWN_BY
            if not skip and form_el.evaluate(_CONTROL, field.id) is None:
                reason = f"unhandled_rendering: {field.id} is not on the page"
                field = replace(field, unfillable=reason)
            out.append((field, kind))
        self._kinds = {field.id: kind for field, kind in out if not field.unfillable}
        return [f for f, _ in out]

    def shown_fields(self, page: Page) -> list[str]:
        form = page.locator(FORM)
        return [i for i in self._kinds if form.evaluate(_CONTROL, i) is not None]

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        form = page.locator(FORM)
        for field_id, kind in self._kinds.items():  # a question before the fields it shows
            if field_id not in answers:
                continue
            answer = answers[field_id]
            value = answer if isinstance(answer, str) else answer[0]
            if field_id in SHOWN_BY:  # rendered a moment after the answer that shows it
                with suppress(Timeout):
                    form.locator(f"[id^='{field_id}-']").first.wait_for(
                        state="attached", timeout=SHOWN_TIMEOUT_MS
                    )
            dom = form.evaluate(_CONTROL, field_id)
            if dom is None:
                continue  # shown by another answer, which was not given
            control = page.locator(f"[id='{dom}']")
            match kind:
                case "file":
                    control.set_input_files(value)
                case "select":
                    control.evaluate(_SELECT, value)
                case "radio":
                    browser.click(control.locator("label").filter(has_text=_exact(value)))
                case "checkbox":
                    if control.is_checked() != (value == "Yes"):
                        browser.click(page.locator(f"label[for='{dom}']"))
                case "month":
                    month, _, year = value.partition("/")
                    page.locator(f"[id='{dom}-month']").fill(month)
                    page.locator(f"[id='{dom}-year']").fill(year)
                case _:
                    control.fill(value)

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = page.locator(FORM).evaluate(_READ_BACK, self._kinds)
        return {k: v for k, v in values.items() if v not in (None, "")}

    def submit(self, page: Page) -> str | None:
        """Clicks submit; the evidence is the API's answer to the application."""
        with page.expect_response(
            lambda r: r.request.method == "POST" and APPLIED.match(r.url) is not None,
            timeout=SUBMIT_TIMEOUT_MS,
        ) as applied:
            browser.click(page.locator(SUBMIT))
        response = applied.value
        if not response.ok:
            raise RuntimeError(f"apply answered {response.status}: {response.text()[:200]}")
        return f"{response.url} {response.status}"


def _job(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.hostname != HOST or not (match := JOB.match(parts.path)):
        return None
    return match[1]


def _exact(value: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(value)}\s*$")


def _form(api: dict[str, Any]) -> list[tuple[Field, str]]:
    """Each field of the form API's template with its API type; an entry the page can repeat
    is read once, as its first. A field shown by one the form does not ask is never shown."""
    out = []
    for section in api["template"]["sections"]:
        for fieldset in section["fieldsets"]:
            for entry in fieldset["fields"]:
                out.append(
                    (_field(entry, SECTIONS.get(section["id"], "")), entry["attributes"]["type"])
                )
    ids = {f.id for f, _ in out}
    return [(f, k) for f, k in out if f.id not in SHOWN_BY or SHOWN_BY[f.id].field_id in ids]


def _field(entry: dict[str, Any], section: str) -> Field:
    field_id, attributes = entry["id"], entry["attributes"]
    kind = attributes["type"]
    label = (
        section
        + (
            LABELS.get(field_id) or attributes.get("label") or entry.get("label") or field_id
        ).strip()
    )
    if kind not in TYPES:
        reason = f"unhandled_rendering: {field_id} is a {kind}"
        return Field(
            field_id, label, FieldType.TEXT, bool(attributes.get("required")), unfillable=reason,
            shown_when=SHOWN_BY.get(field_id),
        )  # fmt: skip
    required = attributes["required"] and kind != "checkbox"  # a box left clear is an answer
    options = [o["name"] for o in attributes["options"]] or None
    if kind == "checkbox":
        options = YES_NO
    elif kind == "month":
        label += MONTH
    max_length = attributes.get("maxlength") if TYPES[kind] is FieldType.TEXT else None
    if kind == "month":
        max_length = None
    accept = FILE_TYPES if kind == "file" else None
    return Field(
        field_id, label, TYPES[kind], required, options, max_length, accept,
        default=_default(attributes, kind), shown_when=SHOWN_BY.get(field_id),
    )  # fmt: skip


def _default(attributes: dict[str, Any], kind: str) -> str | None:
    """The answer the page shows picked before any, from the API's `value`: an option's name,
    a box's `Yes` or `No`."""
    value = attributes.get("value")
    if kind == "checkbox" and isinstance(value, bool):
        return YES_NO[0] if value else YES_NO[1]
    names = {str(o.get("value")): o["name"] for o in attributes.get("options") or []}
    return names.get(str(value)) if isinstance(value, str | int) else None
