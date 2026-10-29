"""Rippling Recruiting (`ats.rippling.com/<board>/jobs/<job>`), as docs/adapters/rippling.md
observes.

The job's apply page (`…/apply`) carries its form in the page data: the standard questions by
id and type, the employer's question groups, and whether the EEO questionnaire is asked.
`request_read` fetches the page over HTTP, `browser_read` reads the data of the page it opened, and
both list the fields from it. Each field renders in an element whose `data-testid` is the
field's id; the résumé is uploaded and parsed as soon as it is attached.
"""

import json
import re
import urllib.request
from contextlib import suppress
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser
from atsfill.core.adapter import Gap, NotTaken, Unsupported, out_of_scope
from atsfill.core.browser import HTTP_TIMEOUT_S, Locator, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, Is, SideEffect

HOST = "ats.rippling.com"
JOB = re.compile(r"^(?:/[a-z]{2}-[A-Z]{2})?/([^/]+)/jobs/([0-9a-f-]{36})(?:/|$)")
POSTING = "https://ats.rippling.com/{board}/jobs/{job}"
APPLY = POSTING + "/apply"
APPLIED = re.compile(r"^https://ats\.rippling\.com/api/.*/apply")
PARSED = re.compile(r"/parse_resume$")
PARSED_NOTICE = re.compile(r"^Résumé parsing was (successful|unsuccessful)")
DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
FORM_TIMEOUT_MS = 30_000
PARSE_TIMEOUT_MS = 30_000
UPLOAD_TIMEOUT_MS = 60_000
NOTICE_TIMEOUT_MS = 15_000
OPTIONS_TIMEOUT_MS = 10_000
PLACES_QUIET_MS = 500  # how long the place suggestions stay unchanged before one is picked
SUBMIT_TIMEOUT_MS = 90_000
DIAL_CODES = (3, 2, 1)  # the lengths a dial code can have, longest tried first
FILE_TYPES = [".doc", ".docx", ".pdf"]
OTHER = "Other"
TESTIDS = {"pronouns": "pronouns_strategy"}  # a field whose element is not named by its id

# A standard question's type, by its `fieldType`; the location is a place typeahead.
BASIC = {
    "SHORT_ANSWER": FieldType.TEXT,
    "PHONE_NUMBER": FieldType.PHONE,
    "FILE": FieldType.FILE,
    "PRONOUN": FieldType.CHOICE,
}
# An employer's question's type, by its `questionType`.
CUSTOM = {
    "SHORT_ANSWER": FieldType.TEXT,
    "LONG_ANSWER": FieldType.TEXT,
    "DATE": FieldType.DATE,
    "DOCUMENT_UPLOAD": FieldType.FILE,
    "SINGLE_SELECT_RADIO": FieldType.CHOICE,
    "SINGLE_SELECT_DROPDOWN": FieldType.CHOICE,
    "KNOCKOUT": FieldType.CHOICE,
    "YES_NO_SCALE_4": FieldType.CHOICE,
    "MULTI_SELECT_CHECKBOX": FieldType.MULTI_CHOICE,
}
SCALE = ["Strong No", "No", "Yes", "Strong Yes"]
# "Enter my pronouns" opens a text box instead of picking.
PRONOUNS = [
    "She/her/hers",
    "He/him/his",
    "They/them/theirs",
    "She/her/hers, they/them/theirs",
    "He/him/his, they/them/theirs",
    "Just use my name",
]
# The EEO questionnaire, the same on every form that asks it; every answer optional.
EEOC = [
    ("eeoc.gender", "Gender", ["Male", "Female", "Non-binary", "Choose not to disclose"]),
    (
        "eeoc.race",
        "Please identify your race",
        [
            "American Indian or Alaskan Native",
            "Asian",
            "Black or African American",
            "Middle Eastern or North African",
            "White",
            "Native Hawaiian or other Pacific Islander",
            "Two or more races",
            "Choose not to disclose",
        ],
    ),
    ("eeoc.hispanicOrLatino", "Are you Hispanic/Latino?", ["Yes", "No", "Choose not to disclose"]),
    (
        "eeoc.veteranStatus",
        "Veteran Status",
        [
            "I am not a protected veteran",
            "I identify as one or more of the classifications of a protected veteran",
            "Choose not to disclose",
        ],
    ),
]
SMS = (
    "sms_opt_in",
    "Text message updates about this application",
    [
        "Yes - I consent to receiving text messages",
        "No - I do not consent to receiving text messages",
    ],
)

