"""Paycor Recruiting (`recruitingbypaycor.com/career/...`), as docs/adapters/paycor.md observes.

The apply page is server-rendered. Before the form it may show pages of their own: the
federal contractor's self-identification (VEVRAA) and the Yes/No qualifying questions. Each
posts its answers to the next, which carries them as hidden inputs; the form posts everything
to `ApplyJob.action` on Submit. `browser_read` walks those pages to the form, `fill` walks them
again with the answers.
"""

import json
import re
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from atsfill.core.adapter import Blocked, Gap, NotTaken, Unsupported
from atsfill.core.browser import Locator, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, Is, OptionSet, SideEffect

HOST = "recruitingbypaycor.com"
CAREER = f"https://{HOST}/career/"
FORM = "form#gnewtonForm[action*='ApplyJob.action']"
EEO_FORM = "form#gnewton-vevraa-form"
QUESTIONS = "div.gnewtonYes"
UPLOAD = "#uploadResumeInput"
SUBMIT = "#gnewtonForm button.gnewtonSubmitBtn"
YES_NO = ["Yes", "No"]
EEO = {"gender": "Gender", "race": "Ethnicity/Race", "veteran": "Protected veteran status"}
LABELS = {
    "ccpaDisclaimerAgree": "Privacy policy acknowledgement",
    "candidateTextMessagingOptIn": "Opt in to text messages",
}
KINDS = {
    "text": FieldType.TEXT,
    "number": FieldType.TEXT,
    "phone": FieldType.PHONE,
    "date": FieldType.DATE,
    "select": FieldType.CHOICE,
    "radio": FieldType.CHOICE,
    "checkbox": FieldType.MULTI_CHOICE,
    "file": FieldType.FILE,
    "upload": FieldType.FILE,
}
STEPS = 6  # pages before the form: the EEO introduction, its two pages, the questions
PAGE_TIMEOUT_MS = 30_000
CONTROL_TIMEOUT_MS = 10_000  # a radio stays disabled until the answer it hangs on is taken
UPLOAD_TIMEOUT_MS = 60_000
SUBMIT_TIMEOUT_MS = 90_000

# The form's controls in page order, a radio or a checkbox group once: what `fill` and
# `read_back` work on. A control under `display: none` and the honeypot are left out.
_CONTROLS = """() => {
  const form = document.querySelector('#gnewtonForm');
  const text = el => (el ? el.textContent : '').replace(/\\s+/g, ' ').trim();
  const hidden = el =>
    el.closest('[style*="display: none"], [style*="display:none"], .nt-aux-contact');
  const option = el => text(el.closest('label')?.querySelector('.radio-label'))
    || text(el.closest('label')) || text(el.parentElement);
  const out = [], seen = new Set();
  const upload = document.querySelector('#uploadResumeInput');
  if (upload) out.push({id: 'resume', label: 'Resume', kind: 'upload',
    required: upload.classList.contains('gnewtonRequired'), options: null, max: null});
  for (const el of form.querySelectorAll('input, select, textarea')) {
    const type = el.tagName === 'INPUT' ? el.type : el.tagName.toLowerCase();
    if (['hidden', 'submit', 'button'].includes(type) || !el.name || seen.has(el.name)) continue;
    if (hidden(el)) continue;
    seen.add(el.name);
    const group = [...form.querySelectorAll(`[name="${CSS.escape(el.name)}"]`)]
      .filter(e => e.type !== 'hidden' && !hidden(e));
    const box = el.closest('.gnewtonQuestionInputClass')?.parentElement;
    const own = el.id && !['radio', 'checkbox'].includes(type)
      && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    const label = text(own) || text(box?.querySelector('.gnewtonQuestionLabelClass label'))
      || el.title
      || text(el.closest('.gnewtonSectionClass')?.querySelector('.gnewtonSectionTitleClass'));
    let kind = type === 'textarea' ? 'text' : type;
    if (type === 'tel' || el.classList.contains('gnewtonPhone')) kind = 'phone';
    else if (el.name.endsWith('_answer_date')) kind = 'date';
    else if (['email', 'url'].includes(type)) kind = 'text';
    const options = type === 'select'
      ? [...el.options].filter(o => o.value).map(o => text(o))
      : ['radio', 'checkbox'].includes(type) ? group.map(option) : null;
    out.push({id: el.name, label, kind, options, max: el.maxLength > 0 ? el.maxLength : null,
      required: group.some(e => e.required || e.classList.contains('gnewtonRequired'))});
  }
  return out;
}"""
# The answer each control holds: a select's shown text, a checked radio's or boxes' labels.
_READ = """controls => {
  const form = document.querySelector('#gnewtonForm');
  const text = el => (el ? el.textContent : '').replace(/\\s+/g, ' ').trim();
  const option = el => text(el.closest('label')?.querySelector('.radio-label'))
    || text(el.closest('label')) || text(el.parentElement);
  const held = {};
  for (const [id, kind] of controls) {
    const els = [...form.querySelectorAll(`[name="${CSS.escape(id)}"]`)]
      .filter(e => e.type !== 'hidden');
    const el = els[0];
    let value = null;
    switch (kind) {
      case 'upload': value = form.querySelector('[name=ajaxResumeFilename]').value || null; break;
      case 'file': value = el && el.files.length ? el.files[0].name : null; break;
      case 'select': value = el.value ? text(el.selectedOptions[0]) : null; break;
      case 'radio': value = els.filter(e => e.checked).map(option)[0] || null; break;
      case 'checkbox': value = els.filter(e => e.checked).map(option); break;
      case 'date': {
        const m = el.value.match(/^(\\d{2})\\/(\\d{2})\\/(\\d{4})$/);
        value = el.type === 'date' ? el.value || null : m ? `${m[3]}-${m[1]}-${m[2]}` : null;
        break;
      }
      default: value = el.value || null;
    }
    held[id] = value;
  }
  return held;
}"""
# Clicks a radio or a box when its state differs: the styled mark can cover the input.
_CHECK = """(el, checked) => { if (el.checked !== checked) el.click(); }"""
# A hidden input of the form: an answer given on a page before it.
_HIDDEN = """name => {
  const el = document.querySelector(`#gnewtonForm input[type=hidden][name="${CSS.escape(name)}"]`);
  return el ? el.value : null;
}"""


