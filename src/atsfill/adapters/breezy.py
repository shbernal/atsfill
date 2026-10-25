"""Breezy HR (`<employer>.breezy.hr/p/<position>`, or an employer's own domain), as
docs/adapters/breezy.md observes.

Two page engines, an AngularJS one and a React one, render the same markup: named controls
under `h3` headings, the questionnaire's as `section_<section>_question_<n>`. Only a browser
read lists the fields. Files upload to Breezy when attached; the rest reaches it on submit.
"""

import re
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser
from atsfill.core.adapter import Gap, Unsupported, out_of_scope
from atsfill.core.browser import Locator, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOST = re.compile(r"^[a-z0-9-]+\.breezy\.hr$")
JOB = re.compile(r"^/p/([0-9a-f]{12,})(?:-[^/]*)?(?:/apply)?/?$")
PORTAL = "script[src*='assets-cdn.breezy.hr/breezy-portal']"
APPLIED = re.compile(r"/api/apply/[0-9a-f]+$")
REFERENCE = re.compile(r"^(section_\w+)\.reference(\d+)\.(\w+)$")
FORM = "form[name=form]"
NEXT = "button[ng-click='nextSection()']"
SUBMIT = "button[ng-click='apply()'], button[type=submit]"
FORM_TIMEOUT_MS = 30_000
UPLOAD_TIMEOUT_MS = 30_000
STEP_TIMEOUT_MS = 5_000
SUBMIT_TIMEOUT_MS = 90_000
MAX_STEPS = 20
YES_NO = ["Yes", "No"]
KINDS = {
    "text": FieldType.TEXT,
    "phone": FieldType.PHONE,
    "date": FieldType.DATE,
    "select": FieldType.CHOICE,
    "radio": FieldType.CHOICE,
    "consent": FieldType.CHOICE,
    "checkboxes": FieldType.MULTI_CHOICE,
    "file": FieldType.FILE,
}
# Labels of the controls without a heading of their own.
LABELS = {
    "cResume": "Resume",
    "salaryCurrency": "Desired Salary: currency",
    "salaryPeriod": "Desired Salary: period",
}
REFERENCE_PARTS = {
    "name": "Full Name",
    "email_address": "Email Address",
    "phone_number": "Phone Number",
    "type": "Relationship",
}


@dataclass(frozen=True)
class Entry:
    """A list the page adds entries to on a click; atsfill fills its first entry."""

    model: str  # its controls' `ng-model` object
    add: str  # what its "Add" link calls
    heading: str
    checked: tuple[str, ...]  # what Breezy checks of the first entry when the list is required
    fields: tuple[tuple[str, str, str], ...]  # (key, label, kind)


_DATES = (("date_start", "Start date", "date"), ("date_end", "End date", "date"))
ENTRIES = {
    "work_history": Entry(
        "candidatePosition",
        "addPosition",
        "Work History",
        ("company_name", "title"),
        (
            ("company_name", "Company", "text"),
            ("title", "Title", "text"),
            *_DATES,
            ("summary", "Summary", "text"),
        ),
    ),  # fmt: skip
    "education": Entry(
        "candidateSchool",
        "addEducation",
        "Education",
        ("school_name", "field_of_study"),
        (
            ("school_name", "School", "text"),
            ("field_of_study", "Field of Study", "text"),
            *_DATES,
            ("summary", "Summary", "text"),
        ),
    ),  # fmt: skip
}

