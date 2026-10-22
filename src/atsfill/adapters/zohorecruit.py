"""Zoho Recruit (`<account>.zohorecruit.<tld>/jobs/<page>/<job>` career sites), as
docs/adapters/zohorecruit.md observes.

The job page names its application form, which "I'm interested" renders from a public form
API: sections of fields by id, type and requirement, and the pre-screening questions a form
may ask with them. `read_api` fetches it over HTTP, `read_form` through the page's browser
context, and both list the fields from it. Each field renders as a Lyte component whose id
derives from the field's; nothing but the submit reaches Zoho.
"""

import json
import re
import urllib.request
from contextlib import suppress
from dataclasses import replace
from datetime import date, datetime
from typing import Any
from urllib.parse import quote, urlsplit

from atsfill.core import browser
from atsfill.core.adapter import Blocked, Gap, Unsupported
from atsfill.core.browser import HTTP_TIMEOUT_S, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOST = re.compile(r"^[\w-]+\.zohorecruit\.(?:com|eu|in|ca|com\.au|jp|com\.cn|sa|uk)$")
JOB = re.compile(r"^/jobs/([^/]+)/(\d+)(?:/|$)")
POSTING = "https://{host}/recruit/v2/public/Job_Openings/{job}"
API = POSTING + "/apply/{form}?pagename={page}&source=CareerSite"
WEBFORM = re.compile(r'"webform_id":"(\d+)"')
APPLY = "#detail-page-applybtn button"
COOKIES = "button.cookie-decline-btn"
FORM = "rec-form-component[webformid]"
SUBMIT = "#cw-submit-btn button"
FORM_TIMEOUT_MS = 30_000
COOKIE_TIMEOUT_MS = 5_000
COOKIE_ATTEMPTS = 3
SHOWN_TIMEOUT_MS = 2_000
TAG_TIMEOUT_MS = 2_000
TAG_ATTEMPTS = 4
SUBMIT_TIMEOUT_MS = 90_000
YES_NO = ["Yes", "No"]
NONE = "-None-"

# A field's kind, by its data type; a skill set is a textarea the page renders as tags.
KINDS = {
    "text": "input",
    "email": "input",
    "website": "input",
    "textarea": "textarea",
    "integer": "number",
    "bigint": "number",
    "double": "number",
    "currency": "number",
    "phone": "phone",
    "date": "date",
    "picklist": "picklist",
    "multipicklist": "multipicklist",
    "boolean": "boolean",
    "monthrange": "month",
}
SKILLS_UI = 600
NOT_FIELDS = {"captcha", "gdpr"}  # the CAPTCHA is typed on submit; consent is not rendered
# A pre-screening question's kind, by its type.
QUESTIONS = {1: "input", 2: "textarea", 3: "radio", 6: "picklist"}
TYPES = {
    "input": FieldType.TEXT,
    "textarea": FieldType.TEXT,
    "number": FieldType.TEXT,
    "skills": FieldType.TEXT,
    "month": FieldType.TEXT,
    "phone": FieldType.PHONE,
    "date": FieldType.DATE,
    "picklist": FieldType.CHOICE,
    "radio": FieldType.CHOICE,
    "boolean": FieldType.CHOICE,
    "multipicklist": FieldType.MULTI_CHOICE,
    "file": FieldType.FILE,
}

# The job page and its form API, fetched as the page fetches them.
_FETCH = """async url => {
  const response = await fetch(url);
  return response.ok ? {text: await response.text()} : {status: response.status};
}"""

