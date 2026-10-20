"""Workable (`apply.workable.com` job pages and their application form), as
docs/adapters/workable.md observes.

Every field sits under an element whose `data-ui` is its id in Workable's form API, which
`read_api` reads. Nothing but an attached file reaches Workable before submit, so the
read-back is the page.
"""

import json
import re
import urllib.request
from contextlib import suppress
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser
from atsfill.core.adapter import Unsupported
from atsfill.core.browser import Locator, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOSTS = re.compile(r"^(apply|[\w-]+)\.workable\.com$")
JOB = re.compile(r"^(?:/[^/]+)?/j/([0-9A-F]{10})\b")
API = "https://apply.workable.com/api/v1/jobs/{code}/form"
APPLIED = re.compile(r"/api/v1/jobs/[0-9A-F]{10}/apply$")
FORM = "form[data-ui=application-form]"
COOKIES = "[data-ui=cookie-consent-decline]"
FORM_TIMEOUT_MS = 30_000
COOKIE_TIMEOUT_MS = 5_000
OPTIONS_TIMEOUT_MS = 5_000
CHECK_TIMEOUT_MS = 3_000
UPLOAD_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
YES_NO = ["Yes", "No"]
API_TYPES = {
    "text": FieldType.TEXT,
    "email": FieldType.TEXT,
    "paragraph": FieldType.TEXT,
    "number": FieldType.TEXT,
    "phone": FieldType.PHONE,
    "file": FieldType.FILE,
    "date": FieldType.DATE,
    "boolean": FieldType.CHOICE,
    "dropdown": FieldType.CHOICE,
}

# The fields in page order: each outermost `data-ui` element of a section, by what it holds.
_READ = """form => {
  const text = el => {
    if (!el) return "";
    const copy = el.cloneNode(true);
    copy.querySelectorAll("small").forEach(e => e.remove());
    return copy.textContent.replace(/\\s+/g, " ").trim();
  };
  const optionLabel = el =>
    text(document.getElementById((el.getAttribute("aria-labelledby") || "").split(" ")[1]));
  const found = [];
  for (const section of form.querySelectorAll("[data-ui=section-fields]")) {
    for (const el of section.querySelectorAll("[data-ui]:not([data-ui=calendar-icon])")) {
      if (el.parentElement.closest("[data-ui]") !== section) continue;
      const id = el.getAttribute("data-ui");
      const wrapper = [...section.children].find(c => c.contains(el));
      const named = el.matches("[aria-labelledby]") ? el : el.querySelector("[aria-labelledby]");
      const labelId = (named?.getAttribute("aria-labelledby") || "").split(" ")[0];
      const field = {id, label: text(document.getElementById(labelId) ||
                                     document.getElementById(`${id}_label`)),
        required: [...wrapper.querySelectorAll("strong")].some(s => s.textContent.trim() === "*")};
      const input = el.matches("input, textarea") ? el : null;
      if (el.querySelector("[data-ui=add-section]")) {
        field.kind = "group";
        field.required = !document.getElementById(`${id}_label`)?.querySelector("small");
      } else if (input?.type === "file") {
        field.kind = "file";
        field.accept = input.accept ? input.accept.split(",").filter(a => a.startsWith(".")) : null;
      } else if (el.querySelector("input[type=tel]")) field.kind = "phone";
      else if (el.matches("[data-input-type=select]")) field.kind = "select";
      else if (el.matches("[role=radiogroup]")) {
        const radios = [...el.querySelectorAll("[role=radio]")];
        const values = radios.map(r => r.querySelector("input").value);
        const yesNo = values.length === 2 && values.includes("true") && values.includes("false");
        field.kind = yesNo ? "yes_no" : "radio";
        field.options = yesNo ? null : radios.map(optionLabel);
      } else if (el.matches("[role=group]")) {
        field.kind = "checkboxes";
        field.options = [...el.querySelectorAll("[role=checkbox]")].map(optionLabel);
      } else if (el.querySelector("input[type=checkbox]")) {
        field.kind = "consent";
        field.required = true;  // unmarked, but the API's: it takes only a Yes
        field.label = text(document.getElementById(labelId));
      } else if (input && el.parentElement.querySelector("[data-ui=calendar-icon]")) {
        field.kind = "date";
        field.format = input.placeholder;
      } else if (input) {
        field.kind = input.id ? "text" : "number";  // a number input alone has no id
        field.maxLength = input.maxLength > 0 ? input.maxLength : null;
      } else field.kind = el.tagName.toLowerCase();
      found.push(field);
    }
  }
  return found;
}"""

