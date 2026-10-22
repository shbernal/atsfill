"""HiringThing (`<employer>.<white-label domain>/job/<id>/<slug>`), as docs/adapters/hiringthing.md
observes.

The job page carries the posting's configuration in a React component's props, and its Apply
button renders the form from them in the page: MUI inputs named by field id, radios and boxes,
react-select comboboxes for the country and state, Dropzone areas that upload a file as soon as
it is attached. `read_form` lists the rendered controls; there is no `read_api`, since which
controls render is the page's logic over the props, not the props alone.
"""

import json
import re
from contextlib import suppress
from datetime import datetime
from urllib.parse import urlsplit

from atsfill.core.adapter import Blocked, Gap, NotTaken, Unsupported
from atsfill.core.browser import Locator, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

# The white-label domains HiringThing serves its career sites on, as freehire indexes them.
DOMAINS = (
    "hiringthing.com",
    "oasisrecruit.com",
    "prismhr-hire.com",
    "gnahiring.com",
    "primepay-recruit.com",
    "checkwritersrecruit.com",
    "exponentats.com",
    "rippling-ats.com",
    "verahr-hiring.com",
    "alphastaff-hiring.com",
    "elevate-ats.com",
    "iconnecthire.com",
    "atsmodule.com",
    "viewpointhr-ats.com",
    "ezhiregov.com",
    "lumberhiring.com",
    "topdoghrrecruiting.com",
    "teammemberhire-recruit.com",
    "edistoats.com",
    "topgradinghire.com",
)
JOB = re.compile(r"^/job/(\d+)(?:/|$)")
PROPS = "[data-react-class='HiringThing.Components.ApplyButtonGroup']"
APPLY = "button.job-apply-button"
FORM = "#job-application-form"
SUBMIT = f"{FORM} button[type=submit]"
BANNER = ".cookie-banner"
UPLOAD = "/client_api/add_file"
FORM_TIMEOUT_MS = 30_000
PAGE_TIMEOUT_MS = 30_000
MENU_TIMEOUT_MS = 5_000
REGIONS_TIMEOUT_MS = 10_000
UPLOAD_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
REQUIRED = re.compile(r"\s*\(required\)\s*$")
DATE = "%m/%d/%Y"
YES_NO = ["Yes", "No"]
TYPES = {
    "text": FieldType.TEXT,
    "date": FieldType.DATE,
    "phone": FieldType.PHONE,
    "radio": FieldType.CHOICE,
    "box": FieldType.CHOICE,
    "boxes": FieldType.MULTI_CHOICE,
    "select": FieldType.CHOICE,
    "region": FieldType.TYPEAHEAD,
    "file": FieldType.FILE,
}

# Each control of the form by its name: its kind, label, options; a Dropzone area by its id.
_CONTROLS = """form => {
  const text = e => (e?.innerText ?? "").replace(/\\s+/g, " ").trim();
  const option = e => text(e.closest(".multi-option") ?? e.closest("label"));
  const labelOf = id => document.getElementById(`${id}_label`);
  const TEXTS = {text: "text", email: "text", textarea: "text", tel: "phone"};
  const out = new Map();
  for (const el of form.querySelectorAll("input, textarea, select")) {
    const name = el.getAttribute("name") || "";
    if (el.getAttribute("role") === "combobox") {
      const id = el.id;
      const kind = id.endsWith(".state") ? "region" : "select";
      out.set(id, {id, kind, label: text(labelOf(id))});
      continue;
    }
    if (!name || el.type === "hidden" || el.type === "file" || name.endsWith("phoneCountry"))
      continue;
    const label = labelOf(name);
    if (el.type === "radio" || el.type === "checkbox") {
      const own = el.type === "radio" ? el.value : option(el);
      const entry = out.get(name) ?? {id: name, kind: el.type, label: text(label), options: []};
      entry.options.push(own);
      if (el.type === "checkbox") {
        entry.kind = label?.classList.contains("multi-field-label") ? "boxes" : "box";
        if (entry.kind === "box") entry.label = own;
      }
      out.set(name, entry);
      continue;
    }
    const typed = el.tagName === "INPUT" ? el.type : el.tagName.toLowerCase();
    let kind = TEXTS[typed] ?? "unhandled";
    if (label?.classList.contains("datePicker-field-label")) kind = "date";
    const maxLength = el.maxLength > 0 ? el.maxLength : null;
    out.set(name, {id: name, kind, label: text(label), maxLength, typed});
  }
  for (const zone of form.querySelectorAll(".file-field-input[id]"))
    out.set(zone.id, {id: zone.id, kind: "file", label: text(labelOf(`${zone.id}.file`))});
  return [...out.values()];
}"""

