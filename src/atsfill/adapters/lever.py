"""Lever (`jobs.lever.co` job pages and their application form), as docs/adapters/lever.md
observes.

The form is server-rendered HTML: every field is a named control, and its name is the field's
id. Nothing but the resume reaches Lever before submit, so the read-back is the page.
"""

import re
from contextlib import suppress
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser
from atsfill.core.adapter import Blocked, Gap, Unsupported
from atsfill.core.browser import Locator, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOSTS = re.compile(r"^jobs\.(eu\.)?lever\.co$")
JOB = re.compile(r"^/([^/]+)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b")
FORM = "form#application-form"
FORM_TIMEOUT_MS = 30_000
UPLOAD_TIMEOUT_MS = 30_000
SUGGEST_TIMEOUT_MS = 10_000
SUBMIT_TIMEOUT_MS = 90_000
KEY_DELAY_MS = 40
YES_NO = ["Yes", "No"]
KINDS = {
    "text": FieldType.TEXT,
    "phone": FieldType.PHONE,
    "date": FieldType.DATE,
    "location": FieldType.TYPEAHEAD,
    "file": FieldType.FILE,
    "select": FieldType.CHOICE,
    "radio": FieldType.CHOICE,
    "consent": FieldType.CHOICE,
    "checkboxes": FieldType.MULTI_CHOICE,
}

# The fields in page order, one per control name: its question's label, and how it renders.
_READ = """form => {
  const text = el => {
    if (!el) return "";
    const copy = el.cloneNode(true);
    copy.querySelectorAll(".required, a, .description, .eeo-option-description, ul, select")
      .forEach(e => e.remove());
    return copy.textContent.replace(/\\s+/g, " ").trim();
  };
  const optionText = input => text(input.closest("label")?.querySelector(
    ".application-answer-alternative")) || input.value;
  const found = new Map();
  const controls = form.querySelectorAll(
    "input[name]:not([type=hidden]):not([type=submit]), select[name], textarea[name]");
  for (const el of controls) {
    const name = el.name;
    if (!name || el.id === "customPronounsTextField") continue;
    const box = el.closest(".application-question, .application-additional");
    if (!box) continue;
    let field = found.get(name);
    if (!field) {
      const label = box.querySelector(".application-label .text") ||
        box.querySelector(".application-label");
      field = {id: name, label: text(label), required: !!box.querySelector(".required"),
               options: [], count: 0};
      if (el.type === "file") {
        field.kind = "file";
      } else if (el.tagName === "SELECT") {
        field.kind = "select";
        field.options = [...el.options].filter(o => o.value).map(o => o.textContent.trim());
      } else if (el.type === "radio") field.kind = "radio";
      else if (el.type === "checkbox") field.kind = name.startsWith("consent[") ? "consent" :
                                                    "checkboxes";
      else if (name === "location") field.kind = "location";
      else if (name === "phone") field.kind = "phone";
      else if (el.placeholder === "MM/DD/YYYY") field.kind = "date";
      else field.kind = "text";
      field.maxLength = el.maxLength > 0 ? el.maxLength : null;
      if (field.kind === "consent") field.label = text(el.closest("label"));
      if (!field.label) field.label = el.placeholder ||
        text(box.closest(".section")?.querySelector("h4"));
      found.set(name, field);
    }
    field.required ||= el.required;
    if (el.type === "radio" || el.type === "checkbox") field.options.push(optionText(el));
  }
  return [...found.values()];
}"""

# What each field shows: a choice its checked option's text, a select its option's text, a file
# its attached name.
_READ_BACK = """(form, kinds) => {
  const optionText = input => (input.closest("label")?.querySelector(
    ".application-answer-alternative")?.textContent || input.value).replace(/\\s+/g, " ").trim();
  const out = {};
  for (const [name, kind] of Object.entries(kinds)) {
    const all = [...form.querySelectorAll(`[name="${CSS.escape(name)}"]`)]
      .filter(e => e.type !== "hidden");
    if (!all.length) continue;
    const el = all[0];
    if (kind === "file") {
      out[name] = el.closest("a, .application-field")?.querySelector(".filename")
        ?.textContent.trim() || null;
    } else if (kind === "select") {
      const option = el.selectedOptions[0];
      out[name] = option && option.value ? option.textContent.trim() : null;
    } else if (kind === "radio") {
      const checked = all.find(e => e.checked);
      out[name] = checked ? optionText(checked) : null;
    } else if (kind === "checkboxes") {
      out[name] = all.filter(e => e.type === "checkbox" && e.checked).map(optionText);
    } else if (kind === "consent") out[name] = el.checked ? "Yes" : "No";
    else out[name] = el.value;
  }
  return out;
}"""


