"""Recruiterflow (`recruiterflow.com/<employer>/jobs/<id>`), as docs/adapters/recruiterflow.md
observes.

The posting page declares its form in one JSON object (`window.applyJobData`), which a React app
draws: personal information, a candidate profile and the employer's questions, in that order.
`request_read` fetches the page over HTTP and `browser_read` takes it from the browser; one parser
lists the fields from the JSON. The questions are drawn one per child of the questions container,
in the JSON's order, which is how a question's control is found. Files are uploaded at once, the
résumé parsed into the empty fields, so it goes first.
"""

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date
from urllib.parse import urlsplit

from atsfill.core import html
from atsfill.core.adapter import Gap, Unsupported
from atsfill.core.browser import HTTP_TIMEOUT_S, Locator, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

ORIGIN = "https://recruiterflow.com"
HOST = re.compile(r"^(?:www\.)?recruiterflow\.com$")
JOB = re.compile(r"^/([\w-]+)/jobs/(\d+)/?$")
DATA = re.compile(r"var convertedToJSON = (\{.*?\});\s*\n", re.S)
FORM = ".apply-to-job-form-inputs-container form"
QUESTIONS = ".additional-info-inputs-container"
OPTION = "[id^=react-select][id*=-option-]"
FORM_TIMEOUT_MS = 30_000
UPLOAD_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
YES_NO = ["Yes", "No"]
OPT_IN = "mandatory-opt-in-checkbox"
DOCUMENTS = [".doc", ".docx", ".txt", ".rtf", ".pdf"]  # the résumé's drop zone's
MONTH = "%b %Y"  # how a profile entry's date picker shows a month: `Jan 2020`
DAY = "%d %b %Y"  # how a question's date picker shows a day: `31 Jan 2020`
MM_YYYY = re.compile(r"(\d\d)/(\d{4})")
# A question's view field, how it renders, and its field type; the rest are unhandled.
QUESTION_KINDS = {
    1: ("text", FieldType.TEXT),
    2: ("textarea", FieldType.TEXT),
    4: ("number", FieldType.TEXT),
    5: ("money", FieldType.TEXT),
    6: ("date", FieldType.DATE),
    9: ("yes_no", FieldType.CHOICE),
    10: ("select", FieldType.CHOICE),
    11: ("multi", FieldType.MULTI_CHOICE),
    14: ("file", FieldType.FILE),
    19: ("tree", FieldType.MULTI_CHOICE),
}
RANGES = {12: "money range", 13: "number range"}  # a Min and a Max, two fields
NUMBER = " (a number)"  # a label's hint for an input that takes only digits
ENTRIES = {  # a profile list's first entry: (key, label, kind)
    "experience": (
        ("company-name", "Company Name", "text"),
        ("designation", "Title", "text"),
        ("from-experience-year", "Start Date", "month"),
        ("to-experience-year", "End Date", "month"),
    ),
    "education": (
        ("school", "School Name", "text"),
        ("degree", "Degree", "text"),
        ("from-graduation-year", "Start Date", "month"),
        ("to-graduation-year", "End Date", "month"),
    ),
}
ENTRY_REQUIRED = 3  # Recruiterflow checks a required list's first entry's first three

# What each control shows, by the locator `kind` names it with, from inside the form.
_READ_BACK = """(form, controls) => {
  const out = {};
  const questions = form.querySelector(".additional-info-inputs-container")?.children ?? [];
  const at = c => c.question != null ? questions[c.question] : form;
  for (const c of controls) {
    const box = at(c);
    if (!box) continue;
    const el = c.selector ? box.querySelector(c.selector) :
      c.question != null ? box : form.querySelector(`[name="${c.id}"]`);
    if (!el) continue;
    if (c.kind === "file") {
      out[c.id] = el.querySelector(".file-list-item")?.textContent.trim() ?? "";
    } else if (c.kind === "select" || c.kind === "country") {
      out[c.id] = el.querySelector("[class*=singleValue]")?.textContent.trim() ?? "";
    } else if (c.kind === "multi") {
      out[c.id] = [...el.querySelectorAll("[role=button][aria-label^='Remove ']")]
        .map(e => e.getAttribute("aria-label").slice("Remove ".length));
    } else if (c.kind === "tree") {
      out[c.id] = [...el.querySelectorAll(".rf-parent-child-field-accordion")].flatMap(a =>
        [...a.querySelectorAll(".rf-parent-child-field-list")]
          .filter(i => i.querySelector("input").checked)
          .map(i => [a.querySelector(".rf-parent-child-field-header-checkbox-label")
            .textContent.trim(), i.querySelector("p").textContent.trim()]));
    } else if (c.kind === "yes_no") {
      out[c.id] = el.querySelector("button.selected")?.textContent.trim() ?? "";
    } else if (c.kind === "checkbox") {
      out[c.id] = el.checked ? "Yes" : "No";
    } else out[c.id] = el.value ?? "";
  }
  return out;
}"""