# Resolves true once no suggestion list has changed for `quiet` ms, false after `limit` ms.
_QUIET = """([quiet, limit]) => new Promise(done => {
    let timer;
    const end = (settled) => { observer.disconnect(); clearTimeout(timer); clearTimeout(cap);
        done(settled); };
    const wait = () => { clearTimeout(timer); timer = setTimeout(() => end(true), quiet); };
    const list = n => n.nodeType === 1
        && (n.matches("[role=listbox]") || n.querySelector("[role=listbox]"));
    const listed = m => (m.target.nodeType === 1 ? m.target : m.target.parentElement)
        ?.closest("[role=listbox]") || [...m.addedNodes, ...m.removedNodes].some(list);
    const observer = new MutationObserver(ms => { if (ms.some(listed)) wait(); });
    observer.observe(document.body, {childList: true, subtree: true, characterData: true});
    const cap = setTimeout(() => end(false), limit);
    wait();
})"""

# The field's control and what it shows, by its rendering: the first element carrying the
# field's `data-testid`, or the second for an "Other" answer's text box.
_SHOWN = """(root, [id, nth, testids]) => {
  const el = [...root.querySelectorAll("[data-testid]")]
    .filter(e => e.dataset.testid === (testids[id] ?? id))[nth];
  if (!el) return {kind: "missing"};
  const text = e => e.innerText.trim();
  if (el.getAttribute("role") === "radiogroup") {
    const on = el.querySelector("[role=radio][aria-checked=true]");
    return {kind: "radio", value: on ? (on.getAttribute("aria-label") || text(on)) : null};
  }
  if (el.getAttribute("role") === "group")
    return {kind: "checkbox", value: [...el.querySelectorAll("[role=checkbox]")]
      .filter(e => e.getAttribute("aria-checked") === "true").map(text)};
  if (el.querySelector("input[type=file]")) {
    // the chip shortens a long name; the upload's status line keeps it whole
    const status = [...el.querySelectorAll("[role=status]")]
      .map(e => text(e).match(/^File (.+) uploaded successfully$/)).find(m => m);
    const chip = el.querySelector("[data-testid=chip] [id$=-content]");
    return {kind: "file", value: status ? status[1] : chip ? text(chip) : null};
  }
  if (el.querySelector("[data-input=month]")) {
    const part = p => el.querySelector(`[data-input=${p}]`).value;
    const [y, m, d] = [part("year"), part("month"), part("day")];
    return {kind: "date",
            value: y && m && d ? `${y}-${m.padStart(2, "0")}-${d.padStart(2, "0")}` : null};
  }
  const box = el.querySelector("[role=combobox]");
  if (box && box.tagName !== "INPUT")
    return {kind: "dropdown", value: box.querySelector("p") ? null : text(box)};
  if (box) return {kind: "search", value: box.value || null};
  const input = el.matches("input, textarea") ? el : el.querySelector("input, textarea");
  if (input) return {kind: "text", value: input.value};
  return {kind: "unknown"};
}"""


