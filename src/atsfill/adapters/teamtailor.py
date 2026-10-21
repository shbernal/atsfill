"""Teamtailor (`<employer>.teamtailor.com` and career sites on the employer's domain), as
docs/adapters/teamtailor.md observes.

The form posts nothing before submit except the resume, which goes to Teamtailor's storage as
soon as it is picked. The read-back is the form's `FormData`.
"""

import re
import time
import unicodedata
from pathlib import PurePath
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser, matching
from atsfill.core.adapter import Gap, NotTaken, Unsupported, known_gap
from atsfill.core.browser import Locator, Page
from atsfill.core.results import Answer, Field, FieldType, SideEffect

FORM = "#job-application-form"
# A custom question, or a qualifying one: the rest of the form stays disabled until it is
# answered with the answer the employer requires.
QUESTION = ".question, [data-careersite--form-target=qualifyingAnswers]"
LOCKED = "[data-careersite--form-target=formContentWrapper].cursor-not-allowed"
LOCATION = "candidate[location][query]"
CAREER_SITE = "a[href*='//www.teamtailor.com'], script[src*='teamtailor-cdn.com/assets/careersite']"
JOB = re.compile(r"/jobs/(\d+)[^/]*")
SUBMIT_TIMEOUT_MS = 90_000
FOLLOW_UP_TIMEOUT_MS = 1_000
UPLOAD_TIMEOUT_S = 60
UPLOAD_POLL_MS = 500
KEY_DELAY_MS = 30
QUESTION_TYPES = {
    "text": FieldType.TEXT,
    "number": FieldType.TEXT,
    "date": FieldType.DATE,
    "range": FieldType.CHOICE,
    "boolean": FieldType.CHOICE,
    "choice": FieldType.CHOICE,
    "choices": FieldType.MULTI_CHOICE,
}
STANDARD_TYPES = {
    "text": FieldType.TEXT,
    "email": FieldType.TEXT,
    "textarea": FieldType.TEXT,
    "tel": FieldType.PHONE,
    "location": FieldType.TYPEAHEAD,
    "checkbox": FieldType.CHOICE,
    "choices": FieldType.MULTI_CHOICE,
    "file": FieldType.FILE,
}
CHECKBOX = ["Yes", "No"]

# Every field of the form, described from the page. A custom question's id is
# `question:<Teamtailor question id>`; the others are the input's name.
_READ = """(form, QUESTION) => {
  const label = el => {
    if (!el) return "";
    const copy = el.cloneNode(true);
    copy.querySelectorAll("sup, .sr-only").forEach(e => e.remove());
    return copy.textContent.replace(/\\s+/g, " ").trim();
  };
  const required = el => !!el?.querySelector("sup[data-asterisk]");
  const PICKS = "input[type=radio], input[type=checkbox]";
  const options = (box, inputs) => {
    const item = value => box.querySelector(`[role^=menuitem][data-value="${value}"]`);
    return [...inputs]
      .map(e => label(e.labels?.[0]) || item(e.value)?.dataset.searchText || e.value);
  };
  const fields = [];
  for (const el of form.querySelectorAll("input, textarea")) {
    if (el.closest(QUESTION) || el.disabled || fields.some(f => f.id === el.name)) continue;
    const kind = el.tagName === "TEXTAREA" ? "textarea"
      : el.name === "candidate[location][query]" ? "location"
      : el.type === "checkbox" && el.name.endsWith("[]") ? "choices" : el.type;
    let id = el.name;
    if (kind === "file") id = `candidate[${el.id.replace(/^candidate_/, "")}]`;
    if (!["text", "email", "tel", "textarea", "checkbox", "choices", "file", "location"]
        .includes(kind) || !id) continue;
    if (/linkedin|referrer|social_image/.test(id)) continue;
    const box = kind === "choices" ? el.closest("fieldset") : null;
    const host = box ? box.querySelector("legend")
      : el.labels?.[0] ?? el.closest("[data-controller]")?.querySelector("label");
    fields.push({id, kind, label: label(host), required: required(host),
                 options: box ? options(box, form.querySelectorAll(`[name="${id}"]`)) : null,
                 maxLength: el.maxLength > 0 ? el.maxLength : null,
                 accept: el.accept ? el.accept.split(",") : null});
  }
  for (const q of form.querySelectorAll(QUESTION)) {
    const qid = q.querySelector("input[name$='[question_id]']")?.value;
    const qualifying = !q.matches(".question");
    const named = q.querySelector("[name^='candidate[answers_attributes]']:not([type=hidden])");
    const kind = /picked_video/.test(named?.name) ? "video"
      : q.querySelector("input[type=file]") && !named ? "file"
      : (named?.name.match(/\\]\\[(\\w+)\\](\\[\\])?$/)?.[1] ?? "unknown");
    const picks = options(q, q.querySelectorAll(PICKS));
    if (named?.type === "range") {
      const step = Number(named.step) || 1;
      for (let v = Number(named.min); v <= Number(named.max); v += step) picks.push(String(v));
    }
    const menu = [...q.querySelectorAll("[role^=menuitem]")].map(e => e.dataset.searchText);
    const parent = q.dataset.questionShowIfUuid
      && form.querySelector(`[data-question-uuid="${q.dataset.questionShowIfUuid}"]`);
    const shownBy = parent && [...parent.querySelectorAll(PICKS)]
      .find(e => e.value === q.dataset.questionShowIfValue);
    const when = shownBy && ` (asked when "${label(parent.querySelector("legend, label"))}" is`
      + ` "${options(parent, [shownBy])[0]}")`;
    fields.push({id: `question:${qid}`, kind, question: true, qualifying, conditional: !!parent,
                 label: label(q.querySelector("legend, label")) + (when || ""),
                 required: qualifying || (!parent && q.dataset.questionMandatory === "true"),
                 asked: q.dataset.questionMandatory === "true",
                 options: picks.length ? picks : menu.length ? menu : null});
  }
  return fields;
}"""

