"""Teamtailor (`<company>.teamtailor.com` and career sites on the company's domain), as
docs/adapters/teamtailor.md observes.

The form posts nothing before submit except the resume, which goes to Teamtailor's storage as
soon as it is picked. The read-back is the form's `FormData`.
"""

import re
import time
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser
from atsfill.core.adapter import Unsupported
from atsfill.core.browser import Locator, Page
from atsfill.core.results import Answer, Field, FieldType, SideEffect

FORM = "#job-application-form"
JOB = re.compile(r"/jobs/(\d+)[^/]*")
SUBMIT_TIMEOUT_MS = 90_000
UPLOAD_TIMEOUT_S = 60
QUESTION_TYPES = {
    "text": FieldType.TEXT,
    "number": FieldType.TEXT,
    "date": FieldType.DATE,
    "boolean": FieldType.CHOICE,
    "choice": FieldType.CHOICE,
    "choices": FieldType.MULTI_CHOICE,
}
STANDARD_TYPES = {
    "text": FieldType.TEXT,
    "email": FieldType.TEXT,
    "textarea": FieldType.TEXT,
    "tel": FieldType.PHONE,
    "checkbox": FieldType.CHOICE,
    "file": FieldType.FILE,
}
CHECKBOX = ["Yes", "No"]

# Every field of the form, described from the page. A custom question's id is
# `question:<Teamtailor question id>`; the others are the input's name.
_READ = """form => {
  const label = el => {
    if (!el) return "";
    const copy = el.cloneNode(true);
    copy.querySelectorAll("sup, .sr-only").forEach(e => e.remove());
    return copy.textContent.replace(/\\s+/g, " ").trim();
  };
  const required = el => !!el?.querySelector("sup[data-asterisk]");
  const fields = [];
  for (const el of form.querySelectorAll("input, textarea")) {
    if (el.closest(".question") || el.disabled) continue;
    const kind = el.tagName === "TEXTAREA" ? "textarea" : el.type;
    let id = el.name;
    if (kind === "file") id = `candidate[${el.id.replace(/^candidate_/, "")}]`;
    if (!["text", "email", "tel", "textarea", "checkbox", "file"].includes(kind) || !id) continue;
    if (/linkedin|referrer|social_image/.test(id)) continue;
    const host = el.labels?.[0] ?? el.closest("[data-controller]")?.querySelector("label");
    fields.push({id, kind, label: label(host), required: required(host),
                 maxLength: el.maxLength > 0 ? el.maxLength : null,
                 accept: el.accept ? el.accept.split(",") : null});
  }
  for (const q of form.querySelectorAll(".question")) {
    const qid = q.querySelector("input[name$='[question_id]']")?.value;
    const named = q.querySelector("[name^='candidate[answers_attributes]']:not([type=hidden])");
    const kind = /picked_video/.test(named?.name) ? "video"
      : q.querySelector("input[type=file]") && !named ? "file"
      : (named?.name.match(/\\]\\[(\\w+)\\](\\[\\])?$/)?.[1] ?? "unknown");
    const item = value => q.querySelector(`[role^=menuitem][data-value="${value}"]`);
    const options = [...q.querySelectorAll("input[type=radio], input[type=checkbox]")]
      .map(e => label(e.labels?.[0]) || item(e.value)?.dataset.searchText || e.value);
    const menu = [...q.querySelectorAll("[role^=menuitem]")].map(e => e.dataset.searchText);
    fields.push({id: `question:${qid}`, kind, question: true,
                 label: label(q.querySelector("legend, label")),
                 required: q.dataset.questionMandatory === "true",
                 options: options.length ? options : menu.length ? menu : null});
  }
  return fields;
}"""

# What the form will send: each field's value, a choice as its option's text, a file as the
# uploaded file's name.
_READ_BACK = """form => {
  const data = new FormData(form);
  const text = e => {
    const label = e.labels?.[0];
    if (label) return label.textContent.replace(/\\s+/g, " ").trim();
    const item = e.closest(".question")?.querySelector(`[role^=menuitem][data-value="${e.value}"]`);
    return item?.dataset.searchText ?? e.value;
  };
  const file = url => url ? decodeURIComponent(new URL(url).pathname.split("/").pop()) : null;
  const out = {};
  for (const el of form.querySelectorAll("input[type=checkbox][name^='candidate['][value='1']")) {
    out[el.name] = data.getAll(el.name).includes("1") ? "Yes" : "No";
  }
  for (const name of ["first_name", "last_name", "email", "phone"]) {
    out[`candidate[${name}]`] = data.get(`candidate[${name}]`);
  }
  for (const [name, value] of data) {
    if (/^candidate\\[\\w+_remote_url\\]$/.test(name)) out[name] = file(value);
    else if (/cover_letter/.test(name)) out[name] = value;
  }
  for (const q of form.querySelectorAll(".question")) {
    const id = `question:${q.querySelector("input[name$='[question_id]']")?.value}`;
    const checked = [...q.querySelectorAll("input:checked")];
    const named = q.querySelector("[name^='candidate[answers_attributes]']:not([type=hidden])");
    if (q.querySelector("input[type=checkbox]")) out[id] = checked.map(text);
    else if (q.querySelector("input[type=radio]")) out[id] = checked[0] ? text(checked[0]) : null;
    else if (named) out[id] = named.value;
  }
  return out;
}"""


