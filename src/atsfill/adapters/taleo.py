"""Taleo Enterprise career sections
(`<zone>.taleo.net/careersection/<section>/jobdetail.ftl?job=…`), as docs/adapters/taleo.md
observes.

Applying needs an account, one per zone: the account scope. The form is a wizard of
server-rendered pages, one in the DOM at a time, each saved before the next shows.
"""

import html
import re
import secrets
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from atsfill.core.adapter import Blocked, Gap, NotTaken, Refused, SignUpDetails, Unsupported
from atsfill.core.browser import Page, Request, Timeout
from atsfill.core.results import Answer, AnyOf, Field, FieldType, Is, OptionSet, SideEffect

HOST = re.compile(r"^(?P<zone>[a-z0-9-]+)\.taleo\.net$")
PATH = re.compile(r"^/careersection/(?P<section>[^/]+)/(jobdetail|jobapply)\.ftl$")
LOGIN_PATH = "/careersection/iam/accessmanagement/"
SECURITY_PATH = "/accessmanagement/changeQuestionsAnswers.jsf"
FLOW_PATH = "/candidateacquisition/flow.jsf"
SECURITY_QUESTION = "Which value did atsfill generate?"
FORM_TIMEOUT_MS = 30_000
SAVE_TIMEOUT_MS = 60_000
LEVEL_TIMEOUT_MS = 10_000
UPLOAD_TIMEOUT_MS = 60_000
SUBMIT_TIMEOUT_MS = 90_000
SETTLE_MS = 500
SIGN_IN_TIMEOUT_S = 30
SIGN_IN_POLL_MS = 500
MAX_GATES = 4  # pages apply can pass before the wizard: an agreement, security questions

FORM_ID = "dialogTemplate-dialogForm"
NEXT = "input[type=button][id$='-saveContinueCmdBottom']"
CONFIRM = "input[type=button][id$='-saveContinueCmdFlowPopUp']:visible"
SUBMIT = "input[type=button][value='Submit']:visible"
ACCEPT = re.compile(r"^\s*(I )?(accept|agree)", re.IGNORECASE)
FIRST_PAGE = "a[id$='-0-dtGotoPageLink']"
ERRORS_HEADING = "Errors occurred"
SKIPPED_STEPS = {"Resume Parsing"}  # its upload parses a resume into the profile, not used
SKIPPED_TITLES = {"Authentication"}
SOURCE_TYPE, SOURCES = "recruitmentSourceType", "recruitmentSourceDP"
ERROR_TEXT = re.compile(r'class="[^"]*[eE]rror[^"]*"[^>]*>(.*?)</', re.DOTALL)
LOGGED = re.compile(r'id="[^"]*:Logged"[^>]*value="(true|false)"')
LOCATION_GAP = "location_levels"
DATE_PICKER_GAP = "date_picker"
YES_NO = ["Yes", "No"]
PLACEHOLDER = re.compile(r"^(not specified|select one\.*|no selection|month|year|)$", re.I)
NANP = re.compile(r"^\+?1?\D*(\d{3})\D*(\d{3})\D*(\d{4})$")
NANP_MASKED = re.compile(r"^\(\d{3}\)\d{3}-\d{4}$")

ERRORS = [
    (re.compile(r"already (in use|exists|used)|is not available|already registered"),
     Refused, "email_in_use"),
    (re.compile(r"(user name|username) (and|or) (the )?password|does not correspond|"
                r"invalid (user|credentials)"), Refused, "wrong_password"),
    (re.compile(r"password"), Refused, "password_rule"),
    (re.compile(r"captcha"), Blocked, "captcha"),
    (re.compile(r"locked|too many"), Blocked, "rate_limited"),
]  # fmt: skip