# What each field holds: a value, the checked option, a combobox's shown value, a file's name.
_READ_BACK = """(form, fields) => {
  const text = e => (e?.innerText ?? "").replace(/\\s+/g, " ").trim();
  const option = e => text(e.closest(".multi-option") ?? e.closest("label"));
  const out = {};
  for (const [id, kind] of fields) {
    const named = [...form.querySelectorAll(`[name="${id}"]`)];
    if (kind === "radio") out[id] = named.find(e => e.checked)?.value ?? null;
    else if (kind === "boxes")
      out[id] = named.filter(e => e.checked).map(option);
    else if (kind === "box") out[id] = named[0]?.checked ? "Yes" : "No";
    else if (kind === "select" || kind === "region") {
      const select = document.getElementById(id)?.closest(".Select");
      out[id] = text(select?.querySelector(".Select-value-label")) || null;
    } else if (kind === "file") {
      const zone = document.getElementById(id);
      const names = [...(zone?.querySelectorAll(".dz-complete .dz-filename") ?? [])];
      out[id] = names.length ? text(names[names.length - 1]) : null;
    } else out[id] = named[0]?.value ?? null;
  }
  return out;
}"""


class HiringThing:
    name = "hiringthing"
    known_gaps: dict[str, Gap] = {}
    side_effects = [
        SideEffect("upload", "hiring_system", "a file is uploaded to HiringThing once attached"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._kinds: dict[str, str] = {}

    def detect(self, url: str, page: Page | None) -> bool:
        parts = urlsplit(url)
        if not JOB.match(parts.path):
            return False
        host = parts.hostname or ""
        if any(host.endswith(f".{d}") for d in DOMAINS):
            return True
        return page is not None and page.locator(PROPS).count() > 0

    def posting(self, url: str) -> str:
        parts = urlsplit(url)
        job = JOB.match(parts.path)
        return f"https://{parts.hostname}/job/{job[1]}" if job else url

    def read_form(self, page: Page) -> list[Field]:
        props = page.locator(PROPS).first
        try:
            props.wait_for(state="attached", timeout=PAGE_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application on the page") from None
        _hide_banner(page)
        form = page.locator(FORM)
        if not form.count():
            page.locator(APPLY).filter(visible=True).first.click()
        try:
            page.locator(SUBMIT).wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: Apply showed no form") from None
        if form.locator("[id='user.state']:not([role=combobox])").count():
            with suppress(Timeout):  # the state list loads after the form, and replaces its input
                _combobox(page, "user.state").wait_for(state="attached", timeout=REGIONS_TIMEOUT_MS)
        questions = _questions(props.get_attribute("data-react-props") or "{}")
        controls = form.evaluate(_CONTROLS)
        self._kinds = {c["id"]: c["kind"] for c in controls if c["kind"] != "unhandled"}
        return [
            _field(c, questions, _menu(page, c["id"]) if c["kind"] == "select" else None)
            for c in controls
        ]

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        _hide_banner(page)
        form = page.locator(FORM)
        # the state last: its options are the chosen country's
        for field_id, kind in sorted(self._kinds.items(), key=lambda k: k[1] == "region"):
            if field_id not in answers:
                continue
            answer = answers[field_id]
            value = answer if isinstance(answer, str) else answer[0]
            named = form.locator(f"[name='{field_id}']")
            match kind:
                case "text" | "phone":
                    named.fill(value)
                case "date":
                    named.fill("")
                    named.press_sequentially(datetime.fromisoformat(value).strftime(DATE))
                    named.press("Tab")
                case "radio":
                    form.locator(
                        f"input[type=radio][name='{field_id}'][value={json.dumps(value)}]"
                    ).check()
                case "box":
                    named.set_checked(value == "Yes")
                case "boxes":
                    for option in [answer] if isinstance(answer, str) else answer:
                        exact = re.compile(f"^\\s*{re.escape(option)}\\s*$")
                        box = form.locator(
                            ".multi-option", has=page.locator(f"[name='{field_id}']")
                        )
                        box = box.filter(has_text=exact)
                        box.locator("input").first.check()
                case "select" | "region":
                    _pick(page, field_id, value, exact=kind == "select")
                case "file":
                    _attach(page, field_id, value)

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = page.locator(FORM).evaluate(_READ_BACK, list(self._kinds.items()))
        out: dict[str, Answer] = {}
        for field_id, value in values.items():
            if self._kinds[field_id] == "date" and value:
                with suppress(ValueError):
                    value = datetime.strptime(value, DATE).date().isoformat()
            if value not in (None, "", []):
                out[field_id] = value
        return out

    def submit(self, page: Page) -> str | None:
        """Clicks Submit Application; the evidence is the answer to the application's POST."""
        host = urlsplit(page.url).hostname
        with page.expect_response(
            lambda r: (
                r.request.method == "POST"
                and urlsplit(r.url).hostname == host
                and not r.url.endswith(UPLOAD)
            ),
            timeout=SUBMIT_TIMEOUT_MS,
        ) as applied:
            page.locator(SUBMIT).click()
        response = applied.value
        if page.locator("iframe[src*='recaptcha'][title*='challenge']").count():
            raise Blocked("captcha: a reCAPTCHA challenge showed on submit")
        if not response.ok:
            raise RuntimeError(f"apply answered {response.status}: {response.text()[:200]}")
        return f"{response.url} {response.status}"


def _questions(props: str) -> dict[str, str]:
    """Each question's text by its id, from the job's props."""
    with suppress(ValueError, KeyError, TypeError):
        table = json.loads(props)["jobObj"]["table"]
        return {str(q["table"]["id"]): q["table"]["question"] for q in table.get("questions") or []}
    return {}


def _field(control: dict, questions: dict[str, str], options: list[str] | None) -> Field:
    """A field from a rendered control; a history entry's field labelled with its question."""
    field_id, kind = control["id"], control["kind"]
    label = control["label"] or field_id
    required = bool(REQUIRED.search(label))
    label = REQUIRED.sub("", label)
    if entry := re.match(r"job_assessment\.question_(\d+)\.response\.\d+\.", field_id):
        label = f"{questions.get(entry[1], 'History')} {label}"
    if kind == "unhandled":
        reason = f"unhandled_rendering: {field_id} is a {control['typed']}"
        return Field(field_id, label, FieldType.TEXT, required, unfillable=reason)
    if kind == "box":
        options = YES_NO
        required = "disclosure_agree" in field_id  # an agreement, not a preference
    elif kind in ("radio", "boxes"):
        options = control["options"]
    max_length = control.get("maxLength") if kind == "text" else None
    return Field(field_id, label, TYPES[kind], required, options, max_length)


def _hide_banner(page: Page) -> None:
    """Hides the cookie banner, which covers the form's bottom, without answering it."""
    with suppress(Exception):
        page.locator(BANNER).evaluate_all("es => es.forEach(e => e.style.display = 'none')")


def _combobox(page: Page, field_id: str) -> Locator:
    return page.locator(f"{FORM} input[role=combobox][id='{field_id}']")


def _menu(page: Page, field_id: str) -> list[str]:
    """A react-select's options, read by opening its menu and closing it again."""
    box = _combobox(page, field_id)
    box.focus()
    box.press("ArrowDown")
    options = page.locator(".Select-menu-outer .Select-option")
    with suppress(Timeout):
        options.first.wait_for(timeout=MENU_TIMEOUT_MS)
    texts = [" ".join(t.split()) for t in options.all_inner_texts()]
    box.press("Escape")
    return texts


def _pick(page: Page, field_id: str, value: str, exact: bool) -> None:
    """Types into a react-select and clicks the option that is the answer, or for a typeahead
    the first that starts with it."""
    box = _combobox(page, field_id)
    box.focus()
    box.press_sequentially(value)
    options = page.locator(".Select-menu-outer .Select-option")
    try:
        options.first.wait_for(timeout=MENU_TIMEOUT_MS)
    except Timeout:
        box.press("Escape")
        raise NotTaken(f"no option {value!r} in {field_id}") from None
    texts = [" ".join(t.split()) for t in options.all_inner_texts()]
    hit = next((i for i, t in enumerate(texts) if t == value), None)
    if hit is None and not exact:
        hit = next((i for i, t in enumerate(texts) if t.startswith(value)), None)
    if hit is None:
        box.press("Escape")
        raise NotTaken(f"no option {value!r} in {field_id}")
    options.nth(hit).click()


def _attach(page: Page, field_id: str, path: str) -> None:
    """Attaches a file through its Dropzone area's file chooser, and waits for its upload."""
    zone = page.locator(f"{FORM} [id='{field_id}'] .dropzone")
    with page.expect_file_chooser() as chooser:
        zone.click()
    chooser.value.set_files(path)
    name = path.rsplit("/", 1)[-1]
    done = zone.locator(".dz-complete .dz-filename", has_text=name)
    with suppress(Timeout):
        done.first.wait_for(timeout=UPLOAD_TIMEOUT_MS)
