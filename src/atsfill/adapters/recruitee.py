"""Recruitee (`*.recruitee.com` career sites and custom domains on them), as
docs/adapters/recruitee.md observes.

The form is the career site's own page, `/o/<slug>/c/new`: every field is a named control,
`candidate.<field>` or `candidate.openQuestionAnswers.<question>.<value>`, and the name is the
field's id. Nothing reaches Recruitee before submit, so the read-back is the page.
"""

import re
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser
from atsfill.core.adapter import Blocked, Unsupported
from atsfill.core.browser import Locator, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

JOB = re.compile(r"^/o/([^/?#]+)")
CAREER_SITE = "script[src*='/js/PublicApp/'], script[src*='recruiteecdn.com']"
FORM = "form#offer-application-form"
FORM_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
KEY_DELAY_MS = 20
YES_NO = ["Yes", "No"]
EXTENSIONS = {"image/png": [".png"], "image/jpeg": [".jpg", ".jpeg"], "application/pdf": [".pdf"]}
KINDS = {
    "text": FieldType.TEXT,
    "number": FieldType.TEXT,
    "phone": FieldType.PHONE,
    "date": FieldType.DATE,
    "file": FieldType.FILE,
    "radio": FieldType.CHOICE,
    "legal": FieldType.CHOICE,
    "checkboxes": FieldType.MULTI_CHOICE,
}

# The fields in page order, one per control name: its question's label, and how it renders. A
# labelled question without a named control (a video recorder, drawn late) comes as kind
# `video`; one without a label is text only.
_READ = """form => {
  const text = el => {
    if (!el) return "";
    const copy = el.cloneNode(true);
    copy.querySelectorAll("[aria-hidden=true]").forEach(e => e.remove());
    return copy.textContent.replace(/\\s+/g, " ").trim();
  };
  const found = new Map();
  const controls = form.querySelectorAll(
    "input[name]:not([type=hidden]), textarea[name], input[name$='.openQuestionId']");
  for (const el of controls) {
    if (el.type === "hidden") {
      const question = el.name.replace(/\\.openQuestionId$/, "");
      const label = el.parentElement.querySelector("label");
      if (label && !form.querySelector(
          `[name^="${CSS.escape(question)}."]:not([type=hidden])`))
        found.set(question, {id: question, kind: "video", label: text(label),
                             required: /\\*/.test(label.textContent), options: []});
      continue;
    }
    const name = el.name;
    let field = found.get(name);
    if (!field) {
      const group = el.closest("fieldset");
      field = {id: name, required: false, options: [], accept: null,
               maxLength: el.maxLength > 0 ? el.maxLength : null};
      if (el.type === "radio") field.kind = "radio";
      else if (el.type === "checkbox" && /\\.(flag|consent)$/.test(name)) {
        field.kind = "legal";
        field.label = text(form.querySelector(`[id="${el.getAttribute("aria-labelledby")}"]`));
      } else if (el.type === "checkbox") field.kind = "checkboxes";
      else if (el.type === "file") {
        field.kind = "file";
        field.accept = el.accept ? el.accept.split(",").map(a => a.trim()) : null;
      } else if (el.type === "tel") field.kind = "phone";
      else if (el.type === "date") field.kind = "date";
      else if (el.type === "number") field.kind = "number";
      else field.kind = "text";
      if (field.label === undefined) field.label = ["radio", "checkboxes"].includes(field.kind)
        ? text(group?.querySelector("legend"))
        : text(form.querySelector(`label[for="${el.id}"]`)) || el.placeholder;
      found.set(name, field);
    }
    field.required ||= el.required;
    if (el.type === "radio" || el.type === "checkbox" && field.kind === "checkboxes")
      field.options.push(text(form.querySelector(`label[for="${el.id}"]`)) || el.value);
  }
  return [...found.values()];
}"""

# What each field shows: a choice its checked option's text, a file its attached name.
_READ_BACK = """(form, kinds) => {
  const optionText = el => (form.querySelector(`label[for="${el.id}"]`)?.textContent || el.value)
    .replace(/\\s+/g, " ").trim();
  const out = {};
  for (const [name, kind] of Object.entries(kinds)) {
    const all = [...form.querySelectorAll(`[name="${CSS.escape(name)}"]`)];
    if (!all.length) continue;
    const el = all[0];
    if (kind === "file") out[name] = el.files[0]?.name || null;
    else if (kind === "radio") {
      const checked = all.find(e => e.checked);
      out[name] = checked ? optionText(checked) : null;
    } else if (kind === "checkboxes") out[name] = all.filter(e => e.checked).map(optionText);
    else if (kind === "legal") out[name] = el.checked ? "Yes" : "No";
    else out[name] = el.value;
  }
  return out;
}"""


