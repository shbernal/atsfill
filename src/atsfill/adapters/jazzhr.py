"""JazzHR (`<account>.applytojob.com` job pages, which hold their application form), as
docs/adapters/jazzhr.md observes.

The form is plain HTML posted as a whole on submit, so the inputs are the read-back. A
questionnaire's checkboxes are joined into its hidden input only on submit, so they are read
by which boxes are checked.
"""

import re
from contextlib import suppress
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser
from atsfill.core.adapter import Blocked, Gap, Unsupported
from atsfill.core.browser import Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, Is, SideEffect

HOST = re.compile(r"^[\w-]+\.applytojob\.com$")
JOB = re.compile(r"^/apply/([A-Za-z0-9]{10})\b")
FORM = "#form_submit_new_resume"
CAPTCHA = "#resumator-recaptcha iframe[title=reCAPTCHA]"
CHALLENGE = "iframe[src*='recaptcha/api2/bframe']"
RESUME_TYPES = [".pdf", ".doc", ".docx", ".odt", ".txt", ".rtf"]  # as the page states them
YES_NO = ["Yes", "No"]
DISABILITY = "eeoc_disability"  # form CC-305: its name and date are signed once one is declared
SIGNED = ("eeoc_disability_signature", "eeoc_disability_date")
DECLARED = re.compile(r"^\s*YES\b", re.IGNORECASE)
FORM_TIMEOUT_MS = 20_000
CAPTCHA_TIMEOUT_MS = 20_000
SUBMIT_TIMEOUT_MS = 90_000

# The fields in page order: an input, select or textarea by its id, a questionnaire's
# checkboxes by their hidden answer input, radios by their name. An id drops `resumator-`,
# `questionnaire-` and `-value`.
_READ = """form => {
  const text = el => {
    if (!el) return "";
    const copy = el.cloneNode(true);
    copy.querySelectorAll(".asterisk, select, input").forEach(e => e.remove());
    return copy.textContent.replace(/\\s+/g, " ").trim();
  };
  const short = id => id.replace(/^resumator-/, "").replace(/^questionnaire-/, "")
    .replace(/-value$/, "");
  const starred = el => !!el?.querySelector(".asterisk");
  const shown = el => el.offsetParent !== null;
  const disability = "#resumator-new-disability-message";
  const found = [], radios = new Set();
  const pasted = "resumator-resumetext-value";  // "Paste resume", the file's alternative
  const pasteable = !!form.querySelector(`#${pasted}`);
  const controls = form.querySelectorAll(
    "input:not([type=hidden]), select, textarea, input.resumator-questionnaire-checkbox-answer");
  for (const el of controls) {
    if (["resumator-xml-value", "g-recaptcha-response"].includes(el.id)
        || el.classList.contains("resumator-questionnaire-checkbox")) continue;
    const hidden = el.type === "file" ? el.id !== "resumator-resume-value"
      : !shown(el) && el.type !== "hidden" && el.id !== pasted;
    if (hidden) continue;
    const group = el.closest(".form-group, .resumator-field-wrapper");
    const groupLabel = group?.querySelector(".control-label, .resumator-label");
    const own = el.id && form.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    const field = {id: short(el.id), label: text(own || el.closest("label") || groupLabel),
                   required: starred(own || groupLabel), selector: `#${CSS.escape(el.id)}`};
    if (el.type === "hidden") {  // a questionnaire's checkboxes
      field.kind = "checkboxes";
      field.options = [...group.querySelectorAll(".resumator-questionnaire-checkbox")]
        .map(b => b.value);
    } else if (el.type === "radio") {
      if (radios.has(el.name)) continue;
      radios.add(el.name);
      const named = [...form.querySelectorAll(`input[type=radio][name="${el.name}"]`)];
      field.kind = "radio";
      field.id = short(el.name);
      field.selector = `input[name="${el.name}"]`;
      field.label = el.closest(disability) ? "Disability status" : text(groupLabel);
      field.options = named.map(r => text(r.closest("label")));
    } else if (el.type === "checkbox") {
      field.kind = "consent";
    } else if (el.tagName === "SELECT") {
      field.kind = "select";
      field.options = [...el.options].filter(o => !o.value.startsWith("resumator_no_selection")
        && !/^no answer$/i.test(o.text.trim())).map(o => o.text.trim());
    } else if (el.type === "file" || el.id === pasted) {
      field.kind = el.type === "file" ? "file" : "pasted";
      field.label = text(groupLabel);
      field.required = starred(groupLabel);
      if (pasteable) field.oneOf = "resume";
    } else {
      field.kind = el.classList.contains("resumator-datepicker") ? "date"
        : el.type === "tel" ? "tel" : "text";
      if (!own && el.placeholder) field.label = el.placeholder;  // city, state, postal
      if (el.closest("#resumator-address")) field.required = starred(groupLabel);
      field.maxLength = el.maxLength > 0 ? el.maxLength : null;
    }
    if (el.closest(disability) && field.kind !== "radio")
      field.label = `Disability status, ${field.label}`;
    found.push(field);
  }
  return found;
}"""

# What each field holds: an option's label for a select or radios, the checked boxes' values,
# the attached file's name.
_READ_BACK = """(form, fields) => {
  const text = el => el ? el.textContent.replace(/\\s+/g, " ").trim() : null;
  const out = {};
  for (const [id, [kind, selector]] of Object.entries(fields)) {
    if (kind === "radio") {
      const checked = [...form.querySelectorAll(selector)].find(r => r.checked);
      out[id] = checked ? text(checked.closest("label")) : null;
      continue;
    }
    const el = form.querySelector(selector);
    if (!el) continue;
    if (kind === "checkboxes") {
      out[id] = [...el.closest(".form-group, .resumator-field-wrapper")
        .querySelectorAll(".resumator-questionnaire-checkbox:checked")].map(b => b.value);
    } else if (kind === "consent") out[id] = el.checked ? "Yes" : "No";
    else if (kind === "select") out[id] = el.selectedOptions[0]?.text.trim() || null;
    else if (kind === "file") out[id] = el.files.length ? el.files[0].name : null;
    else out[id] = el.value;
  }
  return out;
}"""