# Each country select with its state select, and the page scripts' text, which holds every
# country's state list (`var ca_country_states = [...]`).
_COUNTRIES = """() => ({
  countries: [...document.querySelectorAll('#gnewtonForm select[data-state-id]')].map(c => ({
    name: c.name,
    state: document.getElementById(c.dataset.stateId)?.name,
    options: [...c.options].filter(o => o.value)
      .map(o => [o.value, o.textContent.replace(/\\s+/g, ' ').trim()]),
  })),
  scripts: [...document.scripts].map(s => s.textContent).join('\\n'),
})"""
STATE_LIST = re.compile(r"var (\w+)_country_states\s*=\s*(\[[^\]]*\])")


def _ids(url: str) -> tuple[str, str] | None:
    """The client and job ids of a Paycor career URL, or of an employer page opening it."""
    query = parse_qs(urlsplit(url).query)
    client, job = query.get("clientId", [""])[0], (query.get("id") or query.get("gni") or [""])[0]
    return (client, job) if client and job else None


def _field(control: dict[str, Any]) -> Field:
    """A form control as a field; a control of a type it does not know is listed unfillable."""
    fid, kind = control["id"], control["kind"]
    label = LABELS.get(fid, EEO.get(fid, control["label"]))
    if kind not in KINDS:
        reason = f"unhandled_rendering: {fid} is a {kind} input"
        return Field(fid, label, FieldType.TEXT, control["required"], unfillable=reason)
    label += " (a number)" if kind == "number" else ""
    return Field(fid, label, KINDS[kind], control["required"], control["options"], control["max"])


def _dependent(controls: list[tuple[Field, str]], page: dict[str, Any]) -> list[tuple[Field, str]]:
    """A state select under its country: the page's list for each country that has one, none
    for any other, where the page disables the state and drops its `required`. Unanswered, the
    country leaves the list the page starts with."""
    lists = {
        code: [s if isinstance(s, str) else s.get("value", "") for s in json.loads(names)]
        for code, names in STATE_LIST.findall(page["scripts"])
    }
    under = {c["state"]: c for c in page["countries"] if c["state"]}
    out = []
    for field, kind in controls:
        if (country := under.get(field.id)) and kind == "select":
            sets = [OptionSet(Is(country["name"], negated=True), field.options or [])]
            sets += [
                OptionSet(Is(country["name"], [text]), lists[code.lower()])
                for code, text in country["options"]
                if code.lower() in lists
            ]
            having = [text for code, text in country["options"] if code.lower() in lists]
            field = replace(
                field,
                required=False,
                required_when=Is(country["name"], having) if field.required else None,
                options=[],
                option_sets=sets,
            )
        out.append((field, kind))
    return out