# What each present control shows: `controls` holds [field id, kind, component selector].
_READ_BACK = """controls => {
  const out = {};
  const text = el => el ? el.textContent.replace(/\\s+/g, " ").trim() : "";
  for (const [id, kind, sel] of controls) {
    const el = document.querySelector(sel);
    if (!el) continue;
    if (kind === "input" || kind === "number") out[id] = el.querySelector("input")?.value;
    else if (kind === "textarea") out[id] = el.querySelector("textarea")?.value;
    else if (kind === "date") {
      out[id] = [el.querySelector("input")?.value, el.querySelector("lyte-input")
        ?.getAttribute("lt-prop-format")];
    } else if (kind === "phone") {
      const number = el.querySelector("input[name]")?.value;
      const dial = text(el.querySelector(".flag-drop-code")).replace(/[()]/g, "");
      out[id] = number ? `${dial} ${number}` : null;
    } else if (kind === "picklist") {
      const shown = el.querySelector("lyte-drop-button");
      out[id] = shown?.querySelector(".lyteDropPlaceholderNormal") ? null : text(shown);
    } else if (kind === "multipicklist") {
      out[id] = [...el.querySelectorAll("lyte-drop-button li:not(.lyteMultiselectInput)")]
        .map(text).filter(Boolean);
    } else if (kind === "boolean") out[id] = el.querySelector("input").checked ? "Yes" : "No";
    else if (kind === "radio") {
      out[id] = text(el.querySelector("input:checked")?.closest("label")
        ?.querySelector(".lyteRadioLabel")) || null;
    } else if (kind === "skills") {
      out[id] = [...el.querySelectorAll("li span[aria-label]")]
        .map(s => s.getAttribute("aria-label")).join(", ");
    } else if (kind === "month") {
      const month = document.getElementById(`cxdby-${el.id}_month`)
        ?.querySelector("lyte-drop-item[selected=true]")?.dataset.value;
      const year = text(document.getElementById(`${el.id}_year`)?.querySelector(
        "lyte-drop-button"));
      out[id] = [month, year];
    } else if (kind === "file") {
      out[id] = text(el.querySelector(".lyteFileUpdList .lyteFileUpdFileName"))
        || text(el.querySelector(".lyteFileUpdList")) || null;
    }
  }
  return out;
}"""