class JazzHR:
    name = "jazzhr"
    known_gaps: dict[str, Gap] = {}
    side_effects = [SideEffect("submit", "employer", "the application reaches the employer")]

    def __init__(self) -> None:
        self._fields: dict[str, tuple[str, str]] = {}  # each field's kind and selector

    def detect(self, url: str, page: Page | None) -> bool:
        return _job(url) is not None

    def posting(self, url: str) -> str:
        job = _job(url)
        return f"https://{job[0]}/apply/{job[1]}" if job else url

    def browser_read(self, page: Page) -> list[Field]:
        form = page.locator(FORM)
        try:
            form.wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        fields, self._fields = [], {}
        for raw in form.evaluate(_READ):
            fields.append(_field(raw))
            self._fields[raw["id"]] = (raw["kind"], raw["selector"])
        return _signed_on_disability(fields)

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        form = page.locator(FORM)
        for field_id, answer in answers.items():
            values = [answer] if isinstance(answer, str) else answer
            kind, selector = self._fields[field_id]
            field = form.locator(selector)
            match kind:
                case "file":  # behind "Attach resume", which shows the input
                    with suppress(Timeout):
                        attach = page.locator("#resumator-choose-upload")
                        if attach.is_visible():
                            attach.click()
                    field.set_input_files(values[0])
                case "pasted":  # behind "Paste resume", from the choice or the file's own link
                    for link in ("#resumator-choose-paste", "#resumator-resume-switch-paste"):
                        if page.locator(link).is_visible():
                            page.locator(link).click()
                            break
                    field.fill(values[0])
                case "select":
                    field.select_option(label=values[0])
                case "radio":
                    for radio in field.all():
                        label = radio.locator("xpath=ancestor::label[1]")
                        if " ".join(label.inner_text().split()) == values[0]:
                            radio.check()
                case "checkboxes":
                    group = field.locator("xpath=ancestor::div[contains(@class,'form-group')][1]")
                    for box in group.locator(".resumator-questionnaire-checkbox").all():
                        box.set_checked(box.get_attribute("value") in values)
                case "consent":
                    field.set_checked(answer == "Yes")
                case "date":  # jQuery UI's datepicker, set up with yy-mm-dd
                    field.fill(values[0])
                    field.press("Escape")
                case _:
                    field.fill(values[0])

    def shown_fields(self, page: Page) -> list[str]:
        return [raw["id"] for raw in page.locator(FORM).evaluate(_READ)]

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = page.locator(FORM).evaluate(_READ_BACK, self._fields)
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        """Ticks the reCAPTCHA box, then clicks submit; the evidence is the page the form posts
        to. An image challenge instead of the tick is `Blocked`."""
        captcha = page.locator(CAPTCHA)
        if captcha.count():
            captcha.content_frame.locator("#recaptcha-anchor").click()
            token = page.locator("#g-recaptcha-response")
            try:
                page.wait_for_function(
                    "el => el.value !== ''", arg=token.element_handle(), timeout=CAPTCHA_TIMEOUT_MS
                )
            except Timeout:
                if page.locator(CHALLENGE).is_visible():
                    raise Blocked("captcha: reCAPTCHA image challenge") from None
                raise
        with page.expect_navigation(timeout=SUBMIT_TIMEOUT_MS) as posted:
            browser.click(page.locator(f"{FORM} #resumator-submit-resume"))
        response = posted.value
        if response is None or not response.ok:
            status = response.status if response else "no response"
            raise RuntimeError(f"the form post answered {status}")
        if page.locator(FORM).count():
            raise RuntimeError("the form came back after submit")
        return f"{response.url} {response.status}"


def _job(url: str) -> tuple[str, str] | None:
    """The host and job code of `<account>.applytojob.com/apply/<code>[/<slug>]`."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if not HOST.match(host):
        return None
    match = JOB.match(parts.path)
    return (host, match[1]) if match else None


def _signed_on_disability(fields: list[Field]) -> list[Field]:
    """The disability form's name and date, required by the page once a disability is
    declared."""
    status = next((f for f in fields if f.id == DISABILITY), None)
    declared = [o for o in (status.options if status else None) or [] if DECLARED.match(o)]
    if not declared:
        return fields
    signed = Is(DISABILITY, declared)
    return [replace(f, required_when=signed) if f.id in SIGNED else f for f in fields]


def _field(raw: dict[str, Any]) -> Field:
    kind, options = raw["kind"], raw.get("options")
    match kind:
        case "text" | "pasted":
            field_type = FieldType.TEXT
        case "date":
            field_type = FieldType.DATE
        case "tel":
            field_type = FieldType.PHONE
        case "file":
            field_type = FieldType.FILE
        case "select" | "radio":
            field_type = FieldType.CHOICE
        case "checkboxes":
            field_type = FieldType.MULTI_CHOICE
        case "consent":
            field_type, options = FieldType.CHOICE, YES_NO
        case _:
            reason = f"unhandled_rendering: {raw['id']} is a {kind}"
            return Field(
                raw["id"], raw["label"], FieldType.TEXT, raw["required"], unfillable=reason
            )
    accept = RESUME_TYPES if kind == "file" else None
    return Field(
        raw["id"],
        raw["label"],
        field_type,
        raw["required"],
        options,
        raw.get("maxLength"),
        accept,
        one_of=raw.get("oneOf"),
    )