# The fields in page order, one per control name, with its heading's text and how it renders;
# a live video question is kept as the kind `video`, its id the question's as Breezy names it
# for its widget (`ziggeo-id`, `<section>:<n>`).
_READ = """form => {
  const text = el => {
    if (!el) return "";
    const copy = el.cloneNode(true);
    copy.querySelectorAll(".required, .question-body, select, ul, .error-container")
      .forEach(e => e.remove());
    return copy.textContent.replace(/\\s+/g, " ").trim();
  };
  const starred = h => !!h && [...h.querySelectorAll(".required")]
    .some(s => !s.classList.contains("ng-hide"));
  const heading = el => {
    for (let n = el; n && n !== form; n = n.parentElement)
      for (let s = n.previousElementSibling; s; s = s.previousElementSibling) {
        const h = s.matches("h3") ? s : s.querySelector("h3");
        if (h) return h;
      }
    return null;
  };
  const optionText = el => text(el.closest("li.option, li, label"));
  const found = new Map();
  const add = field => {
    if (!found.has(field.id)) found.set(field.id, field);
    return found.get(field.id);
  };
  // A question whose controls are not one named group: its references, or its video.
  const done = new Set();
  const special = q => {
    if (!q || done.has(q)) return done.has(q);
    const h = q.querySelector("h3");
    if (q.querySelector(".referencecheck")) {
      const qid = q.querySelector("[ng-model='reference.name']")?.name;
      const refs = [...q.querySelectorAll("li.reference")];
      refs.forEach((ref, n) => {
        for (const el of ref.querySelectorAll("[ng-model^='reference.']")) {
          const part = el.getAttribute("ng-model").slice("reference.".length);
          const kind = el.type === "radio" ? "radio" : el.type === "tel" ? "phone" : "text";
          const f = add({id: `${qid}.reference${n + 1}.${part}`, kind, options: [],
                         question: text(h), part, n: n + 1, refs: refs.length,
                         required: el.required || starred(h)});
          if (el.type === "radio") f.options.push(optionText(el));
        }
      });
    } else if (!q.querySelector("[name^='section_']") &&
               q.querySelector("input[type=file], ziggeo, [ng-if*='video']")) {
      const [s, n] = (q.querySelector("ziggeo")?.getAttribute("ziggeo-id") ?? "").split(":");
      const id = n === undefined ? `video_${text(h)}` : `section_${s}_question_${n}`;
      add({id, label: text(h), kind: "video", required: starred(h)});
    } else return false;
    done.add(q);
    return true;
  };
  for (const el of form.querySelectorAll("input, select, textarea")) {
    if (["hidden", "submit", "button"].includes(el.type)) continue;
    if (el.closest(".apply-field-extra") || special(el.closest("li.question"))) continue;
    const model = el.getAttribute("ng-model") || "";
    const id = el.name || (model === "candidate.salary.period" ? "salaryPeriod" : "");
    if (!id) continue;
    const h = heading(el);
    let kind;
    if (el.type === "file") kind = "file";
    else if (el.tagName === "SELECT") kind = "select";
    else if (el.type === "radio") kind = "radio";
    else if (el.type === "checkbox") kind = id.startsWith("section_") ? "checkboxes" : "consent";
    else if (el.type === "date") kind = "date";
    else if (id === "cPhoneNumber" || el.type === "tel") kind = "phone";
    else kind = "text";
    const accept = el.accept ? el.accept.split(",").map(a => a.trim()).filter(Boolean) : null;
    const field = add({id, kind, options: [], required: false, accept,
                       label: kind === "consent" ? optionText(el) : text(h),
                       maxLength: el.maxLength > 0 ? el.maxLength : null});
    field.required ||= el.required || (kind !== "consent" && starred(h));
    if (kind === "select")
      field.options = [...el.options].map(o => o.textContent.trim()).filter(Boolean);
    if (kind === "radio" || kind === "checkboxes") field.options.push(optionText(el));
  }
  return [...found.values()];
}"""

# What one field's controls show: a choice its option's text, a box Yes/No, a file the name
# Breezy shows once it has the upload.
_READ_BACK = """(controls, kind) => {
  const el = controls[0];
  const optionText = e => (e.closest("li.option, li, label")?.textContent || "")
    .replace(/\\s+/g, " ").trim();
  if (kind === "file")
    return el.closest(".file-input-container")?.querySelector("a.bzyLinkColor:not(.placeholder)")
      ?.textContent.trim() || null;
  if (kind === "select") {
    const option = el.selectedOptions[0];
    return option && option.value !== "?" && option.textContent.trim() || null;
  }
  if (kind === "radio") {
    const checked = controls.find(e => e.checked);
    return checked ? optionText(checked) : null;
  }
  if (kind === "checkboxes") return controls.filter(e => e.checked).map(optionText);
  if (kind === "consent") return el.checked ? "Yes" : "No";
  return el.value;
}"""