class Teamtailor:
    name = "teamtailor"
    side_effects = [
        SideEffect("upload", "hiring_system", "a picked file is stored by Teamtailor at once"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._fields: dict[str, Field] = {}

    def detect(self, url: str, page: Page | None) -> bool:
        """A `*.teamtailor.com` job, or a job page with Teamtailor's "powered by" link."""
        if not JOB.search(urlsplit(url).path):
            return False
        if (urlsplit(url).hostname or "").endswith(".teamtailor.com"):
            return True
        return page is not None and page.locator("a[href*='//www.teamtailor.com']").count() > 0

    def posting(self, url: str) -> str:
        parts = urlsplit(url)
        match = JOB.search(parts.path)
        assert match, url
        return f"https://{parts.hostname}/jobs/{match[1]}"

    def read_api(self, url: str) -> list[Field] | None:
        return None

    def read_form(self, page: Page) -> list[Field]:
        """Opens the form's own page, never the overlay nor "Apply with LinkedIn"."""
        parts = urlsplit(page.url)
        job = JOB.search(parts.path)
        assert job, page.url  # detected on a job URL
        page.goto(f"{parts.scheme}://{parts.netloc}{parts.path[: job.end()]}/applications/new")
        form = page.locator(FORM)
        form.wait_for()
        fields = []
        for raw in form.evaluate(_READ):
            field = _field(raw)
            if field is None and raw["required"]:
                raise Unsupported(f"form_shape: {raw['label']!r} is a {raw['kind']} question")
            if field is not None:
                fields.append(field)
        self._fields = {f.id: f for f in fields}
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        form = page.locator(FORM)
        _decline_cookies(page)
        for field_id, answer in answers.items():
            field = self._fields[field_id]
            host = self._host(form, field_id)
            match field.type, answer:
                case FieldType.FILE, str():
                    host.locator("input[type=file]").set_input_files(answer)
                case FieldType.CHOICE, str() if host.locator("input[type=checkbox]").count():
                    box = host.locator("input[type=checkbox]")
                    if box.is_checked() != (answer == "Yes"):
                        _click(page, page.locator(f"label[for='{box.get_attribute('id')}']"))
                case FieldType.CHOICE | FieldType.MULTI_CHOICE, _:
                    for value in [answer] if isinstance(answer, str) else answer:
                        _pick(page, host, value)
                case FieldType.TEXT | FieldType.DATE | FieldType.PHONE, str():
                    box = host.locator("input:not([type=hidden]):not([type=search]), textarea")
                    box.first.fill(answer)
                case kind, _:
                    raise Unsupported(
                        f"form_shape: cannot fill {field_id}, a {kind}, with {answer!r}"
                    )
        self._wait_for_uploads(form, answers)

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = page.locator(FORM).evaluate(_READ_BACK)
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        _click(page, page.locator(f"{FORM} [type=submit]"))
        page.wait_for_url(re.compile(r"/applications/\d+/thanks"), timeout=SUBMIT_TIMEOUT_MS)
        return page.url

    def _host(self, form: Locator, field_id: str) -> Locator:
        """The element holding a field's inputs: its question, else its input's container."""
        if field_id.startswith("question:"):
            qid = field_id.partition(":")[2]
            return form.locator(".question").filter(
                has=form.page.locator(f"input[name$='[question_id]'][value='{qid}']")
            )
        if field_id.endswith("_remote_url]"):
            dom_id = "candidate_" + field_id[len("candidate[") : -1]
            return form.locator("[data-controller~='forms--inputs--upload']").filter(
                has=form.page.locator(f"#{dom_id}")
            )
        return form.locator(f"[name='{field_id}']").first.locator("xpath=..")

    def _wait_for_uploads(self, form: Locator, answers: dict[str, Answer]) -> None:
        files = {
            i: a.rsplit("/", 1)[-1]
            for i, a in answers.items()
            if self._fields[i].type is FieldType.FILE and isinstance(a, str)
        }
        deadline = time.monotonic() + UPLOAD_TIMEOUT_S
        while files and time.monotonic() < deadline:
            held = form.evaluate(_READ_BACK)
            if all(held.get(i) == name for i, name in files.items()):
                return
            form.page.wait_for_timeout(500)


def _field(raw: dict[str, Any]) -> Field | None:
    """The field a raw description is, None for a kind atsfill cannot fill."""
    kinds = QUESTION_TYPES if raw.get("question") else STANDARD_TYPES
    kind = kinds.get(raw["kind"])
    if kind is None:
        return None
    options = raw.get("options")
    if raw["kind"] == "checkbox":
        options = CHECKBOX
    return Field(
        raw["id"],
        raw["label"],
        kind,
        raw["required"],
        options,
        raw.get("maxLength"),
        raw.get("accept"),
    )


def _decline_cookies(page: Page) -> None:
    """Closes the cookie banner, a modal dialog that leaves the form inert while it is open."""
    decline = page.locator("[data-action*='cookies--alert#disableAll']").first
    if decline.is_visible():
        decline.click()


def _click(page: Page, target: Locator) -> None:
    """Clicks, first declining the cookie banner when it covers the target: it comes back."""
    try:
        browser.click(target)
    except browser.Covered:
        _decline_cookies(page)
        browser.click(target)


def _pick(page: Page, question: Locator, value: str) -> None:
    """Checks the option whose text is `value`, a radio or checkbox label or a dropdown item."""
    label = question.locator("label:visible").filter(
        has_text=re.compile(rf"^\s*{re.escape(value)}\s*$")
    )
    if label.count():
        _click(page, label.first)
        return
    trigger = question.locator("button[aria-haspopup]").first
    if trigger.get_attribute("aria-expanded") != "true":
        _click(page, trigger)
    _click(page, question.locator(f"[role^=menuitem][data-search-text='{value}']").first)
