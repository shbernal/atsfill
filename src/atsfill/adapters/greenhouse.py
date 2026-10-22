"""Greenhouse (`job-boards.greenhouse.io`, `boards.greenhouse.io`, their EU hosts, and employer
pages embedding them), as docs/adapters/greenhouse.md observes.

Nothing but an attached file reaches Greenhouse before submit, so the read-back is the page:
input values, the shown value of each select, the phone's dial code, the attached file's name.
"""

import json
import re
import time
import urllib.request
from contextlib import suppress
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from atsfill.core import browser
from atsfill.core.adapter import Gap, Unsupported
from atsfill.core.browser import HTTP_TIMEOUT_S, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOSTS = re.compile(r"^(job-boards|boards)(\.eu)?\.greenhouse\.io$")
FORM = "#application-form"
API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}?questions=true"
EMBED = "https://job-boards.greenhouse.io/embed/job_app"
EMBED_TIMEOUT_MS = 15_000
OPTIONS_TIMEOUT_MS = 3_000
HYDRATE_TIMEOUT_MS = 15_000
FORM_TIMEOUT_MS = 20_000
ANSWER_TIMEOUT_MS = 15_000  # an upload's file name, a suggestion: past it, a mismatch
SUBMIT_TIMEOUT_MS = 90_000
HYDRATE_POLL_MS = 500  # for a select's list to open, per click
KEY_DELAY_MS = 20
API_TYPES = {
    "input_text": FieldType.TEXT,
    "textarea": FieldType.TEXT,
    "input_file": FieldType.FILE,
    "multi_value_single_select": FieldType.CHOICE,
    "multi_value_multi_select": FieldType.MULTI_CHOICE,
}
YES_NO = ["Yes", "No"]
# Sections the page adds or names its own way, or shows a question of only on another answer
# (EEOC race after Hispanic/Latino): `read_api` declines a posting with any (ADR 0011).
UNMAPPED = ("education", "compliance", "demographic_questions")

# The fields in page order. A select's options are read by opening it, from Python.
_READ = """form => {
  const text = el => {
    if (!el) return "";
    const copy = el.cloneNode(true);
    const marks = copy.querySelectorAll("[aria-hidden=true], .required, .visually-hidden");
    marks.forEach(e => e.remove());
    return copy.textContent.replace(/\\s+/g, " ").trim();
  };
  const labelFor = el => document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
  const required = el => el.getAttribute("aria-required") === "true" || el.required;
  const found = [];
  for (const el of form.querySelectorAll("input, textarea")) {
    if (!el.id || ["hidden", "search", "file", "checkbox"].includes(el.type)) continue;
    const combo = el.getAttribute("role") === "combobox";
    if (combo && el.closest(".phone-input")) continue;  // the phone's country, set by its number
    const shell = el.closest(".select");
    const multi = combo && !!shell?.querySelector(".select__value-container--is-multi");
    found.push([el, {id: el.id, label: text(labelFor(el)), required: required(el),
      kind: combo ? (multi ? "multi" : "select")
        : ["tel", "number", "radio"].includes(el.type) ? el.type : "text",
      maxLength: el.maxLength > 0 ? el.maxLength : null}]);
  }
  for (const group of form.querySelectorAll("fieldset.checkbox")) {
    const boxes = [...group.querySelectorAll("input[type=checkbox]")];
    const legend = group.querySelector("legend");
    found.push([group, {id: group.id, kind: "checkboxes", label: text(legend),
      required: group.getAttribute("aria-required") === "true",
      options: boxes.map(b => text(labelFor(b)))}]);
  }
  for (const box of form.querySelectorAll("input[type=checkbox]")) {
    if (box.closest("fieldset.checkbox")) continue;
    found.push([box, {id: box.name || box.id, kind: "consent", label: text(labelFor(box)),
      required: required(box)}]);
  }
  for (const group of form.querySelectorAll(".file-upload")) {
    const input = group.querySelector("input[type=file]");
    found.push([group, {id: group.getAttribute("aria-labelledby").replace(/^upload-label-/, ""),
      kind: "file", label: text(group.querySelector(".upload-label")),
      required: group.getAttribute("aria-required") === "true",
      accept: input?.accept ? input.accept.split(",") : null}]);
  }
  const before = (a, b) => a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING;
  found.sort(([a], [b]) => before(a, b) ? -1 : 1);
  return found.map(([, field]) => field);
}"""

