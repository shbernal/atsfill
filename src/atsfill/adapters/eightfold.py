"""Eightfold (`<employer>.eightfold.ai/careers/job/<pid>` career sites, and the same pages on
the employer's own domain), as docs/adapters/eightfold.md observes.

A posting's apply page (`/careers/apply?pid=<pid>`) renders its form from a questions API:
sections of questions by id, type, requirement and options, and the questions another one's
answer shows. `read_api` fetches it over HTTP, `read_form` through the page's browser context,
and both list the fields from it. Each question renders as a control whose element id is its
section's title and its id, with every other character an underscore.
"""

import http.cookiejar
import json
import re
import urllib.request
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from atsfill.core import browser
from atsfill.core.adapter import Blocked, Gap, NotTaken, Unsupported
from atsfill.core.browser import HTTP_TIMEOUT_S, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOST = re.compile(r"^[\w-]+\.eightfold\.ai$")
JOB = re.compile(r"^/careers/job/(\d+)")
APPLY_PATH = "/careers/apply"
KEPT = ("domain", "microsite")  # the query parameters that pick the employer's site
POSITION = "/api/pcsx/position_details?position_id={pid}&hl=en"
QUESTIONS = "/api/application/v2/questions?pids={pid}&user_mode=logged_out_candidate"
CSRF = re.compile(r'<meta name="_csrf" content="([^"]+)"')
SUBMIT = "[data-test-id=submitApplicationButton]"
AGREE = "#confirmUploadResume"  # the privacy dialog a resume upload opens
COOKIES = [
    "#onetrust-reject-all-handler",
    "#truste-consent-required",
    "#didomi-notice-disagree-button",
]
# An employer's own banner, declined by its button's name.
BANNERS = ", ".join(
    f"[{attribute}*={word} i]"
    for word in ("cookie", "consent", "truste")
    for attribute in ("id", "class")
)
DECLINE = re.compile(
    r"^\s*((reject|decline|refuse|deny)( all)?( cookies)?|(required|necessary|essential) only)\s*$",
    re.IGNORECASE,
)
PICKER = "[data-testid=picker-dialog]"
MAX_DEPTH = 5  # the deepest chain of questions rendering one another that a label names"
FORM_TIMEOUT_MS = 30_000
COOKIE_TIMEOUT_MS = 5_000
SHOWN_TIMEOUT_MS = 2_000
OPTIONS_TIMEOUT_MS = 5_000
UPLOAD_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
PARSE_SETTLE_MS = 2_000
RESUME_UPLOAD = re.compile(r"/api/application/v2/resume_upload")
PICKER_MONTHS = 1200  # the furthest a date is paged to, in months
DATE_FORMATS = ["%d %b %Y", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%b %d, %Y"]
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0"
DIAL = re.compile(r"\(\+([\d ]+)\)")
FLAG = 0x1F1E6  # regional indicator A: a flag emoji is the country's code in these letters
PRIMARY = {"1": "US", "7": "RU", "44": "GB", "47": "NO", "61": "AU", "358": "FI"}

# A question's kind, by its type; a dropdown whose options load from another answer is typed.
KINDS = {
    "text": "text",
    "select": "select",
    "radio": "radio",
    "checkbox": "checkbox",
    "date": "date",
    "phone": "phone",
    "resumeUpload": "file",
    "fileUpload": "file",
    "positionLocation": "pills",
}
TYPES = {
    "text": FieldType.TEXT,
    "select": FieldType.CHOICE,
    "lookup": FieldType.TYPEAHEAD,
    "radio": FieldType.CHOICE,
    "checkbox": FieldType.MULTI_CHOICE,
    "date": FieldType.DATE,
    "phone": FieldType.PHONE,
    "file": FieldType.FILE,
    "pills": FieldType.MULTI_CHOICE,
}
NOT_FIELDS = {"instruction"}

_FETCH = """async ([url, body]) => {
  const csrf = document.querySelector('meta[name=_csrf]')?.content;
  const init = body === null ? {} : {method: 'POST', body, headers: {
    'Content-Type': 'application/json', 'X-CSRF-Token': csrf}};
  const r = await fetch(url, init);
  return r.ok ? {text: await r.text()} : {status: r.status};
}"""

# The element id of each question's control, by its id: the shortest id on the page that ends
# with it, among the ids each field's error line carries.
_DOM_IDS = """keys => {
  const ids = [...document.querySelectorAll('[id$="_error"]')].map(e => e.id.slice(0, -6));
  const out = {};
  for (const [qid, ends] of keys) {
    const hits = ids.filter(id => ends.some(end => id === end || id.endsWith('_' + end)));
    if (hits.length) out[qid] = hits.sort((a, b) => a.length - b.length)[0];
  }
  return out;
}"""

_READ_BACK = """controls => {
  const out = {};
  for (const [qid, dom, kind] of controls) {
    const err = document.getElementById(dom + '_error');
    const box = err?.parentElement;
    if (!box) continue;
    const text = el => (el?.textContent || '').trim();
    if (kind === 'text') {
      out[qid] = box.querySelector('input:not([type=file]), textarea')?.value ?? null;
    } else if (kind === 'select' || kind === 'lookup' || kind === 'date') {
      out[qid] = box.querySelector('input[role=combobox]')?.value ?? null;
    } else if (kind === 'radio') {
      const on = box.querySelector('input[type=radio]:checked');
      out[qid] = on ? text(box.querySelector(`label[for="${CSS.escape(on.id)}"]`)) : null;
    } else if (kind === 'checkbox') {
      out[qid] = [...box.querySelectorAll('input[type=checkbox]:checked')]
        .map(on => text(box.querySelector(`label[for="${CSS.escape(on.id)}"]`)));
    } else if (kind === 'pills') {
      out[qid] = [...box.querySelectorAll('[id^=selectPilllist] > span')].map(text);
    } else if (kind === 'phone') {
      const code = box.querySelector('input[role=combobox]')?.value || '';
      const dial = (code.match(/\\(\\+([\\d ]+)\\)/) || [])[1];
      const number = box.querySelector('input:not([role=combobox])')?.value || '';
      out[qid] = number ? (dial ? `+${dial.replace(/ /g, '')} ${number}` : number) : null;
    } else if (kind === 'file') {
      const names = [...box.querySelectorAll('li span[title]')].map(s => s.title);
      out[qid] = names[0] ?? null;
    }
  }
  return out;
}"""


@dataclass
class _Control:
    field: Field
    kind: str
    ends: list[str]  # what the control's element id ends with
    # the questions whose answer renders it, each with the answers that do (any when empty)
    parents: list[tuple[str, list[str]]] = field(default_factory=list)
    resume: bool = False
    dom: str | None = None


class Eightfold:
    name = "eightfold"
    known_gaps: dict[str, Gap] = {}
    side_effects = [
        SideEffect(
            "upload",
            "hiring_system",
            "the resume is uploaded to Eightfold and parsed at once, after agreeing to the "
            "dialog that says job recommendations follow by email",
        ),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._controls: list[_Control] = []  # in the form's order

    def detect(self, url: str, page: Page | None) -> bool:
        if _pid(url) is None:
            return False
        if page is None or HOST.match(urlsplit(url).hostname or ""):
            return True
        return page.locator("meta[name=_csrf]").count() > 0 and "_EF_GROUP_ID" in page.content()

    def posting(self, url: str) -> str:
        parts, pid = urlsplit(url), _pid(url)
        return f"https://{parts.hostname}/careers/job/{pid}" if pid else url

    def read_api(self, url: str) -> list[Field] | None:
        if (pid := _pid(url)) is None:
            return None
        try:
            jar = urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
            opener = urllib.request.build_opener(jar)
            opener.addheaders = [("User-Agent", UA)]
            base = _base(url)
            position = json.loads(_get(opener, base + _query(POSITION.format(pid=pid), url)))
            _applies(position)
            html = _get(opener, _apply_url(url, pid))
            if (csrf := CSRF.search(html)) is None:
                return None
            request = urllib.request.Request(
                base + _query(QUESTIONS.format(pid=pid), url),
                data=b'{"answers":{}}',
                headers={"Content-Type": "application/json", "X-CSRF-Token": csrf[1]},
            )
            api = json.loads(_get(opener, request))
            return [c.field for c in _form(api["data"])]
        except OSError, ValueError, KeyError, TypeError, Unsupported, Blocked:
            return None

    def read_form(self, page: Page) -> list[Field]:
        pid = _pid(page.url)
        if pid is None:
            raise Unsupported(f"posting_gone: {page.url} is not a job page")
        position = page.evaluate(_FETCH, [_query(POSITION.format(pid=pid), page.url), None])
        if "status" in position:
            raise Unsupported(f"posting_gone: the position API answered {position['status']}")
        _applies(json.loads(position["text"]))
        if urlsplit(page.url).path != APPLY_PATH:
            page.goto(_apply_url(page.url, pid))
        try:
            page.locator(SUBMIT).wait_for(timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the apply page") from None
        api = page.evaluate(_FETCH, [_query(QUESTIONS.format(pid=pid), page.url), '{"answers":{}}'])
        if "status" in api:
            raise Unsupported(f"posting_gone: the questions API answered {api['status']}")
        self._controls = _form(json.loads(api["text"])["data"])
        _decline_cookies(page)
        doms = page.evaluate(_DOM_IDS, [[c.field.id, c.ends] for c in self._controls])
        for control in self._controls:
            control.dom = doms.get(control.field.id)
            if control.field.unfillable:
                continue
            if control.dom is None and not control.parents:
                reason = f"unhandled_rendering: {control.field.id} is not on the page"
                control.field = replace(control.field, unfillable=reason)
            elif control.dom is not None and _prefilled(page, control):
                control.field = replace(control.field, prefilled=True)
        fields = [c.field for c in self._controls]
        self._controls = [c for c in self._controls if not c.field.unfillable]
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        # the resume first: what its parse finds is written over the fields filled before it
        order = sorted(self._controls, key=lambda c: not c.resume)
        _decline_cookies(page, 0)  # a banner shown since the read
        given: dict[str, Answer] = {}
        for control in order:
            if (answer := answers.get(control.field.id)) is None:
                continue
            if control.dom is None:  # rendered once a question that shows it is answered
                if not any(_shows(given.get(p), values) for p, values in control.parents):
                    continue
                with suppress(Timeout):
                    _box_of(page, control).wait_for(timeout=SHOWN_TIMEOUT_MS)
                control.dom = _find(page, control)
                if control.dom is None:
                    continue
            try:
                _fill(page, control, answer)
            except browser.Covered:  # a banner that showed late
                page.keyboard.press("Escape")
                _decline_cookies(page)
                _fill(page, control, answer)
            given[control.field.id] = answer

    def read_back(self, page: Page) -> dict[str, Answer]:
        unplaced = [[c.field.id, c.ends] for c in self._controls if c.dom is None]
        doms = page.evaluate(_DOM_IDS, unplaced) if unplaced else {}
        for control in self._controls:
            control.dom = control.dom or doms.get(control.field.id)
        shown = [c for c in self._controls if c.dom]
        values = page.evaluate(_READ_BACK, [[c.field.id, c.dom, c.kind] for c in shown])
        out: dict[str, Answer] = {}
        for control in shown:
            value = values.get(control.field.id)
            if control.kind == "date" and value:
                value = _iso(value)
            if value not in (None, "", []):
                out[control.field.id] = value
        return out

    def submit(self, page: Page) -> str | None:
        """Clicks submit; the evidence is the application API's answer."""
        with page.expect_response(
            lambda r: (
                r.request.method == "POST" and "/api/application/" in r.url and "apply" in r.url
            ),
            timeout=SUBMIT_TIMEOUT_MS,
        ) as applied:
            _click(page.locator(SUBMIT))
        response = applied.value
        if not response.ok:
            raise RuntimeError(f"apply answered {response.status}: {response.text()[:200]}")
        return f"{response.url.split('?')[0]} {response.status}"


def _pid(url: str) -> str | None:
    parts = urlsplit(url)
    if job := JOB.match(parts.path):
        return job[1]
    if parts.path == APPLY_PATH and (pid := parse_qs(parts.query).get("pid")):
        return pid[0] if pid[0].isdigit() else None
    return None


def _base(url: str) -> str:
    return f"https://{urlsplit(url).hostname}"


def _query(path: str, url: str) -> str:
    """The path with the parameters of `url` that pick the employer's site."""
    kept = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items() if k in KEPT}
    return f"{path}&{urlencode(kept)}" if kept else path


def _apply_url(url: str, pid: str) -> str:
    return _base(url) + _query(f"{APPLY_PATH}?pid={pid}", url)


def _get(opener, request) -> str:
    with opener.open(request, timeout=HTTP_TIMEOUT_S) as response:
        return response.read().decode()


def _applies(position: dict[str, Any]) -> None:
    """Raises when the posting is not applied to on this page."""
    data = position.get("data") or {}
    action = ((data.get("positionUserActions") or {}).get("applyAction")) or {}
    match action.get("status"):
        case "link_off":
            host = urlsplit(action.get("applyUrl") or "").hostname
            raise Unsupported(f"external_step: the application is on {host}")
        case "log_in":
            raise Blocked("login_required: the employer's career site asks to sign in to apply")
        case "allowed" | None:
            return
        case other:
            raise Unsupported(f"posting_gone: the apply action is {other}")


def _norm(qid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", qid)


def _plain(label: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", label)).strip()


def _label(q: dict[str, Any], by_id: dict[str, dict[str, Any]], depth: int = 0) -> str:
    """The question's text; one some of a parent's answers render says so, naming the first
    parent by its own label, so that a parent asked for other answers names it apart."""
    label = _plain(q.get("label") or "")
    first = (q.get("parentDependencies") or [{}])[0]
    if not first.get("values") or depth > MAX_DEPTH:
        return label
    parent = by_id.get(str(first["questionId"]), {})
    labels = {str(c["value"]): c["label"] for c in _choices(parent)}
    shown = " or ".join(labels.get(str(v), str(v)) for v in first["values"])
    named = _label(parent, by_id, depth + 1)
    quote = '"' if "'" in named else "'"  # never escaped: the outermost is the last
    return f"{label} (asked when {quote}{named}{quote} is {shown})"


def _choices(q: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("selectOptions", "radioOptions", "checkboxOptions"):
        if (options := q.get(key)) and options.get("choices"):
            return options["choices"]
    return []


def _form(data: dict[str, Any]) -> list[_Control]:
    questions = [q for s in data["fields"] for q in s["questions"]]
    by_id = {str(q["questionId"]): q for q in questions}
    controls = []
    for q in questions:
        if q.get("type") in NOT_FIELDS:
            continue
        controls.append(_control(q, by_id))
    return controls


def _control(q: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> _Control:
    qid, label = str(q["questionId"]), _label(q, by_id)
    kind = KINDS.get(q.get("type", ""))
    choices = _choices(q)
    if kind == "select" and not choices and (q.get("selectOptions") or {}).get("apiEndpoint"):
        kind = "lookup"  # options load once the question it depends on is answered
    required = bool(q.get("required"))
    parents = []
    for dependency in q.get("parentDependencies") or []:
        parent = by_id.get(str(dependency["questionId"]), {})
        labels = {str(c["value"]): c["label"] for c in _choices(parent)}
        values = [labels.get(str(v), str(v)) for v in dependency.get("values") or []]
        parents.append((str(dependency["questionId"]), values))
    if parents and parents[0][1]:  # asked for some of its parent's answers only
        required = False
    ends = list(dict.fromkeys([_norm(qid), qid]))
    if kind is None:
        reason = f"unhandled_rendering: {qid} is a question of type {q.get('type')}"
        return _Control(Field(qid, label, FieldType.TEXT, required, unfillable=reason), "", ends)
    options = [c["label"] for c in choices] or None
    accept = None
    if kind == "file":
        files = q.get("resumeOptions") or q.get("fileUploadOptions") or {}
        types = files.get("acceptedFileTypes") or (files.get("fileFormats") or "").split(",")
        accept = [t.strip() for t in types if t.strip()] or None
    today = bool((q.get("dateOptions") or {}).get("today"))  # today's date, read-only
    question = Field(qid, label, TYPES[kind], required, options, accept=accept, prefilled=today)
    return _Control(question, kind, ends, parents, q.get("type") == "resumeUpload")


def _decline_cookies(page: Page, wait_ms: int = COOKIE_TIMEOUT_MS) -> None:
    """Declines the employer's cookie banner, which covers the form's lower half."""
    decline = page.locator(", ".join(COOKIES)).or_(
        page.locator(BANNERS).get_by_role("button", name=DECLINE)
    )
    if wait_ms:  # a timeout of 0 waits for ever
        with suppress(Timeout):
            decline.first.wait_for(timeout=wait_ms)
    for button in decline.all():
        if button.is_visible():
            button.click()
            return


def _find(page: Page, control: _Control) -> str | None:
    return page.evaluate(_DOM_IDS, [[control.field.id, control.ends]]).get(control.field.id)


def _box_of(page: Page, control: _Control):
    ends = " , ".join(f'[id$="_{end}_error"]' for end in control.ends if '"' not in end)
    return page.locator(ends or "#none").first


def _box(page: Page, control: _Control):
    return page.locator(f"[id={json.dumps(f'{control.dom}_error')}]").locator("xpath=..")


def _prefilled(page: Page, control: _Control) -> bool:
    if control.kind in ("text", "select", "date"):
        el = _box(page, control).locator("input:not([type=file]), textarea").first
        return bool(el.count() and el.input_value())
    if control.kind == "pills":
        return _box(page, control).locator("[id^=selectPilllist]").count() > 0
    return False


def _fill(page: Page, control: _Control, answer: Answer) -> None:
    box = _box(page, control)
    value = answer if isinstance(answer, str) else (answer[0] if answer else "")
    match control.kind:
        case "text":
            box.locator("input:not([type=file]), textarea").first.fill(value)
        case "select":
            _pick(page, box.locator("input[role=combobox]").first, value, exact=True)
        case "lookup":
            _pick(page, box.locator("input[role=combobox]").first, value, exact=False)
        case "radio" | "checkbox":  # the input lies over its label
            picks = [answer] if isinstance(answer, str) else answer
            labels = box.locator("label[for]").all()
            if missing := set(picks) - {label.inner_text().strip() for label in labels}:
                raise NotTaken(f"no option {sorted(missing)[0]!r}")
            for label in labels:
                choice = box.locator(f"[id={json.dumps(label.get_attribute('for'))}]")
                if choice.is_checked() != (label.inner_text().strip() in picks):
                    _click(choice)
        case "pills":
            picks = [answer] if isinstance(answer, str) else answer
            pills = box.locator("[id^=selectPilllist]")
            for pill in pills.all():
                if pill.locator("span").first.inner_text().strip() not in picks:
                    _click(pill.locator("button"))
            shown = [p.locator("span").first.inner_text().strip() for p in pills.all()]
            for pick in picks:
                if pick not in shown:
                    _pick(page, box.locator("input[role=combobox]").first, pick, exact=True)
        case "date":
            _date(page, box.locator("input[role=combobox]").first, date.fromisoformat(value))
        case "phone":
            _phone(page, box, value)
        case "file":
            _upload(page, box, value, control.resume)


def _pick(page: Page, combo, value: str, exact: bool) -> None:
    """Opens a dropdown and clicks the option with the value; a lookup's options are those its
    API gives for the answers so far."""
    _click(combo)
    options = page.locator(f"[id={json.dumps(combo.get_attribute('aria-controls'))}] [role=option]")
    with suppress(Timeout):
        options.first.wait_for(timeout=OPTIONS_TIMEOUT_MS)
    pattern = _exact(value) if exact else re.compile(rf"^\s*{re.escape(value)}", re.IGNORECASE)
    option = options.filter(has_text=pattern).first
    if not option.count():
        page.keyboard.press("Escape")
        raise NotTaken(f"no option {value!r}")
    option.click()


def _phone(page: Page, box, value: str) -> None:
    """Picks the country code the number starts with, the longest that fits, then types the
    rest of the number. Where several countries share it, its main country."""
    digits = re.sub(r"\D", "", value)
    combo = box.locator("input[role=combobox]").first
    number = box.locator("input:not([role=combobox])").first
    if not value.startswith("+"):
        number.fill(value)
        return
    _click(combo)
    options = page.locator(f"[id={json.dumps(combo.get_attribute('aria-controls'))}] [role=option]")
    options.first.wait_for(timeout=OPTIONS_TIMEOUT_MS)
    texts = options.all_inner_texts()
    fits = []
    for i, text in enumerate(texts):
        if (dial := DIAL.search(text)) and digits.startswith(code := dial[1].replace(" ", "")):
            fits.append((len(code), _country(text) == PRIMARY.get(code), -i, code))
    if not fits:
        page.keyboard.press("Escape")
        number.fill(digits)
        return
    _, _, i, code = max(fits)
    options.nth(-i).click()
    number.fill(digits[len(code) :])


def _country(text: str) -> str:
    letters = [chr(ord(c) - FLAG + ord("A")) for c in text[:2] if FLAG <= ord(c) < FLAG + 26]
    return "".join(letters)


def _date(page: Page, combo, day: date) -> None:
    """Opens the date picker and pages to the day's month, then clicks it."""
    _click(combo)
    picker = page.locator(PICKER).last
    picker.wait_for(timeout=OPTIONS_TIMEOUT_MS)
    cell = picker.locator(f"td[title='{day.isoformat()}'] [role=button]")
    for _ in range(PICKER_MONTHS):
        if cell.count():
            break
        shown = picker.locator("td[title]").first.get_attribute("title") or ""
        step = "next" if shown < day.isoformat() else "prev"
        picker.locator(f"[data-testid=picker-header-{step}-btn]").click()
    cell.click()


def _upload(page: Page, box, path: str, resume: bool) -> None:
    """Attaches a file; a resume first asks to agree to the site's privacy terms, and is then
    uploaded and parsed at once."""
    box.locator("input[type=file]").first.set_input_files(path)
    if resume:
        with suppress(Timeout):
            page.locator(AGREE).wait_for(timeout=OPTIONS_TIMEOUT_MS)
        if page.locator(AGREE).is_visible():
            with page.expect_response(RESUME_UPLOAD, timeout=UPLOAD_TIMEOUT_MS):
                _click(page.locator(AGREE))
            page.wait_for_timeout(PARSE_SETTLE_MS)  # what the parse finds lands after
    box.locator("li span[title]").first.wait_for(timeout=UPLOAD_TIMEOUT_MS)


def _click(target) -> None:
    """Clicks the target scrolled to the middle of the window, clear of the sticky footer."""
    target.evaluate("el => el.scrollIntoView({block: 'center'})")
    browser.click(target)


def _iso(shown: str) -> str:
    """A date as the picker shows it, in the site's locale, back to ISO."""
    for pattern in DATE_FORMATS:
        with suppress(ValueError):
            return datetime.strptime(shown, pattern).date().isoformat()
    return shown


def _shows(answer: Answer | None, values: list[str]) -> bool:
    """Whether a parent's answer renders its dependent: any answer when `values` is empty."""
    if answer in (None, "", []):
        return False
    picks = [answer] if isinstance(answer, str) else answer
    return not values or any(p in values for p in picks)


def _exact(value: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(value)}\s*$")