class Recruitee:
    name = "recruitee"
    side_effects = [SideEffect("submit", "employer", "the application reaches the employer")]

    def __init__(self) -> None:
        self._kinds: dict[str, str] = {}  # how the page renders each field

    def detect(self, url: str, page: Page | None) -> bool:
        """A `*.recruitee.com` posting, or a posting page that loads Recruitee's career site."""
        parts = urlsplit(url)
        if not JOB.match(parts.path):
            return False
        if (parts.hostname or "").endswith(".recruitee.com"):
            return True
        return page is not None and page.locator(CAREER_SITE).count() > 0

    def posting(self, url: str) -> str:
        parts = urlsplit(url)
        match = JOB.match(parts.path)
        return f"https://{parts.hostname}/o/{match[1]}" if match else url

    def read_api(self, url: str) -> list[Field] | None:
        return None

    def read_form(self, page: Page) -> list[Field]:
        if not urlsplit(page.url).path.rstrip("/").endswith("/c/new"):
            page.goto(f"{self.posting(page.url)}/c/new")
        form = page.locator(FORM)
        try:
            form.wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        fields, self._kinds = [], {}
        for raw in form.evaluate(_READ):
            if raw["kind"] == "video":  # recorded in the page, or uploaded to a video service
                if raw["required"]:
                    raise Unsupported(f"form_shape: {raw['label']!r} is a video question")
                continue
            fields.append(_field(raw))
            self._kinds[raw["id"]] = raw["kind"]
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        for name, answer in answers.items():
            values = [answer] if isinstance(answer, str) else answer
            controls = page.locator(f"{FORM} [name='{name}']")
            match self._kinds[name]:
                case "file":
                    controls.first.set_input_files(values[0])
                case "radio" | "checkboxes":
                    for value in values:
                        if (option := _option(page, controls, value)) is not None:
                            _set(option, True)
                case "legal":
                    _set(controls.first, answer == "Yes")
                case "phone":  # the country picker follows the number's own prefix
                    controls.first.fill("")
                    controls.first.press_sequentially(values[0], delay=KEY_DELAY_MS)
                case _:
                    controls.first.fill(values[0])

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = page.locator(FORM).evaluate(_READ_BACK, self._kinds)
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        """Clicks submit; hCaptcha checks the visitor, then the page shows its thanks panel, the
        evidence, or a panel saying this address already applied."""
        browser.click(page.locator(f"{FORM} [data-testid=submit-application-form-button]"))
        panel = page.locator("[role=tabpanel]:not([hidden]):has([aria-live=assertive] h3)")
        try:
            panel.wait_for(timeout=SUBMIT_TIMEOUT_MS)
        except Timeout:
            if page.locator("iframe[src*='hcaptcha'][title*='challenge']").is_visible():
                raise Blocked("captcha: hCaptcha asked for a challenge") from None
            raise
        shown = " ".join((panel.locator("h3").text_content() or "").split())
        if panel.locator("a[href^='mailto:']").count():
            raise Blocked(f"already_applied: {shown}")
        return f"{page.url} ({shown})"


def _field(raw: dict[str, Any]) -> Field:
    kind = raw["kind"]
    label = raw["label"] + (" (a number)" if kind == "number" else "")
    options = {"legal": YES_NO, "radio": raw["options"], "checkboxes": raw["options"]}.get(kind)
    accept = None
    if raw["accept"]:
        accept = [e for a in raw["accept"] for e in EXTENSIONS.get(a, [])] or None
    return Field(
        raw["id"], label, KINDS[kind], raw["required"], options, raw.get("maxLength"), accept
    )


def _set(control: Locator, checked: bool) -> None:
    """Checks or unchecks a radio or checkbox, drawn hidden under its styled box, by a click
    event on the input itself."""
    if control.is_checked() != checked:
        control.dispatch_event("click")


def _option(page: Page, controls: Locator, value: str) -> Locator | None:
    """The radio or checkbox whose label is `value`."""
    for control in controls.all():
        label = page.locator(f"{FORM} label[for='{control.get_attribute('id')}']")
        text = (label.text_content() if label.count() else None) or control.get_attribute("value")
        if " ".join((text or "").split()) == value:
            return control
    return None