# What each field shows: a select its shown value, a file its attached name, a checkbox group
# its checked labels, the phone its country's dial code then its number.
_READ_BACK = """(form, ids) => {
  const text = el => el ? el.textContent.replace(/\\s+/g, " ").trim() : null;
  const out = {};
  for (const id of ids) {
    const el = document.getElementById(id);
    const upload = form.querySelector(`.file-upload[aria-labelledby="upload-label-${id}"]`);
    if (upload) out[id] = text(upload.querySelector(".file-upload__filename p"));
    else if (el?.tagName === "FIELDSET") {
      out[id] = [...el.querySelectorAll("input:checked")]
        .map(b => text(document.querySelector(`label[for="${CSS.escape(b.id)}"]`)));
    } else if (el?.getAttribute("role") === "combobox") {
      const select = el.closest(".select");
      const multi = [...select.querySelectorAll(".select__multi-value__label")].map(text);
      out[id] = multi.length ? multi : text(select.querySelector(".select__single-value"));
    } else if (el?.type === "tel" && el.closest(".phone-input")) {
      const dial = text(el.closest(".phone-input").querySelector(".select__single-value"));
      const number = el.value.startsWith(dial) ? el.value.slice(dial.length).trim() : el.value;
      out[id] = dial && number ? `${dial} ${number}` : null;
    } else {
      const box = form.querySelector(`input[type=checkbox][name="${id}"]`) ?? el;
      out[id] = box?.type === "checkbox" ? (box.checked ? "Yes" : "No") : el?.value ?? null;
    }
  }
  return out;
}"""