class Rippling:
    name = "rippling"
    known_gaps: dict[str, Gap] = {}
    filters = ["ats.rippling.com###transcend-consent-manager"]  # Transcend's banner
    side_effects = [
        SideEffect("upload", "hiring_system", "an attached file is uploaded, a résumé parsed"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._fields: dict[str, Field] = {}

    def detect(self, url: str, page: Page | None) -> bool:
        return _job(url) is not None

    def posting(self, url: str) -> str:
        job = _job(url)
        return POSTING.format(board=job[0], job=job[1]) if job else url

    def request_read(self, url: str) -> list[Field] | None:
        if (job := _job(url)) is None:
            return None
        request = urllib.request.Request(
            APPLY.format(board=job[0], job=job[1]), headers={"User-Agent": "Mozilla/5.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
                html = response.read().decode()
            match = DATA.search(html)
            return _form(_api(json.loads(match[1]))) if match else None
        except OSError, ValueError, KeyError, TypeError, Unsupported:
            return None

    def browser_read(self, page: Page) -> list[Field]:
        job = _job(page.url)
        assert job is not None
        apply = APPLY.format(board=job[0], job=job[1])
        if urlsplit(page.url).path != urlsplit(apply).path:
            page.goto(apply)
        try:
            page.locator("[data-testid=Apply]").wait_for(timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        data = page.locator("#__NEXT_DATA__").text_content() or "{}"
        out = []
        for field in _form(_api(json.loads(data))):
            if not field.unfillable and not field.id.endswith(("_other", "_comment")):
                kind = page.locator("body").evaluate(_SHOWN, [field.id, 0, TESTIDS])["kind"]
                if kind in ("missing", "unknown"):
                    reason = f"unhandled_rendering: {field.id} is {kind} on the page"
                    field = replace(field, unfillable=reason)
            out.append(field)
        self._fields = {f.id: f for f in out if not f.unfillable}
        return out

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        if "resume" in answers:  # its parse fills the fields it finds: first, so they are ours
            with page.expect_response(
                lambda r: PARSED.search(urlsplit(r.url).path) is not None,
                timeout=PARSE_TIMEOUT_MS,
            ):
                _attach(page, "resume", answers["resume"])
            # The page writes the parse and shows its notice a moment after the response; the
            # notice then covers the fields below it while it shows.
            notice = page.get_by_text(PARSED_NOTICE)
            with suppress(Timeout):
                notice.wait_for(timeout=NOTICE_TIMEOUT_MS)
                notice.wait_for(state="hidden", timeout=NOTICE_TIMEOUT_MS)
        for field_id, field in self._fields.items():
            if field_id == "resume" or field_id not in answers:
                continue
            answer = answers[field_id]
            value = answer if isinstance(answer, str) else answer[0]
            if field_id.endswith("_comment"):
                _comment(page, field_id.removesuffix("_comment"), value)
            elif field_id.endswith("_other"):
                page.locator(f"[data-testid='{field_id.removesuffix('_other')}']").nth(1).locator(
                    "input"
                ).fill(value)
            elif field_id == "phone_number":
                _phone(page, value)
            elif field_id == "location":
                _place(page, value)
            elif field.type is FieldType.FILE:
                _attach(page, field_id, value)
            else:
                _fill(page, field_id, answer)

    def read_back(self, page: Page) -> dict[str, Answer]:
        body, out = page.locator("body"), {}
        for field_id in self._fields:
            if field_id.endswith("_other"):
                shown = body.evaluate(_SHOWN, [field_id.removesuffix("_other"), 1, TESTIDS])
            elif field_id == "phone_number":
                code = page.locator("[data-testid='phone_number-code'] input").input_value()
                number = page.locator("[data-testid='input-phone_number']").input_value()
                shown = {"value": f"{code.split()[0]} {number}" if number else None}
            elif field_id == "location":
                shown = {"value": page.locator("[data-testid=location] input").input_value()}
            else:
                shown = body.evaluate(_SHOWN, [field_id, 0, TESTIDS])
            if (value := shown.get("value")) not in (None, "", []):
                out[field_id] = value
        return out

    def submit(self, page: Page) -> str | None:
        """Clicks Apply; the evidence is the answer to the application's POST."""
        with page.expect_response(
            lambda r: r.request.method == "POST" and APPLIED.match(r.url) is not None,
            timeout=SUBMIT_TIMEOUT_MS,
        ) as applied:
            _click(page.locator("[data-testid=Apply]"))
        response = applied.value
        if not response.ok:
            raise RuntimeError(f"apply answered {response.status}: {response.text()[:200]}")
        return f"{response.url} {response.status}"


def _job(url: str) -> tuple[str, str] | None:
    parts = urlsplit(url)
    if parts.hostname != HOST or not (match := JOB.match(parts.path)):
        return None
    return match[1], match[2]


def _api(data: dict[str, Any]) -> dict[str, Any]:
    api = data["props"]["pageProps"].get("apiData") or {}
    if not (api.get("jobPost") or {}).get("activeJobApplication"):
        raise Unsupported("posting_gone: the page has no application form")
    return api


def _form(api: dict[str, Any]) -> list[Field]:
    """Every field of the apply page's data, in the page's order."""
    post = api["jobPost"]
    application = post["activeJobApplication"]
    out = [_basic(q) for q in application["basicQuestions"]]
    for group in application.get("additionalQuestions") or []:
        for q in group["form"]["questions"]:
            out += _custom(f"customQuestions.{group['id']}.{q['uniqueKey']}", q)
    if post.get("eeocQuestionnaireEnabledForJobPost"):
        out += [Field(i, label, FieldType.CHOICE, False, options) for i, label, options in EEOC]
    if api.get("showCc305Form"):
        reason = "unhandled_rendering: cc305 is the voluntary disability form"
        out.append(Field("cc305", "Voluntary self-identification of disability", FieldType.CHOICE,
                         False, unfillable=reason))  # fmt: skip
    for n, question in enumerate(api.get("videoScreeningQuestions") or []):
        field_id = f"video.{question.get('uniqueKey') or question.get('id') or n}"
        label = question.get("title") or question.get("question") or "Video question"
        out.append(Field(field_id, label, FieldType.FILE, True,
                         unfillable=out_of_scope(field_id, "a video recorded live")))  # fmt: skip
    out.append(Field(SMS[0], SMS[1], FieldType.CHOICE, False, SMS[2]))
    return out


def _basic(q: dict[str, Any]) -> Field:
    field_id, label, required = q["oid"], q["title"], bool(q.get("required"))
    if (kind := BASIC.get(q["fieldType"])) is None:
        reason = f"unhandled_rendering: {field_id} is a {q['fieldType']}"
        return Field(field_id, label, FieldType.TEXT, required, unfillable=reason)
    if field_id == "location":
        kind = FieldType.TYPEAHEAD
    options = PRONOUNS if kind is FieldType.CHOICE else None
    accept = FILE_TYPES if kind is FieldType.FILE else None
    return Field(field_id, label, kind, required, options, accept=accept)


def _custom(field_id: str, q: dict[str, Any]) -> list[Field]:
    label, required = q["title"].strip(), bool(q.get("isRequired"))
    if (kind := CUSTOM.get(q["questionType"])) is None:
        reason = f"unhandled_rendering: {field_id} is a {q['questionType']}"
        return [Field(field_id, label, FieldType.TEXT, required, unfillable=reason)]
    options = None
    if kind in (FieldType.CHOICE, FieldType.MULTI_CHOICE):
        options = SCALE if q["questionType"] == "YES_NO_SCALE_4" else list(q["strChoices"])
        if q.get("isOtherEnabled"):
            options.append(OTHER)
    max_length = q.get("maxChars") if kind is FieldType.TEXT else None
    out = [Field(field_id, label, kind, required, options, max_length)]
    if q.get("isOtherEnabled"):  # marked required as its list is
        out.append(Field(f"{field_id}_other", f"{label}: {OTHER}", FieldType.TEXT, required,
                         shown_when=Is(field_id, [OTHER])))  # fmt: skip
    if q.get("allowComments"):
        out.append(Field(f"{field_id}_comment", f"{label}: comment", FieldType.TEXT, False))
    return out


def _click(target: Locator) -> None:
    """Clicks the target scrolled to the middle of the window, clear of the Apply bar fixed
    at its bottom."""
    target.evaluate("e => e.scrollIntoView({block: 'center'})")
    browser.click(target)


def _box(page: Page, field_id: str) -> Locator:
    return page.locator(f"[data-testid='{TESTIDS.get(field_id, field_id)}']").first


def _option(page: Page, field_id: str, value: str) -> None:
    """Picks the open list's option whose text is `value`."""
    option = page.locator("[role=listbox] [role=option]").filter(has_text=_exact(value))
    try:
        option.first.wait_for(timeout=OPTIONS_TIMEOUT_MS)
    except Timeout:
        page.keyboard.press("Escape")
        raise NotTaken(f"{field_id}: no option {value!r}") from None
    _click(option.first)


def _fill(page: Page, field_id: str, answer: Answer) -> None:
    box = _box(page, field_id)
    values = [answer] if isinstance(answer, str) else answer
    match page.locator("body").evaluate(_SHOWN, [field_id, 0, TESTIDS])["kind"]:
        case "radio":
            radio = box.locator("[role=radio]").filter(has_text=_exact(values[0]))
            if not radio.count():
                radio = box.locator(f"[role=radio][aria-label='{values[0]}']")
            _click(radio.first)
        case "checkbox":
            for item in box.locator("[role=checkbox]").all():
                if (item.get_attribute("aria-checked") == "true") != (
                    item.inner_text().strip() in values
                ):
                    _click(item)
        case "date":
            year, month, day = values[0].split("-")
            for part, text in (("month", month), ("day", day), ("year", year)):
                box.locator(f"[data-input={part}]").fill(text)
        case "dropdown" | "search":
            _click(box.locator("[role=combobox]"))
            _option(page, field_id, values[0])
        case _:
            own = box.evaluate("e => e.matches('input, textarea')")
            (box if own else box.locator("input, textarea").first).fill(values[0])


def _attach(page: Page, field_id: str, path: Answer) -> None:
    box = _box(page, field_id)
    box.locator("input[type=file]").set_input_files(path if isinstance(path, str) else path[0])
    box.locator("[data-testid=chip]").first.wait_for(timeout=UPLOAD_TIMEOUT_MS)


def _comment(page: Page, field_id: str, value: str) -> None:
    comment = page.locator(f"[data-testid='{field_id}_comment'] textarea")
    if not comment.count():
        block = _box(page, field_id).locator("xpath=ancestor::*[@data-testid='field'][1]/..")
        _click(block.locator("button[data-testid='Add comment']").first)
    comment.fill(value)


def _phone(page: Page, value: str) -> None:
    """Picks the dial code the number starts with, unless the shown one fits, then types the
    rest."""
    digits = re.sub(r"\D", "", value)
    code = page.locator("[data-testid='phone_number-code'] input")
    shown = re.sub(r"\D", "", code.input_value().split()[0] if code.input_value() else "")
    if value.startswith("+") and not (shown and digits.startswith(shown)):
        for length in DIAL_CODES:
            _click(code)
            code.fill(f"+{digits[:length]}")
            option = page.locator("[role=listbox] [role=option]").filter(
                has_text=re.compile(rf"^\s*\+{digits[:length]} ")
            )
            try:
                option.first.wait_for(timeout=OPTIONS_TIMEOUT_MS // len(DIAL_CODES))
            except Timeout:
                continue
            _click(option.first)
            shown = digits[:length]
            break
        else:
            page.keyboard.press("Escape")
            raise NotTaken(f"phone_number: no dial code for {value!r}")
    national = digits[len(shown) :] if value.startswith("+") else digits
    page.locator("[data-testid='input-phone_number']").fill(national)


def _place(page: Page, value: str) -> None:
    """Types the place and picks the first suggestion once the list has settled: each key
    typed renders its own suggestions a moment later, replacing the ones shown."""
    box = page.locator("[data-testid=location] input")
    box.fill("")
    box.press_sequentially(value)
    option = page.locator("[role=listbox] [role=option]")
    try:
        option.first.wait_for(timeout=OPTIONS_TIMEOUT_MS)
    except Timeout:
        raise NotTaken(f"location: no place suggested for {value!r}") from None
    if not page.evaluate(_QUIET, [PLACES_QUIET_MS, OPTIONS_TIMEOUT_MS]):
        raise NotTaken(f"location: the places suggested for {value!r} kept changing")
    _click(option.first)


def _exact(value: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(value)}\s*$")
