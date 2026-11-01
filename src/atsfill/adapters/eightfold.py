"""Eightfold (`<employer>.eightfold.ai/careers/job/<pid>` career sites, and the same pages on
the employer's own domain), as docs/adapters/eightfold.md observes.

A posting's apply page (`/careers/apply?pid=<pid>`) renders its form from a questions API:
sections of questions by id, type, requirement and options, and the questions whose answer
shows each follow-up. `request_read` fetches it over HTTP, `browser_read` through the page's browser
context, and both list the fields from it. Each question renders as a control whose element id
is its section's title and its id, with every other character an underscore.
"""

import http.cookiejar
import json
import re
import urllib.request
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from atsfill.core import browser, conditions
from atsfill.core.adapter import Blocked, Gap, NotTaken, Unsupported
from atsfill.core.browser import HTTP_TIMEOUT_S, Page, Timeout
from atsfill.core.results import Answer, AnyOf, Condition, Field, FieldType, Is, SideEffect

HOST = re.compile(r"^[\w-]+\.eightfold\.ai$")
JOB = re.compile(r"^/careers/job/(\d+)")
APPLY_PATH = "/careers/apply"
KEPT = ("domain", "microsite")  # the query parameters that pick the employer's site
POSITION = "/api/pcsx/position_details?position_id={pid}&hl=en"
QUESTIONS = "/api/application/v2/questions?pids={pid}&user_mode=logged_out_candidate"
CSRF = re.compile(r'<meta name="_csrf" content="([^"]+)"')
SUBMIT = "[data-test-id=submitApplicationButton]"
AGREE = "#confirmUploadResume"  # the privacy dialog a resume upload opens
PICKER = "[data-testid=picker-dialog]"
FORM_TIMEOUT_MS = 30_000
SHOWN_TIMEOUT_MS = 2_000
OPTIONS_TIMEOUT_MS = 5_000
FOCUS_TIMEOUT_MS = 500
LIST_CLOSE_TIMEOUT_MS = 2_000  # a list can close late on a page still loading
PICK_CLICKS = 3  # the list can lose a click on its option
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

# The element ids of the controls on the page.
_SHOWN = """() => [...document.querySelectorAll('[id$="_error"]')].map(e => e.id.slice(0, -6))"""

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
        self._fields: list[Field] = []
        self._ends: dict[str, str] = {}  # each question's id, by what its element id ends with

    def detect(self, url: str, page: Page | None) -> bool:
        if _pid(url) is None:
            return False
        if page is None or HOST.match(urlsplit(url).hostname or ""):
            return True
        return page.locator("meta[name=_csrf]").count() > 0 and "_EF_GROUP_ID" in page.content()

    def posting(self, url: str) -> str:
        parts, pid = urlsplit(url), _pid(url)
        return f"https://{parts.hostname}/careers/job/{pid}" if pid else url

    def request_read(self, url: str) -> list[Field] | None:
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

    def browser_read(self, page: Page) -> list[Field]:
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
        doms = page.evaluate(_DOM_IDS, [[c.field.id, c.ends] for c in self._controls])
        for control in self._controls:
            control.dom = doms.get(control.field.id)
            if control.field.unfillable:
                continue
            if control.dom is None and not control.field.shown_when:
                reason = f"unhandled_rendering: {control.field.id} is not on the page"
                control.field = replace(control.field, unfillable=reason)
        held = self._values(page, [c for c in self._controls if c.dom and not c.field.unfillable])
        for control in self._controls:
            if control.field.id in held and control.field.prefilled is None:
                control.field = replace(control.field, default=held[control.field.id])
        self._fields = [c.field for c in self._controls]
        self._ends = {end: c.field.id for c in self._controls for end in c.ends}
        self._controls = [c for c in self._controls if not c.field.unfillable]
        return self._fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        # the resume first: what its parse finds is written over the fields filled before it
        order = sorted(self._controls, key=lambda c: not c.resume)
        given: dict[str, Answer] = {}
        for control in order:
            if (answer := answers.get(control.field.id)) is None:
                continue
            if control.dom is None:  # a follow-up, rendered once its condition holds
                if not conditions.Form(self._fields, given).shown(control.field):
                    continue
                with suppress(Timeout):
                    _box_of(page, control).wait_for(timeout=SHOWN_TIMEOUT_MS)
                control.dom = _find(page, control)
                if control.dom is None:
                    continue
            _fill(page, control, answer)
            given[control.field.id] = answer
        self._again(page, given)

    def _again(self, page: Page, given: dict[str, Answer]) -> None:
        """Fills once more an answered control a later render emptied."""
        placed = [c for c in self._controls if c.dom and given.get(c.field.id)]
        values = self._values(page, placed)
        for control in placed:
            if not values.get(control.field.id):
                _fill(page, control, given[control.field.id])

    def shown_fields(self, page: Page) -> list[str]:
        """The questions rendered once filled, each control's by the longest question id its
        element id ends with; one no question's id ends as the page names it."""
        shown: dict[str, None] = {}
        for dom in page.evaluate(_SHOWN):
            ends = [end for end in self._ends if dom == end or dom.endswith(f"_{end}")]
            shown[self._ends[max(ends, key=len)] if ends else dom] = None
        return list(shown)

    def read_back(self, page: Page) -> dict[str, Answer]:
        unplaced = [[c.field.id, c.ends] for c in self._controls if c.dom is None]
        doms = page.evaluate(_DOM_IDS, unplaced) if unplaced else {}
        for control in self._controls:
            control.dom = control.dom or doms.get(control.field.id)
        return self._values(page, [c for c in self._controls if c.dom])

    @staticmethod
    def _values(page: Page, controls: list[_Control]) -> dict[str, Answer]:
        """What each rendered control shows, a date in ISO; those showing nothing left out."""
        values = page.evaluate(_READ_BACK, [[c.field.id, c.dom, c.kind] for c in controls])
        out: dict[str, Answer] = {}
        for control in controls:
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