# Each control of the current page, in page order, by how it renders. A question's radios or
# boxes are one field, named by their fieldset's legend; a month and a year select, one.
_READ = r"""() => {
  const clean = s => (s || "").replace(/\s+/g, " ").trim();
  const shown = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const text = el => {
    if (!el) return "";
    const copy = el.cloneNode(true);
    copy.querySelectorAll(".hidden-audible, img, .index").forEach(n => n.remove());
    return clean(copy.textContent).replace(/[\s.*:]+$/, "");
  };
  const marked = el => !!el && (/Required/.test(el.textContent) ||
    !!el.querySelector?.("img.requiredimg"));
  const labelOf = id => document.querySelector(`label[for="${CSS.escape(id)}"]`);
  const placeholder = t => /^(not specified|select one\.*|no selection|month|year|)$/i.test(t);
  const boxLabel = b => text(b.closest("label") || labelOf(b.id));
  const out = [], seen = new Set();
  for (const el of document.querySelectorAll("input, select, textarea")) {
    const type = (el.type || "").toLowerCase();
    if (["hidden", "button", "submit", "password", "image"].includes(type)) continue;
    if (/sendMeJobs|ResumeParsing/i.test(el.id)) continue;
    if (/AttachedFilesBlock/.test(el.id) && !/uploadedFile$/.test(el.id)) continue;
    const explanation = /questionExplanationText$/.test(el.id);
    if (!shown(el) && !explanation) continue;
    const set = el.closest("fieldset");
    const group = set && /PossibleAnswer/.test(el.id);
    if (type === "radio" || (type === "checkbox" && group)) {
      const legend = set?.querySelector("legend label") || set?.querySelector("legend");
      const id = legend?.getAttribute("for") || el.name;
      if (seen.has(id)) continue;
      seen.add(id);
      const boxes = [...(set || document).querySelectorAll(`input[type=${type}]`)]
        .filter(b => type === "checkbox" || b.name === el.name);
      out.push({id, kind: type === "radio" ? "radio" : "checks",
        label: text(legend?.querySelector(".description") || legend) || id,
        required: marked(legend), options: boxes.map(boxLabel),
        value: boxes.filter(b => b.checked).map(boxLabel), controls: boxes.map(b => b.id)});
      continue;
    }
    if (el.tagName === "SELECT" && /\.(month|year)$/.test(el.id)) {
      const base = el.id.replace(/\.(month|year)$/, "");
      if (seen.has(base)) continue;
      seen.add(base);
      const month = document.getElementById(base + ".month");
      const year = document.getElementById(base + ".year");
      const label = labelOf(base) || el.closest("fieldset")?.querySelector("legend");
      const pick = s => s && !placeholder(clean(s.selectedOptions[0]?.textContent)) ?
        clean(s.selectedOptions[0].textContent) : "";
      const m = pick(month), y = pick(year);
      const number = m ? String(month.selectedIndex).padStart(2, "0") : "";
      out.push({id: base, kind: "monthyear", required: marked(label),
        label: text(label) || base.split(/[_-]/).pop(), options: [],
        value: m && y ? `${number}/${y}` : ""});
      continue;
    }
    const id = el.id;
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const label = labelOf(id);
    const field = {id, label: text(label) || el.title || id, required: marked(label),
                   kind: el.tagName === "TEXTAREA" ? "text" : type, options: [],
                   value: el.value, maxLength: el.maxLength > 0 ? el.maxLength : null};
    if (explanation) {
      const legend = el.closest(".questionnaire")?.querySelector("fieldset legend label");
      field.parent = legend?.getAttribute("for") || null;
      field.kind = "text";
    }
    if (el.tagName === "SELECT") {
      field.level = +((id.match(/Location-(\d+)$/) || [0, -1])[1]);
      field.kind = field.level >= 0 ? "level" : "select";
      field.options = [...el.options].map(o => clean(o.textContent)).filter(t => !placeholder(t))
        .filter(t => !(/recruitmentSourceType$/.test(id) && /event ID/i.test(t)));
      const chosen = clean(el.selectedOptions[0]?.textContent);
      field.value = placeholder(chosen) ? "" : chosen;
    } else if (type === "checkbox") {
      field.value = el.checked ? "Yes" : "No";
    } else if (type === "file") {
      field.accept = (el.accept || "").split(",").filter(Boolean);
      field.value = "";
    } else if (/Phone$/i.test(id)) field.kind = "phone";
    else if (["text", "email", "number", "tel", ""].includes(type)) field.kind = "text";
    out.push(field);
  }
  for (const a of document.querySelectorAll("a[id$='.display']")) {
    if (!shown(a)) continue;
    const id = a.id.replace(/\.display$/, "");
    const label = labelOf(id);
    out.push({id, kind: "datepicker", label: text(label) || "Date", required: marked(label),
              options: [], value: ""});
  }
  const levels = out.filter(f => f.kind === "level");
  for (const f of levels) {  // the section marks the levels required, not each select
    const section = document.getElementById(f.id)?.closest("fieldset, table");
    const mark = section?.querySelector("legend, .requiredimg");
    f.required = f.required || marked(mark?.parentElement);
  }
  return out;
}"""