# What the form will send: each field's value, a choice as its option's text, a file as the
# uploaded file's name, an address once a suggestion is picked.
_READ_BACK = """(form, QUESTION) => {
  const data = new FormData(form);
  const text = e => {
    const label = e.labels?.[0];
    if (label) return label.textContent.replace(/\\s+/g, " ").trim();
    const box = e.closest(`${QUESTION}, fieldset`);
    const item = box?.querySelector(`[role^=menuitem][data-value="${e.value}"]`);
    return item?.dataset.searchText ?? e.value;
  };
  const file = url => url ? decodeURIComponent(new URL(url).pathname.split("/").pop()) : null;
  const out = {};
  for (const el of form.querySelectorAll("input[type=checkbox][name^='candidate['][value='1']")) {
    out[el.name] = data.getAll(el.name).includes("1") ? "Yes" : "No";
  }
  for (const el of form.querySelectorAll("input[type=checkbox][name^='candidate['][name$='[]']")) {
    out[el.name] = [...form.querySelectorAll(`[name="${el.name}"]:checked`)].map(text);
  }
  for (const name of ["first_name", "last_name", "email", "phone"]) {
    out[`candidate[${name}]`] = data.get(`candidate[${name}]`);
  }
  for (const [name, value] of data) {
    if (/^candidate\\[\\w+_remote_url\\]$/.test(name)) out[name] = file(value);
    else if (/^candidate\\[uploads_attributes\\]\\[\\d+\\]\\[file_remote_url\\]$/.test(name)) {
      out["candidate[file_remote_url]"] = file(value);  // "Additional files", one per upload
    } else if (/cover_letter/.test(name)) out[name] = value;
  }
  if (data.get("candidate[location][place_id]")) {
    out["candidate[location][query]"] = data.get("candidate[location][query]");
  }
  for (const q of form.querySelectorAll(QUESTION)) {
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
    known_gaps = {"video_upload": Gap.RENDERING, "file_question": Gap.RENDERING}
    side_effects = [
        SideEffect("upload", "hiring_system", "a picked file is stored by Teamtailor at once"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._fields: dict[str, Field] = {}
        self._qualifying: set[str] = set()
        self._follow_ups: dict[str, bool] = {}  # shown by an earlier answer: required once shown
        self._uploads: dict[str, str] = {}  # file field id: the answer's file name

    def detect(self, url: str, page: Page | None) -> bool:
        """A `*.teamtailor.com` job, or a job page with Teamtailor's "powered by" link or its
        career site script."""
        if not JOB.search(urlsplit(url).path):
            return False
        if (urlsplit(url).hostname or "").endswith(".teamtailor.com"):
            return True
        return page is not None and page.locator(CAREER_SITE).count() > 0

    def posting(self, url: str) -> str:
        parts = urlsplit(url)
        match = JOB.search(parts.path)
        assert match, url
        return f"https://{parts.hostname}/jobs/{match[1]}"

    def read_form(self, page: Page) -> list[Field]:
        """Opens the form's own page, never the overlay nor "Apply with LinkedIn"."""
        parts = urlsplit(page.url)
        job = JOB.search(parts.path)
        assert job, page.url  # detected on a job URL
        page.goto(f"{parts.scheme}://{parts.netloc}{parts.path[: job.end()]}/applications/new")
        if urlsplit(page.url).hostname != parts.hostname:
            raise Unsupported(f"external_step: the job applies on another site ({page.url})")
        form = page.locator(FORM)
        try:
            form.wait_for()
        except browser.Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        fields, self._qualifying, self._follow_ups = [], set(), {}
        for raw in form.evaluate(_READ, QUESTION):
            if raw.get("qualifying"):
                self._qualifying.add(raw["id"])
            if raw.get("conditional"):
                self._follow_ups[raw["id"]] = raw["asked"]
            fields.append(self._field(raw))
        self._fields = {f.id: f for f in fields}
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        form = page.locator(FORM)
        _decline_cookies(page)
        for field_id in [i for i in answers if i in self._qualifying]:
            self._fill(form, field_id, answers[field_id])
        if form.locator(LOCKED).count():
            raise Unsupported("not_qualified: the qualifying answers leave the form locked")
        for field_id in [i for i in answers if i not in self._qualifying]:
            if field_id in self._follow_ups and not self._shown(form, field_id):
                continue  # not asked: the read-back leaves it out
            self._fill(form, field_id, answers[field_id])
        for field_id, required in self._follow_ups.items():
            if required and field_id not in answers and self._shown(form, field_id):
                label = self._fields[field_id].label
                raise Unsupported(f"missing_answer: an earlier answer asks {label!r}")
        self._uploads = {
            i: PurePath(a).name
            for i, a in answers.items()
            if self._fields[i].type is FieldType.FILE and isinstance(a, str)
        }
        self._wait_for_uploads(form)

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = page.locator(FORM).evaluate(_READ_BACK, QUESTION)
        for field_id, name in self._uploads.items():
            if values.get(field_id) == _stored_name(name):
                values[field_id] = name
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        _click(page, page.locator(f"{FORM} [type=submit]"))
        page.wait_for_url(re.compile(r"/applications/[^/]+/thanks"), timeout=SUBMIT_TIMEOUT_MS)
        return page.url

    def _fill(self, form: Locator, field_id: str, answer: Answer) -> None:
        page, field, host = form.page, self._fields[field_id], self._host(form, field_id)
        match field.type, answer:
            case FieldType.FILE, str():
                host.locator("input[type=file]").set_input_files(answer)
            case FieldType.CHOICE, str() if host.locator("input[type=checkbox]").count():
                box = host.locator("input[type=checkbox]")
                if box.is_checked() != (answer == "Yes"):
                    _click(page, page.locator(f"label[for='{box.get_attribute('id')}']"))
            case FieldType.CHOICE, str() if host.locator("input[type=range]").count():
                host.locator("input[type=range]").fill(answer)
            case FieldType.TYPEAHEAD, str():
                _suggest(page, field, host, answer)
            case FieldType.CHOICE | FieldType.MULTI_CHOICE, _:
                for value in [answer] if isinstance(answer, str) else answer:
                    _pick(page, host, value)
                trigger = host.locator("button[aria-haspopup][aria-expanded=true]")
                if trigger.count():  # a multiple choice menu stays open over the next fields
                    _click(page, trigger.first)
            case FieldType.TEXT, str() if host.locator("input[type=number]").count():
                if re.fullmatch(r"-?\d+(\.\d+)?", answer):  # else left empty: a mismatch
                    host.locator("input[type=number]").first.fill(answer)
            case FieldType.TEXT | FieldType.DATE | FieldType.PHONE, str():
                box = host.locator("input:not([type=hidden]):not([type=search]), textarea")
                box.first.fill(answer)
            case kind, _:
                raise Unsupported(
                    f"unhandled_rendering: cannot fill {field_id}, a {kind}, with {answer!r}"
                )

    def _field(self, raw: dict[str, Any]) -> Field:
        field_id, label, required = raw["id"], raw["label"], raw["required"]
        match raw["kind"]:
            case "video" if raw.get("question"):
                gap = known_gap(
                    self, "video_upload", f"{field_id} is a video, recorded or uploaded"
                )
                return Field(field_id, label, FieldType.FILE, required, unfillable=gap)
            case "file" if raw.get("question"):
                gap = known_gap(self, "file_question", f"{field_id} is a question's file drop zone")
                return Field(field_id, label, FieldType.FILE, required, unfillable=gap)
        kind = (QUESTION_TYPES if raw.get("question") else STANDARD_TYPES).get(raw["kind"])
        if kind is None:
            reason = f"unhandled_rendering: {field_id} is a Teamtailor {raw['kind']} question"
            return Field(field_id, label, FieldType.TEXT, required, unfillable=reason)
        options = CHECKBOX if raw["kind"] == "checkbox" else raw.get("options")
        return Field(
            field_id, label, kind, required, options, raw.get("maxLength"), raw.get("accept")
        )

    def _shown(self, form: Locator, field_id: str) -> bool:
        """Whether a follow-up question shows, once the answer before it has opened it."""
        try:
            self._host(form, field_id).wait_for(timeout=FOLLOW_UP_TIMEOUT_MS)
        except browser.Timeout:
            return False
        return True

    def _host(self, form: Locator, field_id: str) -> Locator:
        """The element holding a field's inputs: its question, else its input's container."""
        if field_id.startswith("question:"):
            qid = field_id.partition(":")[2]
            return form.locator(QUESTION).filter(
                has=form.page.locator(f"input[name$='[question_id]'][value='{qid}']")
            )
        if field_id.endswith("[]"):
            return form.locator("fieldset").filter(has=form.page.locator(f"[name='{field_id}']"))
        if field_id == LOCATION:
            return form.locator("[data-controller~='forms--inputs--location']")
        if field_id.endswith("_remote_url]"):
            dom_id = "candidate_" + field_id[len("candidate[") : -1]
            return form.locator("[data-controller~='forms--inputs--upload']").filter(
                has=form.page.locator(f"#{dom_id}")
            )
        return form.locator(f"[name='{field_id}']").first.locator("xpath=..")

    def _wait_for_uploads(self, form: Locator) -> None:
        deadline = time.monotonic() + UPLOAD_TIMEOUT_S
        while self._uploads and time.monotonic() < deadline:
            held = form.evaluate(_READ_BACK, QUESTION)
            if all(held.get(i) == _stored_name(n) for i, n in self._uploads.items()):
                return
            form.page.wait_for_timeout(UPLOAD_POLL_MS)


def _stored_name(name: str) -> str:
    """The name Teamtailor stores a file under: ASCII, lowercase, each run of other
    characters than letters, digits, `.` and `-` one `_`."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9.\-]+", "_", ascii_name.lower())


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


def _suggest(page: Page, field: Field, host: Locator, answer: str) -> None:
    """Types the address key by key, which loads the suggestions, and picks the first that
    matches it: the form keeps an address only once a suggestion is picked."""
    box = host.locator("input[role=combobox]")
    box.fill("")
    box.press_sequentially(answer, delay=KEY_DELAY_MS)
    options = host.locator("[role=option]")
    options.first.wait_for()
    for i in range(options.count()):
        if matching.matches(field, answer, options.nth(i).get_attribute("data-description") or ""):
            _click(page, options.nth(i))
            return
    raise NotTaken(f"no suggestion for {answer!r} in {field.id}")


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