class Paycor:
    name = "paycor"
    known_gaps: dict[str, Gap] = {}
    side_effects = [
        SideEffect("upload", "hiring_system", "the resume is stored and parsed by Paycor at once"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._start: str | None = None
        self._before: list[tuple[Field, str]] = []  # (field, page): a field before the form
        self._codes: dict[str, dict[str, str]] = {}  # an EEO page's option labels, by value
        self._controls: list[tuple[Field, str]] = []  # (field, kind) on the form

    def detect(self, url: str, page: Page | None) -> bool:
        """A `recruitingbypaycor.com/career` job URL, or an employer page that embeds one."""
        parts = urlsplit(url)
        if parts.hostname == HOST and (ids := _ids(url)):
            self._start = self._apply(*ids)
        elif page is not None:
            frame = page.locator(f'iframe[src*="{HOST}/career/"]').first
            src = frame.get_attribute("src") if frame.count() else None
            job = parse_qs(parts.query).get("gni", [None])[0]
            if src and (ids := _ids(src)):
                self._start = self._apply(ids[0], job or ids[1])
        return self._start is not None

    def posting(self, url: str) -> str:
        """The job id alone: it is unique across employers, and an employer's page names only it."""
        query = parse_qs(urlsplit(url).query)
        job = (query.get("id") or query.get("gni") or [url])[0]
        return f"{CAREER}JobIntroduction.action?id={job}"

    def browser_read(self, page: Page) -> list[Field]:
        self._before, self._codes = [], {}
        self._walk(page, None)
        controls = [(_field(c), c["kind"]) for c in page.evaluate(_CONTROLS)]
        controls = _dependent(controls, page.evaluate(_COUNTRIES))
        self._controls = [(f, kind) for f, kind in controls if not f.unfillable]
        return [f for f, _ in self._before] + [f for f, _ in controls]

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        if self._before:
            self._walk(page, answers)
        controls = sorted(self._controls, key=lambda c: c[1] != "upload")  # parsed, then answered
        for field, kind in controls:
            if (answer := answers.get(field.id)) is None:
                continue
            named = page.locator(f'#gnewtonForm [name="{field.id}"]:not([type=hidden])')
            match kind, answer:
                case "upload", str():
                    page.locator(UPLOAD).set_input_files(answer)
                    page.wait_for_function(
                        "() => document.querySelector('[name=ajaxResumeFilename]').value",
                        timeout=UPLOAD_TIMEOUT_MS,
                    )
                case "file", str():
                    named.set_input_files(answer)
                case "date", str() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", answer):
                    year, month, day = answer.split("-")
                    native = named.get_attribute("type") == "date"  # the page's HTML5 dates
                    named.fill(answer if native else f"{month}/{day}/{year}")
                    named.dispatch_event("change")
                case "text" | "number" | "phone", str():
                    named.fill(answer)
                    named.dispatch_event("change")
                case "select", str() if named.is_disabled():
                    raise NotTaken(f"{field.id} has no options for that country")
                case "select", str():
                    named.select_option(label=answer)
                case "radio", str():
                    self._until_enabled(page, named, field.id)
                    self._pick(named, field, [answer])
                case "checkbox", list():
                    self._pick(named, field, answer)
                case _:
                    raise Unsupported(
                        f"unhandled_rendering: cannot fill {field.id}, a {kind}, with {answer!r}"
                    )

    def _until_enabled(self, page: Page, radios: Locator, field_id: str) -> None:
        """Waits for a radio group the page enables on an earlier answer: the opt-in, on a phone
        it takes."""
        try:
            radios.first.wait_for(state="attached", timeout=CONTROL_TIMEOUT_MS)
            page.wait_for_function(
                "n => [...document.getElementsByName(n)].some(e => !e.disabled)",
                arg=field_id,
                timeout=CONTROL_TIMEOUT_MS,
            )
        except Timeout:
            detail = f"{field_id} stays disabled: the page refused an answer before it"
            raise NotTaken(detail) from None

    def shown_fields(self, page: Page) -> list[str]:
        """The form's controls; the answers to the pages before it are hidden inputs by now."""
        return [c["id"] for c in page.evaluate(_CONTROLS)]

    def read_back(self, page: Page) -> dict[str, Answer]:
        held: dict[str, Answer] = {}
        for field, step in self._before:
            value = page.evaluate(_HIDDEN, field.id.split(".", 1)[1])
            if step == "eeo":
                held[field.id] = self._codes[field.id].get(value or "", "")
            elif value:
                held[field.id] = "Yes" if value == "true" else "No"
        held |= page.evaluate(_READ, [[f.id, kind] for f, kind in self._controls])
        return {k: v for k, v in held.items() if v not in (None, [])}

    def submit(self, page: Page) -> str | None:
        with page.expect_response(
            lambda r: r.request.method == "POST" and "ApplyJob.action" in r.url,
            timeout=SUBMIT_TIMEOUT_MS,
        ) as submitted:
            page.locator(SUBMIT).click()
        response = submitted.value
        if not response.ok:
            raise Blocked(f"form_errors: {response.status}")
        page.wait_for_load_state()
        if page.locator(FORM).count():
            errors = page.locator(".gnewtonErrorMessage, .error").all_text_contents()
            raise Blocked(f"form_errors: {' '.join(errors).strip()[:300]}")
        return f"POST ApplyJob.action → {response.status} ({page.url})"

    @staticmethod
    def _apply(client: str, job: str) -> str:
        """The apply page in Paycor's own layout (`m=t`), which does not send the browser on
        to the employer's career page that frames it."""
        return f"{CAREER}SubmitResume.action?{urlencode({'clientId': client, 'id': job, 'm': 't'})}"

    @staticmethod
    def _pick(named: Any, field: Field, labels: list[str]) -> None:
        for n, option in enumerate(field.options or []):
            named.nth(n).evaluate(_CHECK, option in labels)

    def _walk(self, page: Page, answers: dict[str, Answer] | None) -> None:
        """Opens the apply page and answers the pages before the form: the Test user's picks
        when filling, the last option and Yes when only reading, which list the fields."""
        if self._start is None:
            raise RuntimeError("no Paycor job in the page")
        page.goto(self._start, wait_until="domcontentloaded")
        for _ in range(STEPS):
            page.wait_for_load_state("domcontentloaded")
            if page.locator(FORM).count():
                # The page's script fills the state and country lists once its DOM is in.
                page.wait_for_function(
                    "() => [...document.querySelectorAll('#gnewtonForm select')]"
                    ".every(s => s.options.length)",
                    timeout=PAGE_TIMEOUT_MS,
                )
                return
            if page.locator(EEO_FORM).count():
                self._eeo(page, answers)
                button = page.locator(f"{EEO_FORM} #gnewton-submit")
            elif page.locator(QUESTIONS).count():
                self._questions(page, answers)
                button = page.locator("#saveBtn")
            elif "no longer active" in (page.text_content("body") or ""):
                raise Unsupported("posting_gone: this job is no longer active")
            else:
                raise RuntimeError(f"an unknown page before the form ({page.url})")
            # The form's third-party scripts (Dropbox, Google) can hold its `load` back.
            with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS):
                button.click(no_wait_after=True)
        raise RuntimeError("no form after the pages before it")

    def _eeo(self, page: Page, answers: dict[str, Answer] | None) -> None:
        for name in page.locator(f"{EEO_FORM} input[type=radio]").evaluate_all(
            "es => [...new Set(es.map(e => e.name))]"
        ):
            radios = page.locator(f'{EEO_FORM} input[type=radio][name="{name}"]')
            codes = radios.evaluate_all(
                "es => es.map(e => [e.value, (e.closest('label')?.querySelector('.radio-label')"
                " || e.closest('label') || e.parentElement)"
                ".textContent.replace(/\\s+/g, ' ').trim()])"
            )
            fid = f"eeo.{name}"
            field = Field(fid, EEO.get(name, name), FieldType.CHOICE, True, [t for _, t in codes])
            if answers is None:
                self._before.append((field, "eeo"))
                self._codes[fid] = dict(codes)
            pick = answers.get(fid) if answers is not None else codes[-1][1]
            n = next((i for i, (_, t) in enumerate(codes) if t == pick), None)
            if n is None:
                raise Unsupported(f"missing_answer: {fid} comes before the form")
            radios.nth(n).evaluate(_CHECK, True)

    def _questions(self, page: Page, answers: dict[str, Answer] | None) -> None:
        for question in page.locator(".gnewtonQuestionWrapper").all():
            qid = (question.locator(QUESTIONS).get_attribute("id") or "")[2:]
            fid = f"question.{qid}"
            label = (question.locator(".gnewtonQuestion").text_content() or "").strip()
            if answers is None:
                self._before.append((Field(fid, label, FieldType.CHOICE, True, YES_NO), "question"))
            pick = answers.get(fid) if answers is not None else "Yes"
            if pick not in YES_NO:
                raise Unsupported(f"missing_answer: {fid} comes before the form")
            question.locator(f"#{'y' if pick == 'Yes' else 'n'}_{qid}").click()