# What each field shows: a choice its checked option's label, a file its attached name (a photo
# its preview's alt), the phone its country's dial code then its number.
_READ_BACK = """(form, kinds) => {
  const text = el => el ? el.textContent.replace(/\\s+/g, " ").trim() : null;
  const optionLabel = el =>
    text(document.getElementById((el.getAttribute("aria-labelledby") || "").split(" ")[1]));
  const out = {};
  for (const [id, kind] of Object.entries(kinds)) {
    const el = form.querySelector(`[data-ui="${id}"]`);
    if (!el) continue;
    if (kind === "file") {
      const zone = el.closest("[data-role=dropzone]");
      const preview = zone?.querySelector("[data-role=preview] img");  // a photo, named by its alt
      out[id] = preview ? preview.alt : text(zone?.querySelector("[data-id=filename]"));
    } else if (kind === "phone") {
      const dial = (el.querySelector("button[aria-label]")?.getAttribute("aria-label") || "")
        .match(/\\((\\+\\d+)\\)/);
      const number = el.querySelector("input[type=tel]").value;
      out[id] = dial && number ? `${dial[1]} ${number}` : null;
    } else if (kind === "yes_no") {
      const value = el.querySelector("[role=radio][aria-checked=true] input")?.value;
      out[id] = value === "true" ? "Yes" : value === "false" ? "No" : null;
    } else if (kind === "radio") {
      const radio = el.querySelector("[role=radio][aria-checked=true]");
      out[id] = radio ? optionLabel(radio) : null;
    } else if (kind === "checkboxes") {
      out[id] = [...el.querySelectorAll("[role=checkbox][aria-checked=true]")].map(optionLabel);
    } else if (kind === "consent") {
      out[id] = el.querySelector("[role=checkbox]").getAttribute("aria-checked") === "true"
        ? "Yes" : "No";
    } else if (kind === "select") {
      out[id] = el.querySelector("[role=combobox]").value.trim() || null;
    } else if (kind === "number") out[id] = el.value.replace(/,/g, "");
    else out[id] = el.value;
  }
  return out;
}"""