class Lever:
    name = "lever"
    known_gaps: dict[str, Gap] = {}
    side_effects = [
        SideEffect("upload", "hiring_system", "the resume is uploaded to Lever and parsed at once"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._kinds: dict[str, str] = {}  # how the page renders each field

    def detect(self, url: str, page: Page | None) -> bool:
        return _job(url) is not None

    def posting(self, url: str) -> str:
        parts, job = urlsplit(url), _job(url)
        return f"https://{parts.hostname}/{job[0]}/{job[1]}" if job else url

    def browser_read(self, page: Page) -> list[Field]:
        if not urlsplit(page.url).path.rstrip("/").endswith("/apply"):
            page.goto(f"{self.posting(page.url)}/apply")
        form = page.locator(FORM)
        try:
            form.wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        fields, self._kinds = [], {}
        for raw in form.evaluate(_READ):
            fields.append(_field(raw))
            self._kinds[raw["id"]] = raw["kind"]
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        # the resume is parsed into the form, so it goes first and is waited for
        for name, answer in sorted(answers.items(), key=lambda a: a[0] != "resume"):
            values = [answer] if isinstance(answer, str) else answer
            controls = page.locator(f"{FORM} [name='{name}']:not([type=hidden])")
            match self._kinds[name]:
                case "file":
                    _attach(controls.first, values[0], resume=name == "resume")
                case "location":
                    _locate(page, controls.first, values[0])
                case "select":
                    controls.first.select_option(label=values[0])
                case "radio" | "checkboxes":
                    for value in values:
                        option = _option(controls, value)
                        if option is not None:
                            option.check()
                case "consent":
                    controls.first.set_checked(answer == "Yes")
                case "date":  # typed as its placeholder shows, MM/DD/YYYY
                    day = date.fromisoformat(values[0])
                    controls.first.fill(f"{day.month:02}/{day.day:02}/{day.year}")
                case _:
                    controls.first.fill(values[0])

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = page.locator(FORM).evaluate(_READ_BACK, self._kinds)
        for name, kind in self._kinds.items():
            if kind == "date" and (
                shown := re.fullmatch(r"(\d\d)/(\d\d)/(\d{4})", values.get(name) or "")
            ):
                values[name] = f"{shown[3]}-{shown[1]}-{shown[2]}"
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        """Clicks submit; hCaptcha checks the visitor, then the form posts and Lever shows its
        thanks page, the evidence."""
        browser.click(page.locator(f"{FORM} #btn-submit"))
        try:
            page.wait_for_url(re.compile(r"/thanks\b"), timeout=SUBMIT_TIMEOUT_MS)
        except Timeout:
            if page.locator("iframe[src*='hcaptcha'][title*='challenge']").is_visible():
                raise Blocked("captcha: hCaptcha asked for a challenge") from None
            raise
        return page.url


def _job(url: str) -> tuple[str, str] | None:
    """The account and posting id, from `jobs[.eu].lever.co/<account>/<id>[/apply]`."""
    parts = urlsplit(url)
    if not HOSTS.match(parts.hostname or ""):
        return None
    match = JOB.match(parts.path)
    return (match[1], match[2]) if match else None


def _field(raw: dict[str, Any]) -> Field:
    kind = raw["kind"]
    if kind not in KINDS:
        reason = f"unhandled_rendering: {raw['id']} is a {kind}"
        return Field(raw["id"], raw["label"], FieldType.TEXT, raw["required"], unfillable=reason)
    options = {"consent": YES_NO, "select": raw["options"]}.get(kind)
    if kind in ("radio", "checkboxes"):
        options = raw["options"]
    return Field(
        raw["id"], raw["label"], KINDS[kind], raw["required"], options, raw.get("maxLength")
    )


def _option(controls: Locator, value: str) -> Locator | None:
    """The radio or checkbox whose text is `value`."""
    for control in controls.all():
        label = control.locator("xpath=ancestor::label[1]")
        shown = label.locator(".application-answer-alternative")
        text = (shown.first.text_content() if shown.count() else None) or (
            control.get_attribute("value") or ""
        )
        if " ".join(text.split()) == value:
            return control
    return None


def _attach(control: Locator, path: str, resume: bool) -> None:
    """Attaches a file; the resume is uploaded and parsed at once, waited for until Lever says
    it read it or could not."""
    control.set_input_files(path)
    if not resume:
        return
    box = control.locator("xpath=ancestor::li[1]")
    with suppress(Timeout):
        box.locator(".resume-upload-success, .resume-upload-failure").locator(
            "visible=true"
        ).first.wait_for(timeout=UPLOAD_TIMEOUT_MS)


def _locate(page: Page, control: Locator, value: str) -> None:
    """Types the location and picks Lever's first suggestion: the field takes only a pick."""
    control.fill("")
    control.press_sequentially(value, delay=KEY_DELAY_MS)
    suggestion = page.locator(".dropdown-results .dropdown-location").first
    with suppress(Timeout):
        suggestion.wait_for(timeout=SUGGEST_TIMEOUT_MS)
        suggestion.click()