@dataclass
class _Control:
    field: Field
    kind: str  # text | textarea | phone | country | file | month | money | min | max | date
    #            | yes_no | select | multi | tree | checkbox
    selector: str | None = None  # inside the form, or inside its question's box
    question: int | None = None  # the question's place among the questions container's children
    items: dict[str, tuple[str, str]] = dc_field(default_factory=dict)  # a tree's, by option


class Recruiterflow:
    name = "recruiterflow"
    lists_every_field = "the page's JSON holds every field whatever the answers"
    known_gaps: dict[str, Gap] = {}
    filters = ["recruiterflow.com##.rf-careers-cookie-banner-container"]  # over the questions
    side_effects = [
        SideEffect("upload", "hiring_system", "a file is uploaded at once, a résumé parsed"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._controls: list[_Control] = []

    def detect(self, url: str, page: Page | None) -> bool:
        return _job(url) is not None

    def posting(self, url: str) -> str:
        job = _job(url)
        return f"{ORIGIN}/{job[0]}/jobs/{job[1]}" if job else url

    def request_read(self, url: str) -> list[Field] | None:
        if _job(url) is None:
            return None
        request = urllib.request.Request(self.posting(url), headers={"User-Agent": "atsfill"})
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
                source = response.read().decode()
        except urllib.error.HTTPError, OSError, ValueError:
            return None
        data = _data(source)
        if data is None or not _open(data):
            return None
        return [c.field for c in _controls(data)]

    def browser_read(self, page: Page) -> list[Field]:
        data = _data(page.content())
        if data is None or not _open(data):
            raise Unsupported("posting_gone: no application form on the page")
        try:
            page.locator(FORM).wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        self._controls = _controls(data)
        drawn = page.locator(f"{FORM} {QUESTIONS} > *").count()
        asked = len(data["application_form"].get("questions") or [])
        if drawn != asked:
            raise RuntimeError(f"{asked} questions declared, {drawn} drawn")
        return [c.field for c in self._controls]

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        shown = [
            c for c in self._controls if c.field.id in answers and not c.field.unfillable
        ]  # fmt: skip
        # a section may start collapsed: its header opens it
        for section in page.locator(f"{FORM} .input-section-container").all():
            inputs = section.locator("xpath=following-sibling::*[1]")
            if inputs.count() and not inputs.is_visible():
                section.click()
                inputs.wait_for(timeout=FORM_TIMEOUT_MS)
        # files first: the résumé's parse fills the empty fields
        for c in sorted(shown, key=lambda c: c.kind != "file"):
            _set(page, c, answers[c.field.id])

    def read_back(self, page: Page) -> dict[str, Answer]:
        controls = [
            {"id": c.field.id, "kind": c.kind, "selector": c.selector, "question": c.question}
            for c in self._controls
            if not c.field.unfillable
        ]
        values = page.locator(FORM).evaluate(_READ_BACK, controls)
        out: dict[str, Answer] = {}
        for c in self._controls:
            value = values.get(c.field.id)
            if isinstance(value, str):
                value = _shown(c.kind, value)
            elif c.kind == "tree" and value is not None:
                text = {item: option for option, item in c.items.items()}
                value = [text.get((group, name), name) for group, name in value]
            if value not in (None, "", []):
                out[c.field.id] = value
        return out

    def submit(self, page: Page) -> str | None:
        """Clicks Submit Application; the page posts the form to `submit-application`, whose
        answer, unless it says `FAILURE`, is the evidence. The page's own check of the required
        fields posts nothing and shows its message instead."""
        path = f"{urlsplit(self.posting(page.url)).path}/submit-application"
        try:
            with page.expect_response(
                lambda r: r.request.method == "POST" and urlsplit(r.url).path == path,
                timeout=SUBMIT_TIMEOUT_MS,
            ) as posted:
                page.locator("#submit-application-button").click()
        except Timeout:
            raise RuntimeError(f"nothing posted: {_message(page)}") from None
        response = posted.value
        try:
            answer = response.json()
        except ValueError:
            answer = {}
        if not response.ok or answer.get("RESULT") == "FAILURE":
            detail = answer.get("message") or response.status_text
            raise RuntimeError(f"submit-application answered {response.status}: {detail}"[:300])
        return f"{response.url} {response.status}"


def _job(url: str) -> tuple[str, str] | None:
    """The employer's path and the job id, from `recruiterflow.com/<employer>/jobs/<id>`."""
    parts = urlsplit(url)
    match = JOB.match(parts.path)
    return (match[1], match[2]) if HOST.match(parts.hostname or "") and match else None


def _data(source: str) -> dict | None:
    match = DATA.search(source)
    return json.loads(match[1]) if match else None


def _open(data: dict) -> bool:
    return data.get("job_visibility_id") in data.get("ACTIVE_JOB_VISIBILITY", []) and bool(
        data.get("application_form")
    )


def _controls(data: dict) -> list[_Control]:
    """The form's fields from the page's JSON, in the page's order: a section's item whose
    requirement is `off` is not drawn."""
    form = data["application_form"]
    out: list[_Control] = []
    for item in form.get("personal_info") or []:
        if item["requirement_type"] != "off":
            out += _personal(item)
    for item in form.get("candidate_profile") or []:
        if item["requirement_type"] != "off":
            out += _profile(item)
    for n, question in enumerate(form.get("questions") or []):
        out += _question(n, question)
    if notes := _text(data.get("footer_notes") or ""):
        box = "input.policy-checkbox:not(.is-current-custom-checkbox-container *)"
        out.append(_Control(Field(OPT_IN, notes, FieldType.CHOICE, True, YES_NO), "checkbox", box))
    return out


def _personal(item: dict) -> list[_Control]:
    key, label, required = item["key"], item["display_name"], _required(item)
    match key:
        case "name":
            return [
                _Control(Field(f"personal_info.{n}", t, FieldType.TEXT, required), "text")
                for n, t in (("first_name", "First name"), ("last_name", "Last name"))
            ]
        case "address":
            parts = [
                ("city", "City", FieldType.TEXT, "text"),
                ("state", "State", FieldType.TEXT, "text"),
                ("country", "Country", FieldType.TYPEAHEAD, "country"),
                ("postal_code", "Zip code", FieldType.TEXT, "text"),
            ]
            return [
                _Control(
                    Field(f"personal_info.location.{n}", f"{label}: {t}", type_, required),
                    kind,
                    ".location-input-wrapper" if kind == "country" else None,
                )
                for n, t, type_, kind in parts
            ]
        case "phone":
            return [
                _Control(Field("personal_info.phone", label, FieldType.PHONE, required), "phone")
            ]
        case "email" | "linkedin_profile":
            return [
                _Control(Field(f"personal_info.{key}", label, FieldType.TEXT, required), "text")
            ]
    reason = f"unhandled_rendering: personal_info.{key} is a {item['response_type']}"
    field = Field(f"personal_info.{key}", label, FieldType.TEXT, required, unfillable=reason)
    return [_Control(field, "text")]


def _profile(item: dict) -> list[_Control]:
    key, label, required = item["key"], item["display_name"], _required(item)
    match key:
        case "resume":
            field = Field(
                "candidate_profile.resume", label, FieldType.FILE, required, accept=DOCUMENTS
            )
            return [_Control(field, "file", ":scope > .form-dnd-container")]
        case "cover_letter":
            field = Field("candidate_profile.cover_letter", label, FieldType.FILE, required)
            box = ".candidate-profile-inputs-container .form-dnd-container"
            return [_Control(field, "file", box)]
        case "summary":
            field = Field("candidate_profile.summary", label, FieldType.TEXT, required)
            box = ".candidate-profile-inputs-container textarea"
            return [_Control(field, "textarea", box)]
        case "experience" | "education":
            box = f".{key}-input-wrapper"
            out = []
            for n, (part, text, kind) in enumerate(ENTRIES[key]):
                month = kind == "month"
                field = Field(
                    f"candidate_profile.{part}.0",
                    f"{label}: {text} (MM/YYYY)" if month else f"{label}: {text}",
                    FieldType.TEXT,
                    required and n < ENTRY_REQUIRED,
                )
                picker = ".start-date-picker" if part.startswith("from") else ".end-date-picker"
                out.append(_Control(field, kind, f"{box} {picker}" if month else None))
            return out
    reason = f"unhandled_rendering: candidate_profile.{key} is a {item['response_type']}"
    field = Field(f"candidate_profile.{key}", label, FieldType.TEXT, required, unfillable=reason)
    return [_Control(field, "text")]


def _question(n: int, question: dict) -> list[_Control]:
    view = question.get("view_field_id") or question.get("custom_field_view_field_id")
    id_, label = f"question.{question['question_id']}", _text(question["question_text"])
    required = _required(question)
    if view in RANGES:
        return [
            _Control(
                Field(f"{id_}.{end}", f"{label} ({end.title()}){NUMBER}", FieldType.TEXT, required),
                end,
                f"input[placeholder={end.title()}]",
                n,
            )
            for end in ("min", "max")
        ]
    if view not in QUESTION_KINDS:
        reason = f"unhandled_rendering: {id_} is a view field {view}"
        return [_Control(Field(id_, label, FieldType.TEXT, required, unfillable=reason), "text")]
    kind, type_ = QUESTION_KINDS[view]
    options = None
    if kind == "yes_no":
        options = YES_NO
    elif kind in ("select", "multi"):
        options = list(dict.fromkeys(c for c in question.get("choices") or [] if c.strip()))
    elif kind == "tree":
        options = list(_tree(question.get("choices") or []))
    selector = {
        "text": "input",
        "number": "input",
        "textarea": "textarea",
        "money": "input:not([role=combobox]):not([type=hidden])",
        "date": "input",
    }.get(kind)
    if kind in ("number", "money"):
        label += NUMBER
    items = _tree(question.get("choices") or []) if kind == "tree" else {}
    return [_Control(Field(id_, label, type_, required, options), kind, selector, n, items)]


def _tree(groups: list[dict]) -> dict[str, tuple[str, str]]:
    """A parent-child question's items by option text, its name, or `<group>: <name>` where two
    groups hold the name: the group and the name it is drawn under."""
    items = [(g["name"], c["name"]) for g in groups for c in g.get("children") or []]
    names = [name for _, name in items]
    return {name if names.count(name) == 1 else f"{group}: {name}": (group, name)
            for group, name in items}  # fmt: skip


def _required(item: dict) -> bool:
    return item.get("requirement_type") == "mandatory"


def _text(source: str) -> str:
    """A label's text, the footer's HTML or a question's line breaks collapsed."""
    return " ".join(html.parse(source).text().split())


def _box(page: Page, c: _Control) -> Locator:
    form = page.locator(FORM)
    box = form.locator(f"{QUESTIONS} > *").nth(c.question) if c.question is not None else form
    if c.selector:
        return box.locator(c.selector).first
    return box if c.question is not None else form.locator(f"[name='{c.field.id}']").first


def _set(page: Page, c: _Control, answer: Answer) -> None:
    values = [answer] if isinstance(answer, str) else answer
    box = _box(page, c)
    match c.kind:
        case "file":
            with page.expect_response(lambda r: urlsplit(r.url).path == "/api/file/upload",
                                      timeout=UPLOAD_TIMEOUT_MS):  # fmt: skip
                box.locator("input[type=file]").set_input_files(values[0])
            name = values[0].rsplit("/", 1)[-1]
            box.locator(".file-list-item", has_text=name).wait_for(timeout=UPLOAD_TIMEOUT_MS)
        case "country" | "select" | "multi":
            for value in values:
                _pick(page, box, value, typed=c.kind == "country")
            page.keyboard.press("Escape")
        case "tree":  # an item's group is opened to show it
            for value in values:
                group, name = c.items.get(value, ("", value))
                accordion = box.locator(".rf-parent-child-field-accordion").filter(
                    has=page.locator(".rf-parent-child-field-header-checkbox-label", has_text=group)
                )
                item = accordion.first.locator(".rf-parent-child-field-list").filter(
                    has=page.locator("p", has_text=re.compile(rf"^\s*{re.escape(name)}\s*$"))
                )
                if not item.first.is_visible():
                    accordion.first.locator(".rf-plus-minus-icon-container").click()
                item.first.locator("input[type=checkbox]").click()
        case "yes_no":
            box.locator("button", has_text=re.compile(rf"^\s*{values[0]}\s*$")).click()
        case "checkbox":
            if box.is_checked() != (values[0] == "Yes"):
                box.click()
        case "month" if shown := MM_YYYY.fullmatch(values[0]):
            box.fill(date(int(shown[2]), int(shown[1]), 1).strftime(MONTH))
            box.press("Tab")
        case "date":
            box.fill(date.fromisoformat(values[0]).strftime(DAY))
            box.press("Tab")
        case _:
            box.fill(values[0])
            if c.kind == "phone":
                box.blur()


def _pick(page: Page, box: Locator, value: str, typed: bool) -> None:
    """Opens a react-select and clicks the option `value`; a long list is typed into first."""
    options = page.locator(OPTION)
    if not options.count():
        box.locator("[role=combobox]").click()
    if typed:
        page.keyboard.type(value)
    exact = options.filter(has_text=re.compile(rf"^\s*{re.escape(value)}\s*$"))
    (exact if exact.count() else options.filter(has_text=value)).first.click()


def _shown(kind: str, value: str) -> str:
    """A control's value as the answer gives it: a month `MM/YYYY`, a day `YYYY-MM-DD`, an
    amount without its thousands separators."""
    try:
        match kind:
            case "month" if value:
                parsed = date.strptime(value, MONTH)
                return f"{parsed.month:02}/{parsed.year}"
            case "date" if value:
                return date.strptime(value, DAY).isoformat()
    except ValueError:
        return value
    if kind in ("number", "money", "min", "max"):
        return value.replace(",", "")
    return value


def _message(page: Page) -> str:
    shown = page.locator(".rf-status-message, [role=alert]:visible, .toast:visible")
    return " ".join(" ".join(shown.all_inner_texts()).split())[:200] or "no message shown"