KINDS = {
    "text": FieldType.TEXT,
    "phone": FieldType.PHONE,
    "select": FieldType.CHOICE,
    "level": FieldType.CHOICE,
    "radio": FieldType.CHOICE,
    "checkbox": FieldType.CHOICE,
    "checks": FieldType.MULTI_CHOICE,
    "monthyear": FieldType.TEXT,
    "file": FieldType.FILE,
    "datepicker": FieldType.DATE,
}


@dataclass(frozen=True)
class Posting:
    zone: str
    section: str
    job: str

    @property
    def base(self) -> str:
        return f"https://{self.zone}.taleo.net/careersection/{self.section}"

    @property
    def detail(self) -> str:
        return f"{self.base}/jobdetail.ftl?job={self.job}"

    @property
    def apply(self) -> str:
        return f"{self.base}/jobapply.ftl?job={self.job}&lang=en"


def posting(url: str) -> Posting | None:
    parts = urlsplit(url)
    host = HOST.match((parts.hostname or "").lower())
    path = PATH.match(parts.path)
    job = parse_qs(parts.query).get("job", [""])[0]
    if not host or not path or not job:
        return None
    return Posting(host["zone"], path["section"], job)


class Taleo:
    name = "taleo"
    per_employer = True
    asks: set[str] = set()
    known_gaps = {LOCATION_GAP: Gap.RENDERING, DATE_PICKER_GAP: Gap.RENDERING}
    side_effects = [
        SideEffect("sign_up", "employer", "the account is created in the employer's zone"),
        SideEffect("fill", "employer", "each page is saved to a draft application"),
        SideEffect("upload", "employer", "an attachment is stored with the draft"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._postings: dict[str, Posting] = {}  # by zone, the last posting's
        self._listed: set[str] = set()  # the ids the read listed
        self._walked: set[str] = set()  # the pages the read reached
        self._read_back: dict[str, Answer] = {}

    def detect(self, url: str, page: Page | None) -> bool:
        return posting(url) is not None

    def posting(self, url: str) -> str:
        found = posting(url)
        return found.detail if found else url

    def account_scope(self, url: str) -> str | None:
        found = posting(url)
        if found is None:
            return None
        self._postings[found.zone] = found
        return found.zone

    def login_url(self, scope: str | None) -> str:
        """The zone's sign-in, back to the last posting's form once signed in."""
        if scope not in self._postings:
            raise ValueError(f"no Taleo posting seen for the zone {scope!r}")
        found = self._postings[scope]
        return (f"https://{scope}.taleo.net{LOGIN_PATH}login.jsf?lang=en"
                f"&redirectionURI={quote(found.apply, safe='')}")  # fmt: skip

    def signed_in(self, request: Request, scope: str | None) -> bool:
        """The posting's form loads instead of redirecting to the sign-in."""
        if scope not in self._postings:
            return False
        response = request.get(self._postings[scope].apply)
        path = urlsplit(response.url).path
        if not response.ok or LOGIN_PATH in path or SECURITY_PATH in path:
            return False
        logged = LOGGED.search(response.text())  # a privacy agreement shows to a visitor too
        return logged is None or logged[1] == "true"

    def sign_in(self, page: Page, email: str, password: str) -> None:
        name = page.locator(f"#{FORM_ID}-login-name1")
        name.wait_for(timeout=FORM_TIMEOUT_MS)
        name.fill(email)
        page.locator(f"#{FORM_ID}-login-password").fill(password)
        page.locator(f"#{FORM_ID}-login-defaultCmd").click()
        _left_sign_in(page)

    def sign_up(self, page: Page, email: str, password: str, details: SignUpDetails) -> None:
        page.locator(f"#{FORM_ID}-login-register").click()
        page.wait_for_load_state()
        accept = page.locator(f"#{FORM_ID}-StatementBeforeAuthentificationContent-ContinueButton")
        if accept.count():
            accept.click()
            page.wait_for_load_state()
        page.locator(f"#{FORM_ID}-userName").wait_for(timeout=FORM_TIMEOUT_MS)
        if not page.locator(f"#{FORM_ID}-password").count():
            raise Unsupported("unhandled_rendering: the zone's sign-up mails the password")
        for field, value in (("userName", email), ("password", password),
                             ("passwordConfirm", password), ("email", email),
                             ("emailConfirm", email)):  # fmt: skip
            control = page.locator(f"#{FORM_ID}-{field}")
            if control.count():
                control.fill(value)
        page.locator(f"#{FORM_ID}-defaultCmd").click()
        _left_sign_in(page)

    def browser_read(self, page: Page) -> list[Field]:
        """Walks the wizard from its first page, saving each page it can: a page that does not
        save with what it holds (a fresh draft's Personal Information) ends the walk, its
        fields listed. Reading saves pages, as a fill does."""
        self._open(page)
        fields: list[Field] = []
        self._walked = set()
        while not _on_review(page):
            step = _step(page)
            self._walked.add(step)
            if step not in SKIPPED_STEPS:
                for raw in _read(page):
                    fields.append(_field(step, raw))
                    if raw["id"].endswith(SOURCE_TYPE):
                        fields.append(_sources(page, step, raw))
            if not _save(page):
                break
        self._listed = {f.id for f in fields}
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        """Fills each page from the first and saves it, until Review and Submit. A page that
        shows a field the read did not list stops the fill unsaved; one that does not save, with
        its messages."""
        self._open(page)
        self._read_back = {}
        while not _on_review(page):
            step = _step(page)
            raws = [] if step in SKIPPED_STEPS else _read(page)
            filled: set[str] = set()
            for shown in (raws, _read(page) if raws else []):  # a list an answer shows
                for raw in sorted(shown, key=_order):
                    field_id = _field_id(step, raw)
                    if field_id in answers and field_id not in filled:
                        _fill(page, raw, answers[field_id])
                        filled.add(field_id)
            answered = _read(page) if raws else []
            self._unread(step, answered)
            for raw in answered:
                value = _value(raw)
                if value not in (None, "", []):
                    self._read_back[_field_id(step, raw)] = value
            if not _save(page):
                raise NotTaken(f"{step} was not saved: {'; '.join(_errors(page))}")

    def _unread(self, step: str, raws: list[dict[str, Any]]) -> None:
        """Stops on fields the read did not list, optional ones too, once the answers are in. A
        page the read did not reach is read once the pages before it are saved. On one it read,
        an answer opened the field: the page stops unsaved, so no read would list it."""
        unread = [r["label"] for r in raws if _field_id(step, r) not in self._listed]
        if not unread:
            return
        labels = ", ".join(unread)
        if step in self._walked:
            raise Unsupported(f"unhandled_rendering: an answer showed {labels}, not listed")
        raise NotTaken(f"{step} shows fields the read did not list: {labels}; read again")

    def read_back(self, page: Page) -> dict[str, Answer]:
        return dict(self._read_back)

    def submit(self, page: Page) -> str | None:
        """Clicks Review and Submit's Submit; Taleo moves to its confirmation page."""
        page.locator(SUBMIT).first.click()
        page.wait_for_load_state()
        page.locator(SUBMIT).first.wait_for(state="detached", timeout=SUBMIT_TIMEOUT_MS)
        return page.url

    def _open(self, page: Page) -> None:
        """The wizard's first page, from the posting's apply URL."""
        found = posting(page.url) or next(reversed(self._postings.values()), None)
        if found is None:
            raise Unsupported("unhandled_rendering: not a Taleo posting page")
        page.goto(found.apply)
        page.wait_for_load_state()
        for _ in range(MAX_GATES):
            path = urlsplit(page.url).path
            if LOGIN_PATH in path:
                raise Blocked("login_wall: the form redirects to the sign-in")
            if SECURITY_PATH in path:
                _security_question(page)
                continue
            if FLOW_PATH in path or page.locator(f"{NEXT}, {SUBMIT}, {FIRST_PAGE}").count():
                break
            accept = page.locator("input[type=button]:visible", has_text=ACCEPT)
            accept = accept if accept.count() else page.get_by_role("button", name=ACCEPT)
            if not accept.count():
                if PATH.match(path) and path.endswith("jobdetail.ftl"):
                    raise Unsupported("posting_gone: the posting does not take applications")
                raise Unsupported(f"unhandled_rendering: apply lands on {path}")
            accept.first.click()
            page.wait_for_load_state()
        first = page.locator(FIRST_PAGE)
        if first.count():
            first.first.click()
            page.wait_for_load_state()
        try:
            page.locator(NEXT).first.wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None


def _left_sign_in(page: Page) -> None:
    """Waits to leave the zone's sign-in pages, or for the error they show."""
    deadline = time.monotonic() + SIGN_IN_TIMEOUT_S
    said = page.locator(".errorMessage:visible, .error:visible, [role=alert]:visible")
    while time.monotonic() < deadline:
        path = urlsplit(page.url).path
        if SECURITY_PATH in path:
            _security_question(page)
            continue
        if LOGIN_PATH not in path:
            page.wait_for_load_state()
            logged = LOGGED.search(page.content())
            if logged and logged[1] == "false":
                text = " ".join(page.locator("body").inner_text().split())[:200]
                raise Unsupported(f"unhandled_rendering: not signed in after the sign-in: {text}")
            return
        if said.count():
            text = " ".join(t.strip() for t in said.all_inner_texts() if t.strip())
            if text:
                for pattern, error, code in ERRORS:
                    if pattern.search(text.lower()):
                        raise error(f"{code}: {text}")
                raise Unsupported(f"unhandled_rendering: the sign-in page says {text!r}")
        page.wait_for_timeout(SIGN_IN_POLL_MS)
    raise Unsupported("unhandled_rendering: the sign-in page answered nothing")


def _security_question(page: Page) -> None:
    """The zone's security questions, asked once signed in: each gets a question of atsfill's
    and a random answer it does not keep, so a lost password goes through the email reset."""
    page.wait_for_load_state()
    for question in page.locator("input[id$=-question]:visible").all():
        if not question.input_value():
            question.fill(SECURITY_QUESTION)
    for answer in page.locator("input[id$=-answer]:visible").all():
        answer.fill(secrets.token_urlsafe(16))
    page.locator(f"#{FORM_ID}-defaultCmd").click()
    page.wait_for_url(lambda url: SECURITY_PATH not in url, timeout=FORM_TIMEOUT_MS)


def _slug(step: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", step.lower()).strip("_")


def _short(dom_id: str) -> str:
    """A control's DOM id without the page's generated segments."""
    short = re.sub(r"^.*?-ftf-(gp-)?", "", dom_id)
    short = re.sub(r"j_id_id\d+pc\d+[-:]?|page_+\d+[-:]?", "", short)
    return re.sub(r"[-:]+", "-", short).strip("-")


def _field_id(step: str, raw: dict[str, Any]) -> str:
    return f"{_slug(step)}/{_short(raw['id'])}"


def _field(step: str, raw: dict[str, Any]) -> Field:
    kind, field_id = raw["kind"], _field_id(step, raw)
    label, required = raw["label"], raw["required"]
    if kind not in KINDS:
        reason = f"unhandled_rendering: {field_id} is a {kind} input"
        return Field(field_id, label, FieldType.TEXT, required, unfillable=reason)
    if kind == "datepicker":
        reason = f"{Gap.RENDERING}: {DATE_PICKER_GAP}: {field_id} is a date picker"
        return Field(field_id, label, FieldType.DATE, required, unfillable=reason)
    if kind == "checkbox":
        return Field(field_id, label, FieldType.CHOICE, required, YES_NO, default="No")
    if kind == "monthyear":
        return Field(field_id, f"{label} (MM/YYYY)", FieldType.TEXT, required)
    if kind == "file":
        return Field(field_id, label, FieldType.FILE, required, accept=raw.get("accept") or None)
    if kind == "level" and raw["level"] > 0:
        if raw.get("value"):  # the profile's place, its list loaded for the level above
            return Field(field_id, label, FieldType.CHOICE, required, raw["options"],
                         prefilled=raw["value"])  # fmt: skip
        reason = (f"{Gap.RENDERING}: {LOCATION_GAP}: {field_id} lists its options only once "
                  "the level above is picked")  # fmt: skip
        return Field(field_id, label, FieldType.CHOICE, required, [], unfillable=reason)
    if raw.get("parent"):  # a question's explanation, asked once it is answered Yes
        parent = Is(f"{_slug(step)}/{_short(raw['parent'])}", ["Yes"])
        return Field(field_id, label, FieldType.TEXT, False, max_length=raw.get("maxLength"),
                     shown_when=parent, required_when=parent)  # fmt: skip
    options = raw["options"] if kind in ("select", "level", "radio", "checks") else None
    value = raw.get("value") or None
    return Field(
        field_id, label, KINDS[kind], required, options, raw.get("maxLength"),
        default=value if kind in ("select", "level", "radio") else None,
    )  # fmt: skip


def _sources(page: Page, step: str, raw: dict[str, Any]) -> Field:
    """The sources a source type lists once picked, loaded by picking each type in turn; the
    page's own pick is put back."""
    source_type = page.locator(f'[id="{raw["id"]}"]')
    kept = source_type.input_value()
    sources = page.locator(f"select[id$='{SOURCES}']")
    sets = []
    for option in raw["options"]:
        source_type.select_option(label=option)
        page.wait_for_load_state("networkidle", timeout=SAVE_TIMEOUT_MS)
        page.wait_for_timeout(SETTLE_MS)
        if sources.count() and sources.first.is_visible():
            names = sources.first.evaluate(
                "e => [...e.options].map(o => o.textContent.replace(/\\s+/g, ' ').trim())"
            )
            names = [n for n in names if not PLACEHOLDER.match(n)]
            if names:
                sets.append(OptionSet(Is(_field_id(step, raw), [option]), names))
    source_type.select_option(kept)
    page.wait_for_load_state("networkidle", timeout=SAVE_TIMEOUT_MS)
    field_id = f"{_slug(step)}/{_short(raw['id']).replace(SOURCE_TYPE, SOURCES)}"
    shown = AnyOf([s.when for s in sets]) if sets else Is(_field_id(step, raw), [])
    return Field(field_id, "Sources", FieldType.CHOICE, True, [], option_sets=sets,
                 shown_when=shown)  # fmt: skip


def _order(raw: dict[str, Any]) -> tuple[bool, int, bool]:
    """Location levels top down before the rest of the page; explanations last."""
    return raw["kind"] != "level", raw.get("level", 0), bool(raw.get("parent"))


def _fill(page: Page, raw: dict[str, Any], answer: Answer) -> None:
    values = [answer] if isinstance(answer, str) else answer
    control = page.locator(f'[id="{raw["id"]}"]')
    match raw["kind"]:
        case "select" | "level":
            labels = control.evaluate("e => [...e.options].map(o => o.textContent)")
            label = next((o for o in labels if " ".join(o.split()) == values[0]), None)
            if label is None:
                raise NotTaken(f"no option {values[0]!r} in {raw['label']}")
            if raw["value"] == values[0]:
                return  # picking it again would reload the levels below
            control.select_option(label=label.strip())
            if raw["kind"] == "level":
                _wait_for_level(page, raw["id"])
        case "radio" | "checks":
            for box_id, option in zip(raw["controls"], raw["options"], strict=True):
                box = page.locator(f'[id="{box_id}"]')
                if box.is_checked() != (option in values):
                    box.click()
            page.wait_for_timeout(SETTLE_MS)
        case "checkbox":
            control.set_checked(values[0] == "Yes")
        case "monthyear":
            month, _, year = values[0].partition("/")
            page.locator(f'[id="{raw["id"]}.month"]').select_option(index=int(month))
            page.locator(f'[id="{raw["id"]}.year"]').select_option(label=year)
        case "file":
            _upload(page, raw["id"], values[0])
        case "datepicker":
            pass
        case "phone":
            control.fill(_masked(values[0]))
        case _:
            control.fill(values[0])


def _masked(number: str) -> str:
    """A +1 number in the North American mask zones validate, `(999)999-9999`; another as given,
    for the `+999999999999999` mask."""
    number = number.strip()
    match = NANP.match(number)
    if not match or (number.startswith("+") and not number.startswith("+1")):
        return number
    return f"({match[1]}){match[2]}-{match[3]}"


def _value(raw: dict[str, Any]) -> Answer | None:
    match raw["kind"]:
        case "checks":
            return raw["value"]
        case "radio":
            return raw["value"][0] if raw["value"] else None
        case "file" | "datepicker":
            return None
        case "phone" if NANP_MASKED.match(raw["value"]):
            return f"+1 {raw['value']}"
        case _:
            return raw["value"]


def _wait_for_level(page: Page, level_id: str) -> None:
    """Waits for the next location level's options, where there is one."""
    following = re.sub(r"-(\d+)$", lambda m: f"-{int(m[1]) + 1}", level_id)
    if not page.locator(f'[id="{following}"]').count():
        return
    with suppress(Timeout):  # a place with nothing below it
        page.wait_for_function(
            "id => (document.getElementById(id)?.options.length || 0) > 1",
            arg=following, timeout=LEVEL_TIMEOUT_MS,
        )  # fmt: skip


def _upload(page: Page, input_id: str, path: str) -> None:
    """Attaches the file with the page's Attach button, then waits for it in the list."""
    name = path.rsplit("/", 1)[-1]
    page.locator(f'[id="{input_id}"]').set_input_files(path)
    page.locator("input[type=button][id$='-attachFileCommand']").first.click()
    page.wait_for_load_state()
    page.get_by_text(name).first.wait_for(timeout=UPLOAD_TIMEOUT_MS)


def _read(page: Page) -> list[dict[str, Any]]:
    page.wait_for_timeout(SETTLE_MS)
    return page.evaluate(_READ)


def _step(page: Page) -> str:
    """The current page's title, the wizard step it is."""
    titles = [" ".join(h.split()) for h in page.locator("h1").all_inner_texts()]
    titles = [t for t in titles if t and t not in SKIPPED_TITLES and ERRORS_HEADING not in t]
    return titles[-1] if titles else "page"  # after a site's own heading


def _on_review(page: Page) -> bool:
    return page.locator(SUBMIT).count() > 0


def _errors(page: Page) -> list[str]:
    """The messages a page that did not save lists, each in an element of an error class."""
    found = [
        " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m)).split())
        for m in ERROR_TEXT.findall(page.content())
    ]
    said = [t for t in dict.fromkeys(found) if t and not t.startswith(ERRORS_HEADING)]
    return said[:5] or ["the page shows errors"]


def _save(page: Page) -> bool:
    """Presses Save and Continue; whether the page saved and moved on."""
    before = _step(page)
    page.locator(NEXT).first.click()
    page.wait_for_load_state()
    confirm = page.locator(CONFIRM)
    if confirm.count():
        confirm.first.click()
        page.wait_for_load_state()
    page.wait_for_load_state("networkidle", timeout=SAVE_TIMEOUT_MS)
    if page.locator(f"h1:has-text('{ERRORS_HEADING}')").count():
        return False
    return _on_review(page) or _step(page) != before