def _label(q: dict[str, Any]) -> str:
    label = _plain(q.get("label") or "")
    if (q.get("textOptions") or {}).get("type") == "number":  # a number input takes no text
        label += " (a number)"
    return label


def _shown_when(q: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> Condition | None:
    """What renders a follow-up: any of its parents' answers it lists, any answer where it
    lists none."""
    parents: list[Condition] = []
    for dependency in q.get("parentDependencies") or []:
        parent = by_id.get(str(dependency["questionId"]), {})
        labels = {str(c["value"]): c["label"] for c in _choices(parent)}
        values = [labels.get(str(v), str(v)) for v in dependency.get("values") or []]
        parents.append(Is(str(dependency["questionId"]), values or None))
    return parents[0] if len(parents) == 1 else AnyOf(parents) if parents else None


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
    qid, label, shown_when = str(q["questionId"]), _label(q), _shown_when(q, by_id)
    kind = KINDS.get(q.get("type", ""))
    choices = _choices(q)
    if kind == "select" and not choices and (q.get("selectOptions") or {}).get("apiEndpoint"):
        kind = "lookup"  # options load once the question it depends on is answered
    required = bool(q.get("required"))
    ends = list(dict.fromkeys([_norm(qid), qid]))
    if kind is None:
        reason = f"unhandled_rendering: {qid} is a question of type {q.get('type')}"
        unfillable = Field(qid, label, FieldType.TEXT, required, unfillable=reason)
        return _Control(replace(unfillable, shown_when=shown_when), "", ends)
    options = [c["label"] for c in choices] or None
    accept = None
    if kind == "file":
        files = q.get("resumeOptions") or q.get("fileUploadOptions") or {}
        types = files.get("acceptedFileTypes") or (files.get("fileFormats") or "").split(",")
        accept = [t.strip() for t in types if t.strip()] or None
    # today's date, read-only: the page takes no other, so it stands for the answer
    today = date.today().isoformat() if (q.get("dateOptions") or {}).get("today") else None
    question = Field(
        qid,
        label,
        TYPES[kind],
        required,
        options,
        accept=accept,
        prefilled=today,
        shown_when=shown_when,
    )
    return _Control(question, kind, ends, q.get("type") == "resumeUpload")


def _find(page: Page, control: _Control) -> str | None:
    return page.evaluate(_DOM_IDS, [[control.field.id, control.ends]]).get(control.field.id)


def _box_of(page: Page, control: _Control):
    ends = " , ".join(f'[id$="_{end}_error"]' for end in control.ends if '"' not in end)
    return page.locator(ends or "#none").first


def _box(page: Page, control: _Control):
    return page.locator(f"[id={json.dumps(f'{control.dom}_error')}]").locator("xpath=..")


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
    _choose(page, combo, option)


def _choose(page: Page, combo, option) -> None:
    """Clicks a combobox's option and waits for its list to close and the combobox to take the
    focus back: typed before that, the next answer lands in the combobox. A list still open over
    an unchanged combobox lost the click, so the option is clicked again; clicked once it took,
    it would clear. A list that stays open is dismissed, as it lies over the next control, for
    the read-back to catch what the combobox holds."""
    listbox = page.locator(f"[id={json.dumps(combo.get_attribute('aria-controls'))}]")
    held = combo.input_value()
    for _ in range(PICK_CLICKS):
        with suppress(Timeout):
            option.click(timeout=LIST_CLOSE_TIMEOUT_MS)
            listbox.wait_for(state="hidden", timeout=LIST_CLOSE_TIMEOUT_MS)
        if listbox.is_hidden():
            with suppress(Timeout):
                page.wait_for_function(
                    "el => document.activeElement === el",
                    arg=combo.element_handle(),
                    timeout=FOCUS_TIMEOUT_MS,
                )
            return
        if combo.input_value() != held:
            break
    combo.press("Escape")


def _phone(page: Page, box, value: str) -> None:
    """Picks the country code the number starts with, the longest that fits, then types the
    rest of the number. Where several countries share it, its main country. One already showing
    a code that fits stays: clicking the selected option clears it."""
    digits = re.sub(r"\D", "", value)
    combo = box.locator("input[role=combobox]").first
    number = box.locator("input:not([role=combobox])").first
    if not value.startswith("+"):
        number.fill(value)
        return
    if (held := DIAL.search(combo.input_value())) and digits.startswith(
        code := held[1].replace(" ", "")
    ):
        number.fill(digits[len(code) :])
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
    _choose(page, combo, options.nth(-i))
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


def _exact(value: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(value)}\s*$")