class Workable:
    name = "workable"
    side_effects = [
        SideEffect("upload", "hiring_system", "an attached file is stored by Workable at once"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._kinds: dict[str, str] = {}  # how the page renders each field
        self._formats: dict[str, str] = {}  # a date input's placeholder, MM/DD/YYYY or other

    def detect(self, url: str, page: Page | None) -> bool:
        return _code(url) is not None

    def posting(self, url: str) -> str:
        return f"https://apply.workable.com/j/{_code(url) or url}"

    def read_api(self, url: str) -> list[Field] | None:
        """The fields from the form API the page itself loads, unless it has a required group."""
        code = _code(url)
        if code is None:
            return None
        try:
            request = urllib.request.Request(
                API.format(code=code), headers={"User-Agent": "atsfill"}
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                return _api_fields(json.load(response))
        except OSError, ValueError, KeyError, TypeError:
            return None

    def read_form(self, page: Page) -> list[Field]:
        if "/apply" not in urlsplit(page.url).path:
            page.goto(f"{self.posting(page.url)}/apply/")
        form = page.locator(FORM)
        try:
            form.wait_for(timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        _decline_cookies(page)
        fields, self._kinds, self._formats = [], {}, {}
        for raw in form.evaluate(_READ):
            field = self._field(page, raw)
            if field is not None:
                fields.append(field)
                self._kinds[field.id] = raw["kind"]
                if raw["kind"] == "date":
                    self._formats[field.id] = raw["format"]
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        _decline_cookies(page, wait=False)
        # attaching the resume empties the photo, so it goes first
        for field_id, answer in sorted(answers.items(), key=lambda a: a[0] != "resume"):
            values = [answer] if isinstance(answer, str) else answer
            field = page.locator(f"{FORM} [data-ui='{field_id}']").first
            match self._kinds[field_id]:
                case "file":  # a photo goes through a crop dialog first
                    field.set_input_files(values[0])
                    zone = field.locator("xpath=ancestor::*[@data-role='dropzone']")
                    crop = page.locator("button[data-ui=crop-image]")
                    with suppress(Timeout):
                        zone.locator("[data-id=filename]").or_(crop).first.wait_for(
                            timeout=UPLOAD_TIMEOUT_MS
                        )
                        if crop.is_visible():
                            crop.click()
                            crop.wait_for(state="hidden", timeout=UPLOAD_TIMEOUT_MS)
                case "phone":  # the number's dial code picks the country
                    number = field.locator("input[type=tel]")
                    number.fill("")
                    number.press_sequentially(values[0], delay=20)
                case "date":
                    if typed := _typed_date(values[0], self._formats[field_id]):
                        field.click()
                        field.press_sequentially(typed, delay=20)
                        field.press("Tab")
                case "yes_no":
                    value = {"Yes": "true", "No": "false"}.get(values[0])
                    radio = field.locator("[role=radio]").filter(
                        has=page.locator(f"input[value='{value}']")
                    )
                    if value and radio.count():
                        _check(radio, True)
                case "radio":
                    _pick(page, field, "[role=radio]", values[0])
                case "checkboxes":
                    for value in values:
                        _pick(page, field, "[role=checkbox]", value)
                case "consent":  # the box, not its label: the label holds a link
                    _check(field.locator("[role=checkbox]"), answer == "Yes")
                case "select":
                    _select(page, field_id, values[0])
                case _:
                    field.fill(values[0])

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = page.locator(FORM).evaluate(_READ_BACK, self._kinds)
        for field_id, fmt in self._formats.items():
            values[field_id] = _iso_date(values.get(field_id) or "", fmt)
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        """Clicks submit; the evidence is the `/apply` call's answer, after Turnstile's check."""
        with page.expect_response(
            lambda r: r.request.method == "POST" and APPLIED.search(r.url) is not None,
            timeout=SUBMIT_TIMEOUT_MS,
        ) as applied:
            browser.click(page.locator(f"{FORM} button[data-ui=apply-button]"))
        response = applied.value
        if not response.ok:
            raise RuntimeError(f"apply answered {response.status}: {response.text()[:200]}")
        return f"{response.url} {response.status}"

    def _field(self, page: Page, raw: dict[str, Any]) -> Field | None:
        kind, options = raw["kind"], raw.get("options")
        match kind:
            case "text" | "number":
                field_type = FieldType.TEXT
            case "date":
                field_type = FieldType.DATE
            case "phone":
                field_type = FieldType.PHONE
            case "file":
                field_type = FieldType.FILE
            case "yes_no" | "consent":
                field_type, options = FieldType.CHOICE, YES_NO
            case "radio":
                field_type = FieldType.CHOICE
            case "checkboxes":
                field_type = FieldType.MULTI_CHOICE
            case "select":
                field_type, options = FieldType.CHOICE, _options(page, raw["id"])
            case "group" if not raw["required"]:
                return None  # an optional education or experience entry, left out
            case _:
                raise Unsupported(f"form_shape: {raw['id']} is a {kind}")
        return Field(
            raw["id"],
            raw["label"],
            field_type,
            raw["required"],
            options,
            raw.get("maxLength"),
            raw.get("accept"),
        )


def _code(url: str) -> str | None:
    """The posting's shortcode, from `apply.workable.com[/<account>]/j/<code>`."""
    parts = urlsplit(url)
    if not HOSTS.match(parts.hostname or ""):
        return None
    match = JOB.match(parts.path)
    return match[1] if match else None


def _api_fields(form: list[dict[str, Any]]) -> list[Field] | None:
    fields = []
    for entry in (f for section in form for f in section["fields"]):
        kind = entry["type"]
        if kind == "group":
            if entry["required"]:
                return None
            continue
        options = [o["value"].strip() for o in entry.get("options") or []] or None
        if kind == "multiple":
            single = entry.get("singleOption", True)
            field_type = FieldType.CHOICE if single else FieldType.MULTI_CHOICE
        elif kind == "boolean":
            field_type, options = FieldType.CHOICE, YES_NO
        elif kind in API_TYPES:
            field_type = API_TYPES[kind]
        else:
            return None
        accept = entry.get("supportedFileTypes") if kind == "file" else None
        fields.append(
            Field(
                entry["id"],
                entry.get("label", ""),
                field_type,
                entry["required"],
                options,
                entry.get("maxLength"),
                accept,
            )
        )
    return fields


def _decline_cookies(page: Page, wait: bool = True) -> None:
    """Declines the cookie banner, a modal dialog that leaves the form inert while it is open;
    it shows a moment after the page."""
    decline = page.locator(COOKIES)
    with suppress(Timeout):
        if wait:
            decline.wait_for(timeout=COOKIE_TIMEOUT_MS)
        if decline.is_visible():
            decline.click()


def _exact(value: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(value)}\s*$")


def _pick(page: Page, field: Locator, role: str, value: str) -> None:
    """Checks the option labelled `value`, unless it is already checked. An option names its
    label second in `aria-labelledby`, after the question."""
    for option in field.locator(role).all():
        label_id = (option.get_attribute("aria-labelledby") or "").split()[-1]
        if re.match(_exact(value), page.locator(f"[id='{label_id}']").text_content() or ""):
            _check(option, True)
            return


def _check(option: Locator, checked: bool) -> None:
    """Clicks a radio or checkbox unless it already is `checked`. Its `aria-checked` is the
    form's state: the hidden input it holds is remounted, and catches up, on a later render."""
    state = "true" if checked else "false"
    if option.get_attribute("aria-checked") == state:
        return
    browser.click(option)
    with suppress(Timeout):
        option.and_(option.page.locator(f"[aria-checked={state}]")).wait_for(
            timeout=CHECK_TIMEOUT_MS
        )


def _options(page: Page, field_id: str) -> list[str]:
    """A dropdown's options, from opening it."""
    box = page.locator(f"[id='input_{field_id}_input']")
    box.click()
    labels = page.locator(f"[id='input_{field_id}_listbox'] [data-role=option-label]")
    with suppress(Timeout):
        labels.first.wait_for(timeout=OPTIONS_TIMEOUT_MS)
    options = [t.strip() for t in labels.all_text_contents()]
    box.press("Escape")
    _closed(page, field_id)
    return options


def _select(page: Page, field_id: str, value: str) -> None:
    """Opens a dropdown and clicks the option with the exact text; none leaves it empty."""
    page.locator(f"[id='input_{field_id}_input']").click()
    option = page.locator(f"[id='input_{field_id}_listbox'] [role=option]").filter(
        has=page.locator("[data-role=option-label]", has_text=_exact(value))
    )
    try:
        option.first.wait_for(timeout=OPTIONS_TIMEOUT_MS)
    except Timeout:
        page.keyboard.press("Escape")
    else:
        option.first.click()
    _closed(page, field_id)


def _closed(page: Page, field_id: str) -> None:
    """Waits for a dropdown's list to leave the page: closing, it still takes clicks."""
    with suppress(Timeout):
        page.locator(f"[id='input_{field_id}_listbox']").wait_for(
            state="detached", timeout=OPTIONS_TIMEOUT_MS
        )


def _typed_date(answer: str, fmt: str) -> str | None:
    """The digits a date input takes for a YYYY-MM-DD answer, in its placeholder's order."""
    try:
        day = date.fromisoformat(answer)
    except ValueError:
        return None
    parts = {"YYYY": f"{day.year:04}", "MM": f"{day.month:02}", "DD": f"{day.day:02}"}
    return "".join(parts[p] for p in re.findall(r"YYYY|MM|DD", fmt.upper()))


def _iso_date(shown: str, fmt: str) -> str | None:
    """A date input's value, shown in its placeholder's order, as YYYY-MM-DD."""
    order = re.findall(r"YYYY|MM|DD", fmt.upper())
    numbers = re.findall(r"\d+", shown)
    if len(order) != 3 or len(numbers) != 3:
        return None
    parts = dict(zip(order, numbers, strict=True))
    return f"{parts['YYYY']}-{parts['MM']}-{parts['DD']}"