class Greenhouse:
    name = "greenhouse"
    known_gaps: dict[str, Gap] = {}
    side_effects = [
        SideEffect("upload", "hiring_system", "an attached file is stored by Greenhouse at once"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._fields: dict[str, Field] = {}
        self._kinds: dict[str, str] = {}  # how the page renders each field
        self._embed: str | None = None  # the job_app URL an employer page embeds

    def detect(self, url: str, page: Page | None) -> bool:
        """A board URL, or an employer page whose `#grnhse_iframe` embeds a job application."""
        if _board_job(url):
            return True
        if page is None:
            return False
        frame = page.locator("iframe#grnhse_iframe").first
        try:
            frame.wait_for(state="attached", timeout=EMBED_TIMEOUT_MS)
        except Timeout:
            return False
        src = frame.get_attribute("src") or ""
        query = parse_qs(urlsplit(src).query)
        if not ("for" in query and "token" in query):
            return False
        keep = {"for": query["for"][0], "token": query["token"][0]}
        if gh_src := parse_qs(urlsplit(url).query).get("gh_src"):
            keep["gh_src"] = gh_src[0]
        self._embed = f"{EMBED}?{urlencode(keep)}"
        return True

    def posting(self, url: str) -> str:
        board, job = _board_job(url) or _board_job(self._embed or "") or ("", url)
        return f"https://job-boards.greenhouse.io/{board}/jobs/{job}"

    def read_api(self, url: str) -> list[Field] | None:
        """The fields from the Job Board API, unless the page holds some the API leaves out."""
        found = _board_job(url)
        if found is None:
            return None
        try:
            request = urllib.request.Request(
                API.format(board=found[0], job=found[1]), headers={"User-Agent": "atsfill"}
            )
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
                return _api_fields(json.load(response))
        except OSError, ValueError, KeyError, TypeError:
            return None

    def read_form(self, page: Page) -> list[Field]:
        if self._embed:
            page.goto(self._embed)
        form = page.locator(FORM)
        try:
            form.wait_for(timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        _wait_until_live(page)
        raws = form.evaluate(_READ)
        fields = [self._field(page, raw) for raw in raws]
        self._fields = {f.id: f for f in fields}
        self._kinds = {raw["id"]: raw["kind"] for raw in raws}
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        for field_id, answer in answers.items():
            values = [answer] if isinstance(answer, str) else answer
            match self._kinds[field_id]:
                case "file":
                    upload = page.locator(_upload(field_id))
                    upload.locator("input[type=file]").set_input_files(values[0])
                    with suppress(Timeout):
                        upload.locator(".file-upload__filename").wait_for(timeout=ANSWER_TIMEOUT_MS)
                case "consent":
                    box = page.locator(f"{FORM} input[type=checkbox][name='{field_id}']").first
                    if box.is_checked() != (answer == "Yes"):
                        browser.click(page.locator(f"label[for='{box.get_attribute('id')}']"))
                case "checkboxes":
                    group = page.locator(f"{FORM} fieldset[id='{field_id}']")
                    for value in values:
                        browser.click(group.locator("label").filter(has_text=_exact(value)))
                case "select" | "multi":
                    suggested = self._fields[field_id].type is FieldType.TYPEAHEAD
                    for value in values:
                        _select(page, field_id, value, suggested)
                case "tel":  # the number's dial code picks the country
                    page.locator(_by_id(field_id)).press_sequentially(values[0], delay=KEY_DELAY_MS)
                case "number" if not re.fullmatch(r"-?\d+(\.\d+)?", values[0]):
                    pass  # the input takes no text: left empty, it reads back as a mismatch
                case _:
                    page.locator(_by_id(field_id)).fill(values[0])

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = page.locator(FORM).evaluate(_READ_BACK, list(self._fields))
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        browser.click(page.locator(f"{FORM} button[type=submit]"))
        page.wait_for_url(re.compile(r"/confirmation"), timeout=SUBMIT_TIMEOUT_MS)
        return page.url

    def _field(self, page: Page, raw: dict[str, Any]) -> Field:
        kind, options = raw["kind"], raw.get("options")
        match kind:
            case "text" | "number":
                field_type = FieldType.TEXT
            case "tel":
                field_type = FieldType.PHONE
            case "file":
                field_type = FieldType.FILE
            case "consent":
                field_type, options = FieldType.CHOICE, YES_NO
            case "checkboxes":
                field_type = FieldType.MULTI_CHOICE
            case "select" | "multi":
                options = self._options(page, raw["id"])
                if options is None:
                    field_type = FieldType.TYPEAHEAD
                else:
                    field_type = FieldType.MULTI_CHOICE if kind == "multi" else FieldType.CHOICE
            case _:  # a radio input, not seen on a posting yet
                reason = f"unhandled_rendering: {raw['id']} is a {kind}"
                return Field(
                    raw["id"], raw["label"], FieldType.TEXT, raw["required"], unfillable=reason
                )
        return Field(
            raw["id"],
            raw["label"],
            field_type,
            raw["required"],
            options,
            raw.get("maxLength"),
            raw.get("accept"),
        )

    def _options(self, page: Page, field_id: str) -> list[str] | None:
        """A select's options, from opening it; None for one that suggests as you type."""
        box = page.locator(_by_id(field_id))
        box.click()
        menu = page.locator(f"{_by_id(f'react-select-{field_id}-listbox')} [role=option]")
        try:
            menu.first.wait_for(timeout=OPTIONS_TIMEOUT_MS)
        except Timeout:
            box.press("Escape")
            return None
        options = [t.strip() for t in menu.all_inner_texts()]
        box.press("Escape")
        return options


def _wait_until_live(page: Page) -> None:
    """Waits for React to take over the form, which it shows a second or two before: until
    then a select does not open and late sections (a consent box) are missing."""
    select = page.locator(f"{FORM} [role=combobox]").first
    if not select.count():
        return
    listbox = page.locator("[id^='react-select-'][id$='-listbox']")
    deadline = time.monotonic() + HYDRATE_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        select.click()
        try:
            listbox.first.wait_for(timeout=HYDRATE_POLL_MS)
        except Timeout:
            continue
        select.press("Escape")
        return


def _board_job(url: str) -> tuple[str, str] | None:
    """The board and job id of a board URL: `/<board>/jobs/<id>` or `embed/job_app?for&token`."""
    parts = urlsplit(url)
    if not HOSTS.match(parts.hostname or ""):
        return None
    if match := re.match(r"^/([^/]+)/jobs/(\d+)", parts.path):
        return match[1], match[2]
    query = parse_qs(parts.query)
    if "for" in query and "token" in query:
        return query["for"][0], query["token"][0]
    return None


def _api_fields(job: dict[str, Any]) -> list[Field] | None:
    if any(job.get(k) for k in UNMAPPED) or any(
        c.get("requires_consent") or c.get("requires_processing_consent")
        for c in job.get("data_compliance") or []
    ):
        return None
    fields = []
    for question in job["questions"] + (job.get("location_questions") or []):
        entry = question["fields"][0]  # a file's paste-in textarea comes second
        if entry["type"] == "input_hidden":  # a location's latitude and longitude
            continue
        name = entry["name"]
        field_type = FieldType.PHONE if name == "phone" else API_TYPES[entry["type"]]
        if name == "location":
            name, field_type = "candidate-location", FieldType.TYPEAHEAD
        options = [v["label"] for v in entry.get("values") or []] or None
        fields.append(Field(name, question["label"], field_type, question["required"], options))
    return fields


def _by_id(element_id: str) -> str:
    """Greenhouse ids can start with a digit or hold brackets, which `#id` does not take."""
    return f'[id="{element_id}"]'


def _upload(field_id: str) -> str:
    return f".file-upload[aria-labelledby='upload-label-{field_id}']"


def _exact(value: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(value)}\s*$")


def _select(page: Page, field_id: str, value: str, suggested: bool) -> None:
    """Types into a react-select and picks the option: hovered then Enter, which every select
    takes. A suggestion list takes its first suggestion. No option leaves the select empty."""
    box = page.locator(_by_id(field_id))
    box.click()
    box.press_sequentially(value, delay=KEY_DELAY_MS)
    menu = page.locator(f"{_by_id(f'react-select-{field_id}-listbox')} [role=option]")
    option = menu.first if suggested else menu.filter(has_text=_exact(value)).first
    try:
        option.wait_for(timeout=ANSWER_TIMEOUT_MS)
    except Timeout:
        box.press("Escape")
        return
    option.hover()
    box.press("Enter")