class ZohoRecruit:
    name = "zohorecruit"
    known_gaps: dict[str, Gap] = {}
    side_effects = [SideEffect("submit", "employer", "the application reaches the employer")]

    def __init__(self) -> None:
        self._controls: list[_Control] = []  # in the form's order
        self._captcha = False
        self._job: str | None = None

    def detect(self, url: str, page: Page | None) -> bool:
        return _job(url) is not None

    def posting(self, url: str) -> str:
        parts, job = urlsplit(url), _job(url)
        return POSTING.format(host=parts.hostname, job=job[2]) if job else url

    def read_api(self, url: str) -> list[Field] | None:
        if _job(url) is None:
            return None
        try:
            html = _get(url)
            if (form := WEBFORM.search(html)) is None:
                return None
            api = json.loads(_get(_api(url, form[1])))
            return [c.field for c in _form(api)[0]]
        except OSError, ValueError, KeyError, TypeError, Unsupported:
            return None

    def read_form(self, page: Page) -> list[Field]:
        job = _job(page.url)
        if job is None:
            raise Unsupported(f"posting_gone: {page.url} is not a job page")
        apply = page.locator(APPLY).first
        try:
            apply.wait_for(timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no apply button on the job page") from None
        html = page.evaluate(_FETCH, page.url)
        if "status" in html or (form := WEBFORM.search(html["text"])) is None:
            raise Unsupported("posting_gone: the job page names no application form")
        _open(page, apply)
        page.locator(SUBMIT).wait_for(timeout=FORM_TIMEOUT_MS)
        api = page.evaluate(_FETCH, _api(page.url, form[1]))
        if "status" in api:
            raise Unsupported(f"posting_gone: the form API answered {api['status']}")
        self._controls, self._captcha = _form(json.loads(api["text"]))
        self._job = job[2]
        for control in self._controls:
            if control.field.unfillable or control.row or control.shown_by:
                continue
            if page.locator(control.sel).count() == 0:
                reason = f"unhandled_rendering: {control.field.id} is not on the page"
                control.field = replace(control.field, unfillable=reason)
        fields = [c.field for c in self._controls]
        self._controls = [c for c in self._controls if not c.field.unfillable]
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        rows = {c.row for c in self._controls if c.row and c.field.id in answers}
        for add in rows:  # one entry of each repeatable section answered
            browser.click(page.locator(_id(add)))
        for control in self._controls:
            if control.field.id not in answers:
                continue
            el = page.locator(control.sel).first
            if control.shown_by:  # rendered once the field that shows it is answered
                with suppress(Timeout):
                    el.wait_for(state="visible", timeout=SHOWN_TIMEOUT_MS)
                if not el.is_visible():
                    continue
            _fill(page, control, el, answers[control.field.id])

    def read_back(self, page: Page) -> dict[str, Answer]:
        controls = [[c.field.id, c.kind, c.sel] for c in self._controls]
        values = page.evaluate(_READ_BACK, controls)
        out: dict[str, Answer] = {}
        for control in self._controls:
            value = values.get(control.field.id)
            if control.kind == "date" and value:
                value = _iso(*value)
            elif control.kind == "month" and value:
                month, year = value
                value = f"{int(month):02}/{year}" if month and month != "0" else None
            if value not in (None, "", []):
                out[control.field.id] = value
        return out

    def submit(self, page: Page) -> str | None:
        """Clicks submit; the evidence is the form API's answer to the application. A form
        with an image CAPTCHA is not sent: atsfill does not read the image."""
        if self._captcha:
            raise Blocked("captcha: the form asks to type an image CAPTCHA")
        with page.expect_response(
            lambda r: r.request.method == "POST" and f"/Job_Openings/{self._job}/apply" in r.url,
            timeout=SUBMIT_TIMEOUT_MS,
        ) as applied:
            browser.click(page.locator(SUBMIT))
        response = applied.value
        if not response.ok:
            raise RuntimeError(f"apply answered {response.status}: {response.text()[:200]}")
        return f"{response.url.split('?')[0]} {response.status}"


class _Control:
    """A field and how the page renders it: its kind, its component's selector, the button
    that adds its section's entry, and whether another field's answer shows it."""

    def __init__(
        self, field: Field, kind: str, sel: str, row: str | None = None, shown_by: bool = False
    ) -> None:
        self.field, self.kind, self.sel, self.row, self.shown_by = field, kind, sel, row, shown_by


def _job(url: str) -> re.Match[str] | None:
    parts = urlsplit(url)
    if not HOST.match(parts.hostname or ""):
        return None
    return JOB.match(parts.path)


def _api(url: str, form: str) -> str:
    parts, job = urlsplit(url), _job(url)
    assert job is not None
    return API.format(host=parts.hostname, job=job[2], form=form, page=quote(job[1]))


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "atsfill"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return response.read().decode()


def _open(page: Page, apply) -> None:
    """Clicks "I'm interested", declining the cookie banner first: its layer takes every
    click while it shows, and it shows a moment after the page, at times after a while."""
    decline = page.locator(COOKIES)
    for attempt in range(COOKIE_ATTEMPTS):
        with suppress(Timeout):
            decline.wait_for(timeout=COOKIE_TIMEOUT_MS if attempt == 0 else 0)
            decline.click()
        try:
            browser.click(apply)
            return
        except browser.Covered:
            if attempt == COOKIE_ATTEMPTS - 1:
                raise
            decline.wait_for(timeout=COOKIE_TIMEOUT_MS)


def _form(api: dict[str, Any]) -> tuple[list[_Control], bool]:
    """Each field of the form, in its order, and whether it asks for a CAPTCHA. A repeatable
    section is read as its first entry; the pre-screening questions are read when the form
    asks them with the application."""
    webform = api["webforms"][0]
    shown = {
        f["api_name"]: (rule["primary_field"]["api_name"], query["criteria"])
        for rule in api["meta"].get("layout_rules") or []
        if rule.get("active", True)
        for query in rule["queries"]
        for action in query["actions"]
        if action["type"] == "Show_Fields"
        for f in action["fields"]
    }
    labels = {
        f["api_name"]: f["field_label"].strip()
        for s in webform["sections"]
        for f in s.get("fields") or []
    }
    controls, captcha = [], "captcha_info" in webform
    for section in sorted(webform["sections"], key=lambda s: s["sequence_number"]):
        if section.get("isattachment"):
            controls += [_file(a) for a in section["attachmenttypes"]]
        elif section.get("istabular"):
            controls += _row(section, webform["layout"]["id"])
        elif section.get("isVisible", True):
            for f in section.get("fields") or []:
                if f["data_type"] == "captcha":
                    captcha = True
                if (control := _control(f, labels, shown)) is not None:
                    controls.append(control)
    assessment = api.get("Assessments")
    if webform.get("mandate_assessment") and isinstance(assessment, dict):
        controls += [_question(q) for q in assessment.get("questions") or []]
    return controls, captcha


def _control(
    f: dict[str, Any], labels: dict[str, str], shown: dict[str, tuple[str, dict]]
) -> _Control | None:
    kind = f["data_type"]
    if kind in NOT_FIELDS or not f.get("visible", True):
        return None
    if kind not in KINDS:
        return _unhandled(f["api_name"], f["field_label"].strip(), f.get("required"), kind)
    kind = "skills" if f["ui_type"] == SKILLS_UI else KINDS[kind]
    field = _field(f["api_name"], f["field_label"].strip(), kind, f.get("required", False), f)
    if trigger := shown.get(f["api_name"]):
        name, criteria = trigger
        negated = "not " if criteria["comparator"] == "not_equal" else ""
        label = labels.get(name, name)
        field = Field(
            field.id,
            f"{field.label} (asked when {label!r} is {negated}{criteria['value']})",
            field.type,
            False,
            field.options,
            field.max_length,
        )
    return _Control(field, kind, _id(f"rec-form_{f['id']}"), shown_by=f["api_name"] in shown)


def _field(field_id: str, label: str, kind: str, required: bool, f: dict[str, Any]) -> Field:
    options = None
    if kind in ("picklist", "multipicklist"):
        options = [o["display_value"] for o in f["pick_list_values"] if o["actual_value"] != NONE]
    elif kind == "boolean":
        options, required = YES_NO, False  # a box left clear is an answer
    elif kind == "number":
        label += " (a number)"
    elif kind == "month":
        label += " (MM/YYYY)"
    elif kind == "skills":
        label += " (comma-separated)"
    max_length = f.get("length") if kind in ("input", "textarea") else None
    return Field(field_id, label, TYPES[kind], bool(required), options, max_length)


def _row(section: dict[str, Any], layout: str) -> list[_Control]:
    """The fields of a repeatable section's first entry: `<section>.<field>`, its month
    range as a start and an end. A box ("Currently pursuing") hides the range's end once
    ticked, so it comes first and the end is asked when it is clear."""
    tabular = next(f for f in section["fields"] if f["data_type"] == "tabular")
    fields = tabular.get("fields") or [f for f in section["fields"] if f is not tabular]
    fields = [f for f in fields if f.get("visible", True)]
    fields.sort(key=lambda f: (f["data_type"] != "boolean", f.get("sequence_number", 0)))
    heading = section.get("display_label") or tabular["field_label"]
    add = f"crc-tabular-add-btn-{layout}_{section['id']}"
    label = lambda f: f"{heading}: {f['field_label'].strip()}"  # noqa: E731
    current = next((label(f) for f in fields if f["data_type"] == "boolean"), None)
    out = []
    for f in fields:
        field_id, dom = f"{tabular['api_name']}.{f['api_name']}", f"{f['id']}_1"
        if (kind := KINDS.get(f["data_type"])) is None:
            out.append(_unhandled(field_id, label(f), f.get("required"), f["data_type"]))
            continue
        if kind != "month":
            field = _field(field_id, label(f), kind, f.get("required"), f)
            out.append(_Control(field, kind, _id(dom), add))
            continue
        start = _field(f"{field_id}.from", f"{label(f)} start", kind, f.get("required"), f)
        out.append(_Control(start, kind, _id(f"{dom}_from"), add))
        end = _field(f"{field_id}.to", f"{label(f)} end", kind, f.get("required"), f)
        if current:
            end = Field(end.id, f"{end.label} (asked when {current!r} is No)", end.type, False)
        out.append(_Control(end, kind, _id(f"{dom}_to"), add, shown_by=current is not None))
    return out


def _file(attachment: dict[str, Any]) -> _Control:
    accept = sorted({f".{a}" for a in attachment["$accept"].split(",") if "/" not in a})
    field = Field(
        attachment["api_name"],
        attachment["field_label"].strip(),
        FieldType.FILE,
        bool(attachment.get("required")),
        accept=accept,
    )
    # The attachment's own input: the autofill uploader above the form names RESUME too
    upload = f"lyte-fileupload:has(input[name='rec-form_{attachment['id']}_file'])"
    return _Control(field, "file", upload)


def _question(q: dict[str, Any]) -> _Control:
    label = re.sub(r"\s+", " ", q["question"]).strip()
    if (kind := QUESTIONS.get(q["type"])) is None:
        what = f"pre-screening question of type {q['type']}"
        return _unhandled(q["id"], label, q.get("ismandatory"), what)
    options = [o["value"] for o in q["options"]] or None
    field = Field(q["id"], label, TYPES[kind], bool(q.get("ismandatory")), options)
    return _Control(field, kind, _id(f"rec-form_{q['id']}"))


def _unhandled(field_id: str, label: str, required: Any, what: str) -> _Control:
    reason = f"unhandled_rendering: {field_id} is a {what}"
    field = Field(field_id, label, FieldType.TEXT, bool(required), unfillable=reason)
    return _Control(field, "", "")  # listed, never filled


def _fill(page: Page, control: _Control, el, answer: Answer) -> None:
    value = answer if isinstance(answer, str) else answer[0]
    match control.kind:
        case "input" | "number":
            el.locator("input").first.fill(value)
        case "textarea":
            el.locator("textarea").first.fill(value)
        case "picklist":
            _pick(page, el, [value])
        case "multipicklist":
            _pick(page, el, [answer] if isinstance(answer, str) else answer)
        case "radio":
            browser.click(el.locator("label").filter(has_text=_exact(value)))
        case "boolean":
            if el.locator("input").is_checked() != (value == "Yes"):
                browser.click(el.locator("label"))
        case "date":
            fmt = el.locator("lyte-input").get_attribute("lt-prop-format") or "MM/DD/YYYY"
            day = date.fromisoformat(value)
            typed = (
                fmt.replace("YYYY", f"{day.year:04}")
                .replace("MM", f"{day.month:02}")
                .replace("DD", f"{day.day:02}")
            )
            el.locator("input").fill(typed)
            page.keyboard.press("Escape")
        case "phone":
            _phone(page, el, value)
        case "skills":
            for skill in (s.strip() for s in value.split(",")):
                if skill:
                    _tag(page, el, skill)
        case "month":
            month, _, year = value.partition("/")
            dom = el.get_attribute("id")
            _drop(page, f"{dom}_month", f"[data-value='{int(month)}']")  # named in its language
            _drop(page, f"{dom}_year", f":text-is('{year}')")
        case "file":
            el.locator("input[type=file]").set_input_files(value)


def _pick(page: Page, el, values: list[str]) -> None:
    """Opens a dropdown and picks each value; its list renders at the end of the page."""
    dom = el.locator("lyte-dropdown").first.get_attribute("id")
    items = page.locator(f"lyte-drop-body[class*='parentscope_{dom}_'] lyte-drop-item")
    for value in values:
        browser.click(el.locator(".lyteDummyEventContainer").first)
        browser.click(items.filter(has_text=_exact(value)).first)
    page.keyboard.press("Escape")


def _tag(page: Page, el, skill: str) -> None:
    """Adds a skill's tag: Enter takes the first of the suggestions the page looks up as the
    skill is typed, so it does nothing until they are in. A new tag opens a proficiency
    popover, and the input, while it has the focus, a suggestions popover: both cover the
    fields below until closed."""
    typed, tags = el.locator("input"), el.locator("li span[aria-label]")
    added = tags.count()
    typed.press_sequentially(skill)  # the lookup runs on keystrokes
    for _ in range(TAG_ATTEMPTS):
        typed.press("Enter")
        with suppress(Timeout):
            tags.nth(added).wait_for(timeout=TAG_TIMEOUT_MS)
            break
    page.keyboard.press("Escape")
    typed.blur()
    page.keyboard.press("Escape")


def _drop(page: Page, dom: str, item: str) -> None:
    """Picks the item a selector names from a month range's dropdown."""
    browser.click(page.locator(f"[id='{dom}'] .lyteDummyEventContainer"))
    browser.click(page.locator(f"[id='cxdby-{dom}'] lyte-drop-item{item}").first)


def _phone(page: Page, el, value: str) -> None:
    """Picks the country whose dial code the number starts with, the longest that fits,
    then types the rest of the number."""
    digits = re.sub(r"\D", "", value)
    browser.click(el.locator(".lyteDummyEventContainer").first)
    items = page.locator(f"lyte-drop-body[class*='parentscope_{el.get_attribute('id')}_input']")
    countries = items.locator("lyte-drop-item").evaluate_all(
        "items => items.map(i => [i.dataset.value, i.querySelector('.zrc-cc')?.textContent])"
    )
    fits = [(c, re.sub(r"\D", "", d)) for c, d in countries if d]
    fits = [(c, d) for c, d in fits if value.startswith("+") and digits.startswith(d)]
    if not fits:
        page.keyboard.press("Escape")
        el.locator("input[name]").fill(digits)
        return
    country, dial = max(fits, key=lambda cd: len(cd[1]))  # the first listed of the longest
    browser.click(items.locator(f"lyte-drop-item[data-value='{country}']"))
    el.locator("input[name]").fill(digits[len(dial) :])


def _iso(typed: str | None, fmt: str | None) -> str | None:
    if not typed:
        return None
    pattern = (fmt or "MM/DD/YYYY").replace("YYYY", "%Y").replace("MM", "%m").replace("DD", "%d")
    with suppress(ValueError):
        return datetime.strptime(typed, pattern).date().isoformat()
    return typed


def _id(dom: str) -> str:
    return f"[id='{dom}']:not(template)"  # the component, not the template it renders from


def _exact(value: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(value)}\s*$")