# The step on show: the first section the page displays.
_STEP = """form => [...form.querySelectorAll("[data-section]")]
  .find(s => s.getClientRects().length > 0)?.dataset.section ?? null"""

_OPTION_TEXT = """e => (e.closest("li.option, li, label")?.textContent || "")
  .replace(/\\s+/g, " ").trim()"""

# Whether a file input's step is on show: the input itself is always hidden.
_FILE_SHOWN = """e => {
  const step = e.closest("[data-section]");
  return !step || step.getClientRects().length > 0;
}"""


class Breezy:
    name = "breezy"
    known_gaps: dict[str, Gap] = {}
    side_effects = [
        SideEffect("upload", "hiring_system", "an attached file is uploaded to Breezy at once"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._kinds: dict[str, str] = {}  # how the page renders each field

    def detect(self, url: str, page: Page | None) -> bool:
        """A `*.breezy.hr` position, or one on an employer's domain that serves Breezy's portal."""
        parts = urlsplit(url)
        if not JOB.match(parts.path):
            return False
        if HOST.match(parts.hostname or ""):
            return True
        return page is not None and page.locator(PORTAL).count() > 0

    def posting(self, url: str) -> str:
        parts = urlsplit(url)
        match = JOB.match(parts.path)
        return f"https://{parts.hostname}/p/{match[1]}" if match else url

    def browser_read(self, page: Page) -> list[Field]:
        if not urlsplit(page.url).path.rstrip("/").endswith("/apply"):
            page.goto(f"{self.posting(page.url)}/apply")
        form = page.locator(FORM)
        try:
            form.locator("[name=cName]").wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        fields, self._kinds = [], {}
        for raw in form.evaluate(_READ):
            field = _field(raw)
            fields.append(field)
            if not field.unfillable:
                self._kinds[field.id] = raw["kind"]
        return fields + self._entries(form)

    def _entries(self, form: Locator) -> list[Field]:
        """The first work history and education entry, when the page offers to add them."""
        out = []
        for prefix, entry in ENTRIES.items():
            if not form.locator(f"a[ng-click*='{entry.add}()']").count():
                continue
            flag = form.locator(f"#{prefix.split('_')[0]}_required")
            required = flag.count() > 0 and flag.first.get_attribute("value") == "required"
            for key, label, kind in entry.fields:
                self._kinds[f"{prefix}.{key}"] = kind
                out.append(
                    Field(
                        f"{prefix}.{key}",
                        f"{entry.heading}: {label}",
                        KINDS[kind],
                        required and key in entry.checked,
                    )
                )
        return out

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        """Fills the step on show, then goes on with Continue while answers are left."""
        form = page.locator(FORM)
        pending = {k: v for k, v in answers.items() if k in self._kinds}
        for _ in range(MAX_STEPS):
            for field_id in list(pending):
                if _shown(form, field_id):
                    self._fill(form, field_id, pending.pop(field_id))
            step = form.locator(NEXT)
            if not pending or not step.is_visible():
                return
            shown = form.evaluate(_STEP)
            browser.click(step)
            with suppress(Timeout):
                page.wait_for_function(
                    f"was => ({_STEP})(document.querySelector({FORM!r})) !== was",
                    arg=shown,
                    timeout=STEP_TIMEOUT_MS,
                )
            if form.evaluate(_STEP) == shown:
                return  # Breezy kept the step: an answer it checks is missing

    def _fill(self, form: Locator, field_id: str, answer: Answer) -> None:
        values = [answer] if isinstance(answer, str) else answer
        entry = ENTRIES.get(field_id.split(".")[0])
        if entry and not _controls(form, field_id).count():
            browser.click(form.locator(f"a[ng-click*='{entry.add}()']"))
            _controls(form, field_id).first.wait_for(timeout=STEP_TIMEOUT_MS)
        controls = _controls(form, field_id)
        match self._kinds[field_id]:
            case "file":
                controls.first.set_input_files(values[0])
                with suppress(Timeout):  # the name shows once Breezy has the upload
                    controls.first.locator(
                        "xpath=ancestor::*[contains(@class,'file-input-container')][1]"
                    ).locator("a.bzyLinkColor:not(.placeholder)").first.wait_for(
                        timeout=UPLOAD_TIMEOUT_MS
                    )
            case "select":
                controls.first.select_option(label=values[0])
            case "radio" | "checkboxes":
                for value in values:
                    if (option := _option(controls, value)) is not None:
                        option.check()
            case "consent":
                controls.first.set_checked(values[0] == "Yes")
            case _:
                controls.first.fill(values[0])

    def read_back(self, page: Page) -> dict[str, Answer]:
        form = page.locator(FORM)
        out: dict[str, Answer] = {}
        for field_id, kind in self._kinds.items():
            controls = _controls(form, field_id)
            if not controls.count():
                continue
            value = controls.evaluate_all(_READ_BACK, kind)
            if value not in (None, "", []):
                out[field_id] = value
        return out

    def submit(self, page: Page) -> str | None:
        """Clicks Submit Application; the evidence is Breezy's answer to the application."""
        with page.expect_response(
            lambda r: r.request.method == "POST" and APPLIED.search(r.url) is not None,
            timeout=SUBMIT_TIMEOUT_MS,
        ) as applied:
            browser.click(page.locator(FORM).locator(SUBMIT).locator("visible=true").first)
        response = applied.value
        if not response.ok:
            raise RuntimeError(f"apply answered {response.status}: {response.text()[:200]}")
        return f"{response.url} {response.status}"


def _field(raw: dict[str, Any]) -> Field:
    kind = raw["kind"]
    if kind == "video":
        reason = out_of_scope(raw["id"], "a video recorded live")
        return Field(raw["id"], raw["label"], FieldType.FILE, raw["required"], unfillable=reason)
    label = LABELS.get(raw["id"]) or raw.get("label", "")
    if "part" in raw:
        label = (
            f"{raw['question']} (reference {raw['n']} of {raw['refs']}): "
            f"{REFERENCE_PARTS.get(raw['part'], raw['part'])}"
        )
    options = {"consent": YES_NO}.get(kind)
    if kind in ("select", "radio", "checkboxes"):
        options = raw["options"]
    max_length = raw.get("maxLength") if KINDS[kind] is FieldType.TEXT else None
    accept = raw.get("accept") if kind == "file" else None
    return Field(raw["id"], label, KINDS[kind], raw["required"], options, max_length, accept)


def _controls(form: Locator, field_id: str) -> Locator:
    """Every control of a field."""
    prefix, _, key = field_id.partition(".")
    if entry := ENTRIES.get(prefix):
        return form.locator(f"[ng-model='{entry.model}.{key}']")
    if field_id == "salaryPeriod":
        return form.locator("select[ng-model='candidate.salary.period']")
    if reference := REFERENCE.match(field_id):
        question, n, part = reference.groups()
        return (
            form.locator(f"li.question:has([name='{question}'])")
            .locator("li.reference")
            .nth(int(n) - 1)
            .locator(f"[ng-model='reference.{part}']")
        )
    return form.locator(f"[name='{field_id}']")


def _shown(form: Locator, field_id: str) -> bool:
    """Whether the field's step is on show; an entry's by the link that adds it."""
    if entry := ENTRIES.get(field_id.split(".")[0]):
        return form.locator(f"a[ng-click*='{entry.add}()']").is_visible()
    control = _controls(form, field_id).first
    if not control.count():
        return False
    if control.get_attribute("type") == "file":
        return control.evaluate(_FILE_SHOWN)
    return control.is_visible()


def _option(controls: Locator, value: str) -> Locator | None:
    """The radio or checkbox whose text is `value`."""
    for control in controls.all():
        if control.evaluate(_OPTION_TEXT) == value:
            return control
    return None
