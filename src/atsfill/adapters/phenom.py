"""Phenom's apply flow on an employer's careers site (`<site>/<country>/<lang>/job/<jobSeqNo>`),
as docs/adapters/phenom.md observes.

The job page's Apply link leads to Phenom's own wizard (`/apply?jobSeqNo=`) or to another
system. The wizard renders one step at a time from the employer's configuration, a form schema
on Phenom's CDN, and the parts its loaders serve (the job's questionnaires, the country's
fields, dependent lists): reading interprets them, over HTTP or from the page, without passing a
step. Filling walks the wizard on a fresh session, each step's answers read back before Next.
"""

import http.cookiejar
import json
import re
import time
import urllib.request
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import suppress
from html import unescape
from pathlib import PurePath
from typing import Any
from urllib.parse import parse_qs, urlsplit

from atsfill.core import browser, matching
from atsfill.core.adapter import Gap, NotTaken, Unsupported, known_gap
from atsfill.core.browser import HTTP_TIMEOUT_S, BrowserError, Locator, Overlaid, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

JOB = re.compile(r"^(/[a-z]{2,8}/[a-z]{2})/(job|\w*apply)(?:/|$)")
APPLY = re.compile(r"^/[a-z]{2,8}/[a-z]{2}/\w*apply$")  # `apply`, `hvhapply` for hourly roles
SEQ = re.compile(r"^/[a-z]{2,8}/[a-z]{2}/job/([A-Za-z0-9]+)")
CDN = "https://cdn-prod-static.phenompeople.com/CareerConnectResources"
FORM = "form.rjsf"
NEXT = "button#next"
SUBMIT = "button#submit, button.btn-submit, button[type=submit].btn-navigate:not(#next)"
RESUME = ".resume-upload-wrapper input[type=file]"
RESUME_ID = "resume"
MARKUP = "[data-ph-at-id], [phw-tk], [ph-module], form.rjsf"  # a Phenom page's own
LOADING = ".overlaybg"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0"
FORM_TIMEOUT_MS = 30_000
STEP_TIMEOUT_MS = 30_000
FIELD_TIMEOUT_MS = 5_000
UPLOAD_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
SETTLE_S = 1.0
QUIET_S = 2.0  # no request in flight this long: a parsed resume is back
UPLOAD_POLL_S = 0.25
LINK_TIMEOUT_S = 15
FILL_ROUNDS = 6  # an answer shows a follow-up, which can show another
PICK_TRIES = 3
MAX_STEPS = 12
OPTIONS_TIMEOUT_S = 8
YES_NO = ["Yes", "No"]
NO_YES = ["No", "Yes"]
MONTH = " (MM/YYYY)"
YEAR = " (year)"
PICKER = ".react-datepicker"
MONTHS: list[str] = [
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"
]  # fmt: skip
MAX_MONTH_STEPS = 60
OPTIONS = "el => [...el.options].filter(o => o.value && o.text.trim()).map(o => o.text.trim())"
TYPES = {
    "text": FieldType.TEXT,
    "textarea": FieldType.TEXT,
    "phone": FieldType.PHONE,
    "select": FieldType.CHOICE,
    "typeahead": FieldType.CHOICE,
    "search": FieldType.TYPEAHEAD,
    "radio": FieldType.CHOICE,
    "checkbox": FieldType.CHOICE,
    "checkboxes": FieldType.MULTI_CHOICE,
    "date": FieldType.DATE,
    "month": FieldType.TEXT,
    "year": FieldType.TEXT,
}
# The schema's widgets (`htmlType`) by what the page shows for them.
NOT_ASKED = {
    "hidden", "markdown", "label", "summary", "heading", "file", "dropbox", "onedrive",
    "skydrive", "additionalattachment", "additionalattachment_v2",
}  # fmt: skip
LISTS = {
    "choice", "choiceKeyValue", "choiceKeyValueV2", "country", "asyncselect", "sourceTypeSchema",
    "dependent_utm", "single_utm",
}  # fmt: skip
WIDGETS = {"asynctypeaheadv3", "typeahead"}  # an array of picks, not entries
LOADED = {"asyncfield", "dynamicschema"}  # a part of the form its loader serves
SOURCE_TREE = {"dependent_utm", "single_utm"}  # sources under each "how you heard" answer
TEMPLATE = re.compile(r"\{\{\s*(.*?)\s*\}\}")
TAG = re.compile(r"<[^>]+>")
PAGE_VALUE = re.compile(r"%%(phApp\.[\w.]+)%%")
ANSWER = re.compile(r"%%formData\.(\w+)%%")
SEARCHING = re.compile(r"Searching", re.IGNORECASE)  # a typeahead's menu before its results
ACCEPTED = re.compile(r"\b(DOCX?|PDF|TXT|HTML?|RTF)\b", re.IGNORECASE)

type Post = Callable[[dict[str, Any]], Any]

# Each field the current step shows: its control's kind, id, label and requirement. A field is
# a `.form-group.field` with no field inside it; a checkbox group is named by its boxes' id.
_SCAN = """form => {
  const shown = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const text = el => (el?.innerText || '').replace(/\\s+/g, ' ')
    .replace(/^\\s*\\*\\s*|\\s*\\*\\s*$/g, '').trim();
  const out = [];
  for (const group of form.querySelectorAll('.form-group.field')) {
    if (group.querySelector('.form-group.field') || !shown(group)) continue;
    const controls = [...group.querySelectorAll('input, select, textarea')].filter(el =>
      el.type !== 'hidden' && el.type !== 'file' && !el.classList.contains('rbt-input-hint')
      && (shown(el) || el.type === 'checkbox' || el.type === 'radio'));
    if (!controls.length) continue;
    const first = controls[0];
    let kind = 'text';
    if (first.tagName === 'SELECT') kind = 'select';
    else if (first.tagName === 'TEXTAREA') kind = 'textarea';
    else if (first.dataset.attribute === 'typeAHead' || first.getAttribute('role') === 'combobox')
      kind = 'typeahead';
    else if ((first.type === 'tel' || /^tel/.test(first.autocomplete) || /phone/i.test(first.id))
      && !/extension/i.test(first.id)) kind = 'phone';
    else if (first.type === 'radio') kind = 'radio';
    else if (first.type === 'checkbox') kind = controls.length > 1 ? 'checkboxes' : 'checkbox';
    else if (first.type === 'date' || group.querySelector('.react-datepicker-wrapper'))
      kind = 'date';
    let id = kind === 'radio' ? (first.name || first.id) : (first.id || first.name);
    if (kind === 'checkboxes') id = id.replace(/_\\d+$/, '');
    const format = kind !== 'date' ? null
      : first.type === 'date' ? 'yyyy-mm-dd' : (first.placeholder || 'MM/DD/YYYY');
    const national = first.autocomplete === 'tel-national';
    if (id) out.push({id, dom: first.id || first.name, kind, format, national,
      label: text(group.querySelector('label, legend'))});
  }
  return out;
}"""

# The dial code the step's country code control holds, as digits: `(+1)` in its text or
# `USA_1` as its value.
_DIAL = """form => {
  const el = form.querySelector('[autocomplete=tel-country-code], [id*=dialing i]');
  if (!el) return null;
  const text = el.tagName === 'SELECT' ? (el.selectedOptions[0]?.text || '') : el.value;
  return (text.match(/\\(\\+(\\d+)/) || (el.value || '').match(/_(\\d+)$/) || [])[1] || null;
}"""

# Picks a dropdown's option by its text, as React sees a change, even to the value it holds.
_SELECT = """(el, text) => {
  const option = [...el.options].find(o => o.text.trim() === text);
  if (!option) return false;
  el.focus();
  if (el.value === option.value) {  // set again, so that a list depending on it loads
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(el, "");
    el.dispatchEvent(new Event("change", {bubbles: true}));
  }
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(el, option.value);
  el.dispatchEvent(new Event("input", {bubbles: true}));
  el.dispatchEvent(new Event("change", {bubbles: true}));
  el.blur();
  return true;
}"""

# Clicks the radio of the group `name` whose label is `text`.
_RADIO = """(form, [name, text]) => {
  const label = el =>
    ((el.closest('label') || form.querySelector(`label[for="${CSS.escape(el.id)}"]`))
      ?.innerText.replace(/\\s+/g, ' ').trim()) || el.getAttribute('aria-label');
  const radio = [...form.querySelectorAll(`input[type=radio][name="${CSS.escape(name)}"]`)]
    .find(el => label(el) === text);
  radio?.click();
  return !!radio;
}"""

# What one field shows.
_READ = """(form, f) => {
  const text = el => (el?.innerText || '').replace(/\\s+/g, ' ').trim();
  const labelOf = el =>
    text(el.closest('label') || form.querySelector(`label[for="${CSS.escape(el.id)}"]`))
    || el.getAttribute('aria-label');
  if (f.kind === 'radio') {
    const checked = form.querySelector(`input[type=radio][name="${CSS.escape(f.id)}"]:checked`);
    return checked ? labelOf(checked) : null;
  }
  const byId = form.querySelector(`[id="${CSS.escape(f.dom)}"]`);
  if (!byId) return null;
  if (f.kind === 'select') return byId.value ? byId.selectedOptions[0].text.trim() : null;
  if (f.kind === 'checkbox') return byId.checked ? 'Yes' : 'No';
  if (f.kind === 'checkboxes')
    return [...byId.closest('.form-group').querySelectorAll('input[type=checkbox]:checked')]
      .map(labelOf);
  return byId.value;
}"""

# The job page's Apply link for the job `seq`: its href, once the page has rendered it.
_APPLY_LINK = """seq => {
  const links = [...document.querySelectorAll('a[href]')].filter(a =>
    (/apply/i.test(`${a.dataset.phAtId} ${a.getAttribute('phw-tk')} ${a.getAttribute('ph-tevent')}`)
      && (a.offsetWidth || a.offsetHeight)) || a.href.includes(`jobSeqNo=${seq}`));
  const own = links.find(a => a.href.includes(seq)) || links[0];
  return own ? own.href : null;
}"""

# The errors a step shows, each with its field's control id.
_ERRORS = """form => [...form.querySelectorAll('.error-detail li, .text-danger, .error-message')]
  .filter(e => e.innerText.trim())
  .map(e => {
    const control = e.closest('.form-group')?.querySelector('input, select, textarea');
    return [control?.id || control?.name || '?', e.innerText.trim()];
  }).slice(0, 4)"""


# Whether an element the page shows holds `text`: the uploaded file's name.
_SHOWS = """text => [...document.querySelectorAll('body *')].some(el =>
  !el.children.length && el.textContent.includes(text)
  && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length))"""

# A request from the page, as its own scripts make them: the CDN's configuration, or a post to
# `/applySubmit` with the session's token.
_FETCH = """async ([url, body, token]) => {
  const init = body ? {method: 'POST', body: JSON.stringify(body),
    headers: {'Content-Type': 'application/json', 'x-csrf-token': token}} : {};
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(`${response.status} from ${url}`);
  return response.json();
}"""


class Phenom:
    name = "phenom"
    known_gaps = {
        "first_step": Gap.FLOW,
        "long_wizard": Gap.FLOW,
        "calendar_without_month": Gap.RENDERING,
    }
    side_effects = [
        SideEffect(
            "upload", "hiring_system", "the resume is uploaded to Phenom and parsed at once"
        ),
        SideEffect(
            "fill",
            "hiring_system",
            "each step is submitted to Phenom on Next, which keeps a candidate profile from the"
            " first step",
        ),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._apply = ""
        self._raws: dict[str, dict[str, Any]] = {}  # each field as the configuration has it
        self._kept: dict[str, Answer] = {}

    def detect(self, url: str, page: Page | None) -> bool:
        if JOB.match(urlsplit(url).path) is None:
            return False
        return page is None or page.locator(MARKUP).count() > 0

    def posting(self, url: str) -> str:
        parts = urlsplit(url)
        seq = _seq(url)
        return f"https://{parts.hostname}/apply?jobSeqNo={seq}" if seq else url

    def read_api(self, url: str) -> list[Field] | None:
        """The fields from the job's apply page and the configuration it names, over HTTP;
        None for a job applied to on another system, or a page it does not understand."""
        parts = urlsplit(url)
        prefix, seq = JOB.match(parts.path), _seq(url)
        if not prefix or not seq:
            return None
        origin = f"https://{parts.hostname}"
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

        def load(url: str, body: dict[str, Any] | None = None, token: str = "") -> bytes:
            headers = {"User-Agent": UA}  # Phenom's sites turn away Python-urllib
            if body is not None:
                headers |= {"Content-Type": "application/json", "x-csrf-token": token}
            data = None if body is None else json.dumps(body).encode()
            request = urllib.request.Request(url, data, headers)
            with opener.open(request, timeout=HTTP_TIMEOUT_S) as response:
                return response.read()

        try:
            if prefix[2] == "job":
                job = _phapp_of(load(url).decode())
                if get(job, "ddo.jobDetail.data.job.externalApply") is True:
                    return None
            ph = _phapp_of(load(f"{origin}{prefix[1]}/apply?jobSeqNo={seq}").decode())
            token = get(ph, "sessionParams.csrfToken") or ""
            fields, _ = _read(
                ph,
                lambda url: json.loads(load(url)),
                lambda body: json.loads(load(f"{origin}/applySubmit", body, token)),
            )
            return fields
        except OSError, ValueError, KeyError, TypeError, RuntimeError, Unsupported:
            return None

    def read_form(self, page: Page) -> list[Field]:
        _check_open(page)
        self._apply = _apply_url(page)
        self._open(page)
        try:
            ph = _phapp_of(page.content())  # the page's scripts' own world is out of reach
        except ValueError as e:
            raise RuntimeError(str(e)) from None
        token = get(ph, "sessionParams.csrfToken") or ""
        try:
            fields, raws = _read(
                ph,
                lambda url: page.evaluate(_FETCH, [url, None, token]),
                lambda body: page.evaluate(_FETCH, ["/applySubmit", body, token]),
            )
        except (BrowserError, ValueError, KeyError) as e:
            raise RuntimeError(f"no configuration for the wizard ({e})") from None
        self._raws = {raw["id"]: raw for raw in raws}
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        page.context.clear_cookies()
        self._open(page, fresh=True)
        self._kept = {}
        if RESUME_ID in answers and page.locator(RESUME).count():
            _upload(page, _one(answers[RESUME_ID]))
            self._kept[RESUME_ID] = PurePath(_one(answers[RESUME_ID])).name
        for number in range(1, MAX_STEPS + 1):
            kept: dict[str, Answer] = {}
            for _ in range(FILL_ROUNDS):  # an answer can show a field, or reset one
                step = self._step(page, answers)
                for raw in [raw for raw in step if not self._holds(raw, answers, kept)]:
                    _fill(page, raw, answers[raw["id"]])
                time.sleep(2 * SETTLE_S)  # a reset comes a moment after the answer
                again = self._step(page, answers)
                kept = _read_step(page, again)
                if [r["id"] for r in again] == [r["id"] for r in step] and all(
                    self._holds(raw, answers, kept) for raw in again
                ):
                    break
            self._kept |= kept
            if not _has_next(page):
                return
            _next(page, number)
        raise Unsupported(known_gap(self, "long_wizard", f"more than {MAX_STEPS} steps"))

    def read_back(self, page: Page) -> dict[str, Answer]:
        return self._kept

    def submit(self, page: Page) -> str | None:
        """Clicks the last step's submit; the evidence is Phenom's answer to it."""
        with page.expect_response(
            lambda r: (
                r.request.method == "POST"
                and "/applySubmit" in r.url
                and '"isLastStep":true' in (r.request.post_data or "")
            ),
            timeout=SUBMIT_TIMEOUT_MS,
        ) as applied:
            browser.click(page.locator(FORM).locator(SUBMIT).first)
        response = applied.value
        if not response.ok or '"error"' in response.text()[:200]:
            raise RuntimeError(f"apply answered {response.status}: {response.text()[:200]}")
        return f"{response.url} {response.status}"

    def _open(self, page: Page, fresh: bool = False) -> None:
        if fresh:  # the same origin: its storage holds a reading's application
            page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
        if fresh or not page.url.startswith(self._apply):
            page.goto(self._apply, wait_until="domcontentloaded")
        _check_open(page)
        try:
            page.locator(f"{FORM} {NEXT}, {FORM} button[type=submit]").first.wait_for(
                state="attached", timeout=FORM_TIMEOUT_MS
            )
        except Timeout:
            query = parse_qs(urlsplit(page.url).query)
            if step := (query.get("stepname") or query.get("page") or [None])[0]:
                gap = known_gap(self, "first_step", f"the wizard opens on {step!r}")
                raise Unsupported(gap) from None
            raise RuntimeError("no application step on the page") from None

    def _step(self, page: Page, answers: dict[str, Answer]) -> list[dict[str, Any]]:
        """The answered fields the current step shows, in the configuration's order."""
        shown = {r["id"]: r for r in page.locator(FORM).evaluate(_SCAN)}
        return [self._shown_as(i, shown[i]) for i in self._raws if i in shown and i in answers]

    def _shown_as(self, field_id: str, shown: dict[str, Any]) -> dict[str, Any]:
        """A shown control as filling takes it: the page's rendering, the configuration's
        picker (a day, a month, a year) or search, and a dependent list's parent."""
        raw = self._raws[field_id]
        kind = shown["kind"]
        if kind == "date" and raw["kind"] in ("month", "year"):
            kind = raw["kind"]
        elif raw["kind"] == "search":  # its input is not marked as one
            kind = "typeahead"
        return {**shown, "kind": kind, "parent": raw.get("parent")}

    def _holds(self, raw: dict[str, Any], answers: dict[str, Answer], kept: dict) -> bool:
        field = _field(self._raws[raw["id"]])
        return matching.matches(field, answers[raw["id"]], kept.get(raw["id"]))


def get(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def _phapp_of(html: str) -> dict[str, Any]:
    """The apply page's `phApp`, from the object literals its scripts assign."""
    ph = _literal(html, "var phApp = phApp || ")
    ph["ddo"] = _literal(html, "phApp.ddo = ")
    ph["sessionParams"] = _literal(html, "phApp.sessionParams = ")
    return ph


def _literal(html: str, marker: str) -> Any:
    at = html.find(marker)
    if at < 0:
        raise ValueError(f"no {marker.strip()} on the page")
    return json.JSONDecoder().raw_decode(html, at + len(marker))[0]


def _check_open(page: Page) -> None:
    """Raises `posting_gone` on Phenom's page for a closed posting, which a closed job's URL
    and its wizard's both serve."""
    with suppress(ValueError):
        if _literal(page.content(), "var phApp = phApp || ").get("pageName") == "404":
            raise Unsupported("posting_gone: the careers site answers its 404 page")


def _read(
    ph: dict[str, Any], fetch: Callable[[str], Any], post: Post
) -> tuple[list[Field], list[dict[str, Any]]]:
    """The fields of every step, from the configuration the page names, and their raw form."""
    ats = (get(ph, "ddo.response.response.applyATS") or "").lower()
    base = f"{CDN}/{ph['refNum']}/{ats}/external/careersite/singlepage"
    config = fetch(f"{base}/schema/form_schema.json")
    texts: dict[str, str] = {}
    for bundle in ("default", ph.get("locale") or "en_us"):
        with suppress(Exception):  # a site has one bundle or both
            texts |= fetch(f"{base}/bundles/Messages_{bundle}.json")
    reader = _Config(ph, config, texts, post)
    raws = [raw for step in reader.steps() for raw in step]
    if not raws:
        raise RuntimeError("the wizard shows no fields")
    resume = reader.resume()
    return ([resume] if resume else []) + [_field(raw) for raw in raws], raws


class _Config:
    """The wizard's steps, each its fields as raw dicts, from the employer's configuration:
    a react-jsonschema-form schema and uiSchema with Phenom's widgets, rules on the job's data,
    and loaders that serve further parts on `/applySubmit`."""

    def __init__(
        self, ph: dict[str, Any], config: dict[str, Any], texts: dict[str, str], post: Post
    ) -> None:
        self.ph, self.config, self.texts, self.post = ph, config, texts, post
        self.ddo = get(ph, "ddo.response.response") or {}
        self.listed: dict[str, tuple[list[Any], list[str]]] = {}  # a list's values and texts
        self.labels: dict[str, str] = {}
        self.without_resume = False  # a required field is shown only without a resume
        self._dependents: dict[str, tuple[str, dict]] | None = None

    def text(self, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        for _ in range(3):  # a message can name another
            value = TEMPLATE.sub(lambda m: str(self.texts.get(m[1], "")), value)
        return re.sub(r"\s+", " ", unescape(TAG.sub(" ", value))).strip()

    def steps(self) -> list[list[dict[str, Any]]]:
        """The fields of each step the job shows, in order."""
        names = list(self.config.get("stepConfig") or {}) or [""]
        skipped = {
            name
            for name, step in (self.config.get("stepRules") or {}).items()
            if _rules_hold(self.ph, step.get("rules"))
        }
        aliases: dict[str, str] = {}  # a field names its step by key, id or title
        for name, step in (self.config.get("stepConfig") or {}).items():
            for alias in (name, step.get("stepId"), step.get("name"), self.text(name)):
                if alias:
                    aliases.setdefault(alias, name)
        by_step: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
        schema, ui = self.config["schema"], self.config["uiSchema"]
        removed = {  # a field a common rule removes
            get(r, "event.params.field")
            for r in self.config.get("commonRules") or []
            if get(r, "event.type") == "removeField"
        }
        for raw in self.object(schema, ui, "", None, False, None):
            if raw["id"] in removed:
                continue
            nav = raw.pop("nav") or ""
            by_step[aliases.get(nav) or aliases.get(self.text(nav)) or names[0]].append(raw)
        return [by_step[name] for name in names if name not in skipped]

    def resume(self) -> Field | None:
        """The resume upload: required where its text is marked so or a rule makes it so, and
        where the form would otherwise ask for required entries this adapter does not read."""
        cloud = self.config.get("cloudWidget") or {}
        options = cloud.get("commonCloudOptions") or {}
        if options.get("enableFileUpload") is False:
            return None
        info = self.text(options.get("resumeInfoText") or cloud.get("resumeInfoText"))
        rules = [r.get("event", {}).get("type") for r in self.config.get("commonRules") or [] if r]
        required = "*" in info or "resumeMandatory" in rules or self.without_resume
        types = self.text(options.get("suppResFileType")) + " " + info
        accept = sorted({f".{t.lower()}" for t in ACCEPTED.findall(types)}) or None
        return Field(RESUME_ID, "Resume", FieldType.FILE, required, accept=accept)

    def object(
        self, schema: dict, ui: dict, path: str, nav: str | None, required: bool, when: Any
    ) -> list[dict[str, Any]]:
        """An object's fields: its properties in the uiSchema's order, with those an `allOf`
        rule adds once an answer holds. A top-level property names its step (`nav`)."""
        props = dict(schema.get("properties") or {})
        needed = set(schema.get("required") or [])
        conditions: dict[str, Any] = {}
        for rule in schema.get("allOf") or []:
            then = rule.get("then") or {}
            holds = self.condition(rule.get("if") or {}, path, props)
            holds = True if holds == "given" else holds  # a resume is given where asked
            if holds is False:
                continue
            for name, prop in (then.get("properties") or {}).items():
                props.setdefault(name, prop)
                if holds is True:
                    needed |= {name} & set(then.get("required") or [])
                else:
                    conditions[name] = holds
        for name, u in ui.items():  # a widget only the uiSchema describes
            if isinstance(u, dict) and u.get("htmlType") in SOURCE_TREE:
                props.setdefault(name, {"type": "string"})
        order = [n for n in ui.get("ui:order") or ["*"] if n == "*" or n in props]
        at = order.index("*") if "*" in order else len(order)
        order[at : at + 1] = [n for n in props if n not in order]
        out = []
        for name in order:
            if not isinstance(props[name], dict):
                continue
            u = ui[name] if isinstance(ui.get(name), dict) else {}
            out += self.property(
                props[name], u, f"{path}{name}", nav or u.get("nav"),
                required or name in needed, conditions.get(name, when), ui,
            )  # fmt: skip
        return out

    def property(
        self, p: dict, u: dict, path: str, nav: Any, required: bool, when: Any, siblings: dict
    ) -> list[dict[str, Any]]:
        html, widget = u.get("htmlType"), u.get("ui:widget") or u.get("ui:field")
        if html in NOT_ASKED or widget in ("hidden", "markdown") or p.get("type") == "null":
            return []
        if when == "resume":
            if any(raw["required"] for raw in self.property(p, u, path, nav, required, None, {})):
                self.without_resume = True
            return []
        required = (required or u.get("ui:required") is True) and when is None
        if html in LOADED:
            return self.loaded(u, nav) or self.job_questions(path, nav)
        if html == "daterangepicker" or widget == "daterangepicker":
            return self.date_range(p, u, path, nav, when)
        if p.get("type") == "array" and get(p, "items.properties") and html not in WIDGETS:
            if not (int(p.get("minItems") or 0) or int(u.get("minItemsRequired") or 0)):
                return []  # entries only once Add is clicked
            return self.object(p["items"], u.get("items") or {}, f"{path}[0].", nav, False, when)
        if p.get("type") == "object" or html in ("object", "phoneNumber_v2"):
            return self.object(p, u, f"{path}.", nav, False, when)
        kind = _kind(p, html, widget, path)
        raw: dict[str, Any] = {
            "id": path,
            "kind": kind,
            "label": self.text(p.get("title")) or self.text(u.get("ui:placeholder")) or path,
            "required": required,
            "options": None,
            "nav": nav,
            "prefilled": p.get("defaultTodayDate") is True,  # a signature's date, today
            "max_length": p.get("maxLength") if isinstance(p.get("maxLength"), int) else None,
        }
        self.labels[path] = raw["label"]
        if kind in ("select", "typeahead", "radio", "checkboxes"):
            raw["options"] = self.options(p, u, html, kind, raw, siblings)
        if kind == "search" and (api := get(u, "ui:options.apiConfig")) and api.get("payload"):
            listing = {  # suggestions before any is typed: some of what it takes
                "datasourcepath": get(u, "ui:options.datasourcepath"),
                "displayTextField": get(u, "ui:options.dataFieldMapKey") or "label",
            }
            raw["options"] = _unique(t for _, t in self.loaded_list(listing, api)) or None
        if kind == "select" and re.fullmatch(r"\w+", path):  # a default, the job's country
            raw["prefilled"] = self.prefilled(path)[0] is not None
        if kind in ("date", "month", "year"):
            raw["format"] = (u.get("ui:placeholder") if p.get("format") == "date" else None) or (
                p.get("format") or "MM/dd/yyyy"
            )
        if when:
            raw["label"] += self.asked_when(when)
        out = [raw]
        if html == "country":
            out += self.country_fields(p, u, path, nav)
        return out

    def options(
        self, p: dict, u: dict, html: Any, kind: str, raw: dict, siblings: dict
    ) -> list[str] | None:
        path = raw["id"]
        if html in SOURCE_TREE:
            raw["parent"] = next(
                (name for name, v in siblings.items()
                 if isinstance(v, dict) and v.get("htmlType") == "sourceTypeSchema"),
                None,
            )  # fmt: skip
            return _unique(
                self.text(child.get("key"))
                for parent in u.get("data") or []
                for child in parent.get("child") or []
                if child.get("show") is not False
            )
        if html == "sourceTypeSchema":
            tree = next(
                (v.get("data") for v in siblings.values()
                 if isinstance(v, dict) and v.get("htmlType") == "dependent_utm"),
                None,
            )  # fmt: skip
            return _unique(self.text(parent.get("title")) for parent in tree or [])
        if kind == "radio" and not p.get("enum"):
            return YES_NO
        api = u.get("apiConfig") or get(u, "ui:options.apiConfig")
        if dependent := self.dependents().get(path.rsplit(".", 1)[-1]):
            pairs = self.dependent_list(raw, *dependent)
        elif (
            html == "asyncselect" and api and (parents := ANSWER.findall(api.get("payload") or ""))
        ):
            pairs = self.dependent_list(raw, u.get("dependentKey") or parents[0], api)
        elif html == "asyncselect" and api and api.get("payload"):
            pairs = self.loaded_list(u, api)
        elif table := self.source_table(path.rsplit(".", 1)[-1]):
            pairs = table
        else:
            values = p.get("enum") or get(p, "items.enum") or []
            names = p.get("enumNames") or get(p, "items.enumNames") or values
            pairs = [
                (v, self.text(n) if isinstance(n, str) else str(n))
                for v, n in zip(values, names, strict=False)
            ]
        pairs = [(v, t) for v, t in pairs if t]
        listed = ([v for v, _ in pairs], [t for _, t in pairs])
        self.listed[path] = listed
        self.listed.setdefault(path.rsplit(".", 1)[-1], listed)  # as a workflow names it
        return _unique(t for _, t in pairs) or None

    def loaded_list(self, u: dict, api: dict) -> list[tuple[Any, str]]:
        """A list its own loader serves, by the uiSchema's names for the response's parts."""
        data = get(self.call(api["payload"]), u.get("datasourcepath") or "applyGetReferences.data")
        if isinstance(data, dict):
            data = data.get(u.get("responseField") or "values") or data.get("values")
        shown, value = u.get("displayTextField") or "label", u.get("valueField") or "key"
        return [
            (d.get(value), self.text(d.get(shown) or d.get("label") or d.get("value")))
            for d in data or []
            if isinstance(d, dict)
        ]

    def dependents(self) -> dict[str, tuple[str, dict]]:
        """The lists a workflow loads when another answer changes: each one's parent and
        request, from a workflow's request on `%%formData.<parent>%%` whose result its
        transform writes to the list's schema."""
        if self._dependents is not None:
            return self._dependents
        self._dependents = {}
        for flow in self.config.get("workflow") or []:
            if "onchange" not in (flow.get("events") or []):
                continue
            nodes = flow.get("workflows") or []
            requests = [get(n, "data.config") for n in nodes if n.get("type") == "http"]
            for target in _targets(nodes):
                for request in requests:
                    if isinstance(request, dict) and ANSWER.search(request.get("payload") or ""):
                        self._dependents.setdefault(target, ("", request))
        for target, (_, request) in self._dependents.items():  # a list under two, the nearer
            parents = ANSWER.findall(request.get("payload") or "")
            nearer = [p for p in parents if p in self._dependents]
            self._dependents[target] = ((nearer or parents)[0], request)
        return self._dependents

    def dependent_list(self, raw: dict, parent: str, request: dict) -> list[tuple[Any, str]]:
        """A dependent list's options under its parent's answer before any: the parent's
        default, else the job's country where the parent lists it, else its first option. The
        label names that answer."""
        scope = raw["id"].rpartition(".")[0]  # a parent in the same entry, else at the top
        at = f"{scope}.{parent}" if f"{scope}.{parent}" in self.labels else parent
        value, shown = self.prefilled(parent)
        values, texts = self.listed.get(at) or self.listed.get(parent) or ([], [])
        country = re.sub(r"\W", "", self.ddo.get("jobCountry") or "").lower()
        for v, text in zip(values, texts, strict=False):  # a country list: the job's country
            name = re.sub(r"\W", "", text).lower()
            if (
                value is None
                and name
                and country
                and (country.startswith(name) or name.startswith(country))
            ):
                value, shown = v, text
        if value is None and values:
            value, shown = values[0], texts[0]
        if value is None:
            return []
        payload = request.get("payload") or ""
        for name in ANSWER.findall(payload):
            held = value if name == parent else self.prefilled(name)[0]
            payload = payload.replace(f"%%formData.{name}%%", str(held or ""))
        data = get(self.call(payload), "applyGetReferences.data")
        if isinstance(data, dict):
            data = data.get("values")
        pairs = [
            (d.get("key") or d.get("value"), self.text(d.get("label") or d.get("value")))
            for d in data or []
            if isinstance(d, dict)
        ]
        if pairs:
            raw["label"] += f" (options for {self.labels.get(at, parent)!r} {shown!r})"
            raw["parent"] = at
        return pairs

    def source_table(self, name: str) -> list[tuple[Any, str]]:
        """A list Phenom's applicant-source workflow fills from its own table: the sources,
        or the sub-sources under every source, in order."""
        for flow in self.config.get("workflow") or []:
            nodes = flow.get("workflows") or []
            if name not in _targets(nodes):
                continue
            for node in nodes:
                for table in (
                    get(node, "data.config") or [] if node.get("type") == "temp_data" else []
                ):
                    data = table.get("data") if table.get("key") == "jsonData" else None
                    rows = (
                        data
                        if isinstance(data, list)
                        else [
                            row
                            for rows in (data or {}).values()
                            if isinstance(rows, list)
                            for row in rows
                        ]
                    )
                    pairs = [
                        (r.get("value"), self.text(r.get("display")))
                        for r in rows
                        if isinstance(r, dict)
                    ]
                    if pairs:
                        return pairs
        return []

    def prefilled(self, name: str) -> tuple[Any, str]:
        """The answer a list holds before any: the page's value it names, else its default."""
        prop = get(self.config, f"schema.properties.{name}") or {}
        values, texts = self.listed.get(name) or (
            prop.get("enum") or [],
            [self.text(n) for n in prop.get("enumNames") or prop.get("enum") or []],
        )
        source = get(self.config, f"uiSchema.{name}.preFillWindowObj")
        held = get({"phApp": self.ph}, source) if source else None
        code = self.country_codes().get(held or "")
        for value, shown in zip(values, texts, strict=False):
            if held is not None and held in (value, shown) or code == value:
                return value, shown
        if prop.get("default") in values:
            return prop["default"], texts[values.index(prop["default"])]
        return None, ""

    def country_codes(self) -> dict[str, str]:
        """Countries' codes by name, from the phone code list (`USA_1`, `United States of
        America (+1)`): the job's country is a name, a country list's values codes."""
        out: dict[str, str] = {}
        for prop in _props(self.config["schema"]):
            for value, name in zip(
                prop.get("enum") or [], prop.get("enumNames") or [], strict=False
            ):
                if isinstance(value, str) and (code := re.match(r"([A-Z]{3})_\d+$", value)):
                    out.setdefault(re.sub(r"\s*\(\+.*$", "", self.text(name)), code[1])
        return out

    def call(self, payload: str, **extra: Any) -> Any:
        """Posts a loader's payload to `/applySubmit`, the page's values put in; None when it
        names an answer, or the request fails."""
        if ANSWER.search(payload):
            return None
        filled = PAGE_VALUE.sub(lambda m: _json_text(get({"phApp": self.ph}, m[1])), payload)
        try:
            body = json.loads(filled)
            body.setdefault("formData", {}).update(extra)
            body["formData"].setdefault("applyAtsSource", self.ddo.get("applyATS"))
            return self.post(body)
        except Exception:
            return None

    def loaded(self, u: dict, nav: Any) -> list[dict[str, Any]]:
        """A part of the form its loader serves: a questionnaire of the job's, where the job
        has one."""
        payload = (u.get("apiConfig") or {}).get("payload") or ""
        if not payload or '""' in PAGE_VALUE.sub(
            lambda m: _json_text(get({"phApp": self.ph}, m[1])), payload
        ):
            return []  # the job names no questionnaire of this kind
        extra = {}
        if "getJsq" in payload:  # SuccessFactors' questionnaire, by the job
            job = self.ddo.get("jobData") or {}
            extra = {
                "jobSeqNo": job.get("jobSeqNo"),
                "jobId": job.get("jobId"),
                "locale": _locale(self.ph.get("locale")),
                "apTxnId": str(uuid.uuid4()),
            }
        response = get(self.call(payload, **extra), "applyGetReferences")
        if not isinstance(response, dict):
            raise RuntimeError("the job's questionnaire did not load")
        part = response.get("data") if isinstance(get(response, "data.schema"), dict) else response
        if not isinstance(get(part, "schema"), dict):
            return []
        return self.object(part["schema"], part.get("uiSchema") or {}, "", nav, False, None)

    def job_questions(self, name: str, nav: Any) -> list[dict[str, Any]]:
        """A questionnaire a workflow builds from the job's data (`psqToSchema`, Taleo's
        prescreening): `jobData.<source>.questions`, a follow-up naming its parent question
        and the option that shows it."""
        for flow in self.config.get("workflow") or []:
            for node in flow.get("workflows") or []:
                config = get(node, "data.config") or {}
                if node.get("type") != "psqToSchema" or config.get("targetName") != name:
                    continue
                questions = get(self.ddo, f"jobData.{config.get('sourceName')}.questions")
                prefix = f"{name}.{config.get('questionnaireId') or 'questionnaireId'}."
                return [self.job_question(q, prefix, nav) for q in questions or []]
        return []

    def job_question(self, q: dict, prefix: str, nav: Any) -> dict[str, Any]:
        answers = q.get("possibleAnswers") or []
        kind = {
            "MULTIPLE_CHOICE_SINGLE_SELECT": "select",
            "MULTIPLE_CHOICE_MULTI_SELECT": "checkboxes",
            "TEXT_AREA": "textarea",
        }.get(q.get("type"), "text")
        path = f"{prefix}{q.get('questionId')}"
        options = [self.text(a.get("value")) for a in answers] if answers else None
        self.listed[path] = ([a.get("key") for a in answers], options or [])
        self.labels[path] = self.text(q.get("title")) or path
        parent = q.get("parentQuestionId")
        raw = {
            "id": path,
            "kind": kind if answers or kind != "select" else "text",
            "label": self.labels[path],
            "required": bool(q.get("required")) and not parent,
            "options": options,
            "nav": nav,
        }
        if parent:
            raw["label"] += self.asked_when((f"{prefix}{parent}", [q.get("parentOptionId")], False))
        return raw

    def country_fields(self, p: dict, u: dict, path: str, nav: Any) -> list[dict[str, Any]]:
        """The fields the country's answer before any adds under `cntryFields` (names,
        address)."""
        payload = (u.get("apiConfig") or {}).get("payload") or ""
        code, _ = self.prefilled(path)
        if "getCountrySpecificFields" not in payload or code is None:
            return []
        part = get(self.call(payload, countryCode=code), "applyGetReferences.data")
        if not isinstance(get(part, "schema"), dict):
            return []
        return self.object(
            part["schema"], part.get("uiSchema") or {}, "cntryFields.", nav, False, None
        )

    def date_range(self, p: dict, u: dict, path: str, nav: Any, when: Any) -> list[dict[str, Any]]:
        """A From and To pair, picked as days, months or years by its format."""
        out = []
        for n, title in ((1, p.get("fromTitle")), (2, p.get("toTitle"))):
            name = p.get(f"HtmlInputName{n}") or ("startDate", "endDate")[n - 1]
            needed = u.get(f"input{n}Required", name in (p.get("required") or []))
            out.append(
                {
                    "id": f"{path}.{name}",
                    "kind": _picker(p.get("format") or ""),
                    "label": (self.text(title) or name) + (self.asked_when(when) if when else ""),
                    "required": bool(needed) and not when,
                    "options": None,
                    "format": p.get("format") or "MM/yyyy",
                    "nav": nav,
                }
            )
        return out

    def condition(self, node: dict, path: str, props: dict) -> Any:
        """What an `if` asks: True or False on the job's data, else the one answer it tests,
        `(field id, values, negated)`; `resume` for a resume not given; None for several."""
        parts = []
        if "not" in node:
            inner = self.condition(node["not"], path, props)
            if isinstance(inner, bool):
                parts.append(not inner)
            elif isinstance(inner, tuple):
                parts.append((inner[0], inner[1], not inner[2]))
            else:
                parts.append(None if inner is None else "resume" if inner == "given" else "given")
        for key, kind in (("allOf", all), ("anyOf", any)):
            if key in node:
                parts.append(_combine([self.condition(n, path, props) for n in node[key]], kind))
        for name, test in (node.get("properties") or {}).items():
            parts.append(self.test(name, test, path, props))
        return _combine(parts, all)

    def test(self, name: str, test: dict, path: str, props: dict) -> Any:
        values = [test["const"]] if "const" in test else test.get("enum")
        if name == "resumeName":  # `isNotEmpty`: a resume given
            return "given"
        if (
            get(props.get(name), "type") is None
            or get(self.config, f"uiSchema.{name}.htmlType") == "hidden"
        ):  # the page's own data, known before any answer
            held = get(
                {"phApp": self.ph}, get(self.config, f"uiSchema.{name}.preFillWindowObj") or ""
            )
            held = held if held is not None else {"locale": self.ph.get("locale")}.get(name)
            return True if values is None else held in values
        return None if values is None else (f"{path}{name}", values, False)

    def asked_when(self, when: Any) -> str:
        """The label's note on the answer that shows the field, as the options read."""
        if not isinstance(when, tuple):
            return " (asked depending on other answers)"
        trigger, values, negated = when
        listed = self.listed.get(trigger)
        shown = [
            listed[1][listed[0].index(v)] if listed and v in listed[0] else str(v) for v in values
        ]
        label = self.labels.get(trigger, trigger)
        return f" (asked when {label!r} is {'not ' if negated else ''}{' or '.join(shown)})"


def _combine(parts: list[Any], kind: Callable[[Iterable[object]], bool]) -> Any:
    """Tests combined by `all` or `any`: known ones decide when they can; tests of one answer
    merge; tests of several answers, or of the resume and an answer, are None."""
    known = [p for p in parts if isinstance(p, bool)]
    if (kind is any) in known:  # a False for `all`, a True for `any`
        return kind is any
    named = [p for p in parts if not isinstance(p, bool)]
    if not named:
        return True if kind is all or not known else any(known)
    if len(named) == 1:
        return named[0]
    if any(not isinstance(n, tuple) for n in named) or len({(n[0], n[2]) for n in named}) > 1:
        return None
    sets = [set(n[1]) for n in named]
    merged = set.union(*sets) if (kind is any) != named[0][2] else set.intersection(*sets)
    return (named[0][0], sorted(merged, key=str), named[0][2])


def _rules_hold(ph: dict[str, Any], rules: Any) -> bool:
    """A step's rules on the job's data, json-rules-engine style: when they hold, it is not
    shown."""
    if not isinstance(rules, dict):
        return False
    if "all" in rules or "any" in rules:
        tests = [_rules_hold(ph, r) for r in rules.get("all") or rules.get("any") or []]
        return bool(tests) and (all(tests) if "all" in rules else any(tests))
    fact, value = get(ph, rules.get("fact") or ""), rules.get("value")
    match rules.get("operator"):
        case "equal":
            return fact == value
        case "notEqual":
            return fact != value
        case "in":
            return fact in (value or [])
        case "notIn":
            return fact not in (value or [])
    return False


def _targets(nodes: list[dict]) -> list[str]:
    """The properties a workflow's transforms write: `#schema.schema.properties.<name>`."""
    return [
        m[1]
        for node in nodes
        if node.get("type") == "transform"
        for t in get(node, "data.config") or []
        if isinstance(t, dict) and (m := re.search(r"properties\.(\w+)$", t.get("Path") or ""))
    ]


def _props(schema: dict) -> Iterator[dict]:
    for prop in (schema.get("properties") or {}).values():
        if isinstance(prop, dict):
            yield prop
            yield from _props(prop)


def _kind(p: dict, html: Any, widget: Any, path: str) -> str:
    if widget in ("radio", "radiov2") or html == "radio":
        return "radio"
    if html in ("checkbox", "valuecheckbox") or p.get("type") == "boolean":
        return "checkbox"
    if html == "multipleChoice" or widget == "checkboxes":
        return "checkboxes"
    if html in ("calendar", "date") or widget == "calendar" or p.get("format") == "date":
        return _picker(p.get("format") or "")
    if html == "typeahead":
        return "typeahead"
    if html == "asynctypeaheadv3":
        return "search"
    if html == "textarea" or widget == "textarea":
        return "textarea"
    if html in LISTS or p.get("enum") or get(p, "items.enum"):
        return "select"
    name = path.rsplit(".", 1)[-1]
    if re.search(r"phone", name, re.IGNORECASE) and not re.search(r"extension", name, re.I):
        return "phone"
    return "text"


def _picker(form: str) -> str:
    """The calendar a format picks from: `date` (a day), `month` (`MM/yyyy`), `year`."""
    tokens = {t[0] for t in re.findall(r"y+|m+|d+", form.lower())}
    if not tokens or "d" in tokens:
        return "date"
    return "month" if "m" in tokens else "year"


def _locale(value: str | None) -> str:
    """`en_us` as SuccessFactors' questionnaire takes it, `en_US`."""
    lang, _, country = (value or "en_us").partition("_")
    return f"{lang}_{country.upper()}"


def _json_text(value: Any) -> str:
    return json.dumps("" if value is None else str(value))[1:-1]


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def _seq(url: str) -> str | None:
    parts = urlsplit(url)
    if match := SEQ.match(parts.path):
        return match[1]
    return parse_qs(parts.query).get("jobSeqNo", [None])[0]


def _apply_url(page: Page) -> str:
    """The wizard's URL: the page's, or where the job page's Apply link leads; the wizard's
    own when no link shows, which `_open` then finds or not."""
    parts = urlsplit(page.url)
    prefix = JOB.match(parts.path)
    assert prefix, page.url  # detected on a job or apply URL
    if prefix[2] != "job":
        return f"{parts.scheme}://{parts.netloc}{parts.path}?jobSeqNo={_seq(page.url)}"
    seq = _seq(page.url) or ""
    deadline = time.monotonic() + LINK_TIMEOUT_S
    while (href := page.evaluate(_APPLY_LINK, seq)) is None and time.monotonic() < deadline:
        time.sleep(SETTLE_S)
    target = urlsplit(href or "")
    if target.hostname and target.hostname != parts.hostname:
        raise Unsupported(f"external_step: the application is on {target.hostname}")
    if href and not APPLY.match(target.path):
        raise RuntimeError(f"the Apply link leads to {target.path}")
    if href:
        return f"{parts.scheme}://{parts.netloc}{target.path}?jobSeqNo={seq}"
    return f"{parts.scheme}://{parts.netloc}{prefix[1]}/apply?jobSeqNo={_seq(page.url)}"


def _field(raw: dict[str, Any]) -> Field:
    kind = raw["kind"]
    options = raw["options"]
    if kind == "checkbox":  # an optional box starts clear
        options = YES_NO if raw["required"] else NO_YES
    label = raw["label"] + {"month": MONTH, "year": YEAR}.get(kind, "")
    field_type = FieldType.TYPEAHEAD if kind == "typeahead" and not options else TYPES[kind]
    return Field(
        raw["id"],
        label,
        field_type,
        raw["required"],
        options or None,
        max_length=raw.get("max_length") if field_type is FieldType.TEXT else None,
        prefilled=raw.get("prefilled", False),
    )


def _has_next(page: Page) -> bool:
    return page.locator(FORM).locator(NEXT).count() > 0


def _next(page: Page, number: int) -> None:
    """Clicks Next and waits for the step after `number`; a step that shows errors raises."""
    form = page.locator(FORM)
    before = page.url
    _click(page, form.locator(NEXT))
    deadline = time.monotonic() + STEP_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        time.sleep(SETTLE_S)
        if page.url != before and page.locator(FORM).count():
            with_controls = page.locator(f"{FORM} {NEXT}, {FORM} button[type=submit]")
            if with_controls.count():
                return
        if errors := page.locator(FORM).evaluate(_ERRORS) if page.locator(FORM).count() else []:
            shown = "; ".join(f"{dom}: {message}" for dom, message in errors)
            raise RuntimeError(f"step {number} did not pass: {shown}")
    raise RuntimeError(f"step {number} did not pass in time")


def _click(page: Page, target: Locator) -> None:
    """Clicks once the page's loading overlay is gone."""
    with suppress(Timeout):
        page.locator(LOADING).first.wait_for(state="hidden", timeout=STEP_TIMEOUT_MS)
    browser.click(target)


def _control(page: Page, dom: str) -> Locator:
    """The control whose id is `dom`; a typeahead's menu takes its input's id too."""
    return page.locator(FORM).locator(f":is(input, select, textarea)[id='{dom}']").first


def _read_step(page: Page, step: list[dict[str, Any]]) -> dict[str, Answer]:
    """What each of the step's controls shows, as its answer is given."""
    form = page.locator(FORM)
    out = {}
    for raw in step:
        value = form.evaluate(_READ, raw)
        if raw["kind"] == "date" and isinstance(value, str) and value:
            value = _iso_date(value, raw["format"])
        elif raw["kind"] == "phone" and raw.get("national") and isinstance(value, str) and value:
            dial = form.evaluate(_DIAL)
            value = f"+{dial}{value}" if dial else value
        elif raw["kind"] == "month" and isinstance(value, str) and value:
            numbers = sorted(re.findall(r"\d+", value), key=len)  # `01/2020`, `2020/01`
            if len(numbers) == 2:
                value = f"{numbers[0].zfill(2)}/{numbers[1]}"
        if value not in (None, "", []):
            out[raw["id"]] = value
    return out


def _pick_day(page: Page, dom: str, iso: str) -> None:
    """Picks a day on the open calendar: through its month and year lists, or, without them,
    its previous and next month buttons."""
    year, month, day = (int(n) for n in iso.split("-"))
    picker = page.locator(PICKER).last
    years = picker.locator(
        ".react-datepicker__year-select, .react-datepicker__month-select,"
        " .react-datepicker__header select"
    )
    if years.count() >= 2:  # a year list and a month list, the site's own or the picker's
        lists = years.all()
        by_year = [s for s in lists if any(o.isdigit() for o in s.evaluate(OPTIONS))]
        by_month = [s for s in lists if s not in by_year]
        if not by_year or not by_month or not by_year[0].evaluate(_SELECT, str(year)):
            raise NotTaken(f"{dom} does not list the year {year}")
        names = by_month[0].evaluate(OPTIONS)
        by_month[0].evaluate(_SELECT, names[month - 1 + len(names) - 12])
    elif not picker.locator(".react-datepicker__current-month").count():
        gap = known_gap(Phenom(), "calendar_without_month", f"{dom}'s calendar shows no month")
        raise Unsupported(gap)
    else:
        for _ in range(MAX_MONTH_STEPS):
            shown = picker.locator(".react-datepicker__current-month").inner_text().split()
            at = (int(shown[-1]), MONTHS.index(shown[0][:3].lower()) + 1)
            if at == (year, month):
                break
            step = "next" if at < (year, month) else "previous"
            _click(page, picker.locator(f".react-datepicker__navigation--{step}"))
        else:
            raise NotTaken(f"{dom} does not reach {iso}")
    cell = picker.locator(
        f".react-datepicker__day--{day:03d}:not(.react-datepicker__day--outside-month)"
    ).first
    if "--disabled" in (cell.get_attribute("class") or ""):
        raise NotTaken(f"{dom} does not take {iso}")
    _click(page, cell)


def _page_to(page: Page, picker: Locator, header: str, year: int, dom: str) -> None:
    """Pages the open calendar to `year`, which its header shows, by its year buttons."""
    for _ in range(MAX_MONTH_STEPS):
        shown = int(re.sub(r"\D", "", picker.locator(header).first.inner_text()) or 0)
        if shown == year:
            return
        step = "next" if year > shown else "previous"
        _click(page, picker.locator(f".react-datepicker__navigation--{step}"))
    raise NotTaken(f"{dom} does not reach the year {year}")


def _date_tokens(form: str) -> list[str]:
    return re.findall(r"y+|m+|d+", form.lower())


def _iso_date(shown: str, form: str) -> str:
    """A date the picker shows, as `YYYY-MM-DD`; as shown when it does not follow the form."""
    values = re.findall(r"\d+", shown)
    tokens = _date_tokens(form)
    if len(values) != 3 or len(tokens) != 3:
        return shown
    parts = {t[0]: v for t, v in zip(tokens, values, strict=True)}
    return f"{parts['y']}-{parts['m'].zfill(2)}-{parts['d'].zfill(2)}"


def _one(answer: Answer) -> str:
    return answer if isinstance(answer, str) else answer[0]


def _upload(page: Page, path: str) -> None:
    """Attaches the resume, taken once Phenom's `/fileUpload` answers or the page shows its
    name, and waits for its parse, which fills and clears fields when it comes back: until
    the page's requests have settled."""
    pending: set[Any] = set()
    uploaded: list[bool] = []  # Phenom's `/fileUpload` took it

    def started(request: Any) -> None:
        if request.resource_type in ("xhr", "fetch"):
            pending.add(request)

    def ended(request: Any) -> None:
        pending.discard(request)
        if "/fileUpload" in request.url and (response := request.response()) and response.ok:
            uploaded.append(True)

    page.on("request", started)
    page.on("requestfinished", ended)
    page.on("requestfailed", ended)
    try:
        page.locator(RESUME).first.set_input_files(path)
        name = PurePath(path).stem[:12]  # shown, at times shortened, once uploaded
        deadline = time.monotonic() + UPLOAD_TIMEOUT_MS / 1000
        while not uploaded and not page.evaluate(_SHOWS, name):
            if time.monotonic() > deadline:
                raise RuntimeError("the resume upload did not show the file")
            time.sleep(UPLOAD_POLL_S)
        deadline, quiet_since = time.monotonic() + UPLOAD_TIMEOUT_MS / 1000, time.monotonic()
        while time.monotonic() < deadline and time.monotonic() - quiet_since < QUIET_S:
            time.sleep(UPLOAD_POLL_S)
            if pending:
                quiet_since = time.monotonic()
    finally:
        page.remove_listener("request", started)
        page.remove_listener("requestfinished", ended)
        page.remove_listener("requestfailed", ended)


def _fill(page: Page, raw: dict[str, Any], answer: Answer) -> None:
    form = page.locator(FORM)
    kind, dom = raw["kind"], raw["dom"]
    control = _control(page, dom)
    if kind not in ("radio", "checkbox", "checkboxes"):
        if not control.is_visible() or control.is_disabled():
            return  # shown or enabled by another answer, a round later
        if control.evaluate("el => el.tagName") == "SELECT":
            kind = "select"  # a text box until another answer, then a list (a region)
    match kind:
        case "select":  # a dependent list loads its options once its parent is answered
            for attempt in range(2):
                deadline = time.monotonic() + OPTIONS_TIMEOUT_S
                while not (picked := control.evaluate(_SELECT, _one(answer))):
                    if time.monotonic() > deadline:
                        break
                    time.sleep(SETTLE_S / 2)
                if picked:
                    break
                parent = raw.get("parent")
                if attempt or not parent or not (held := form.locator(f"[id='{parent}']")).count():
                    raise NotTaken(f"{raw['id']} has no option {_one(answer)!r}")
                held.evaluate(_SELECT, held.evaluate("el => el.selectedOptions[0]?.text.trim()"))
        case "typeahead":  # a list, or suggestions for what is typed
            _click(page, control)
            control.fill(_one(answer))
            items = form.locator(".rbt-menu .dropdown-item")
            menu = form.locator(".rbt-menu")
            deadline = time.monotonic() + OPTIONS_TIMEOUT_S
            while time.monotonic() < deadline and (
                not items.count() or SEARCHING.search(" ".join(menu.all_inner_texts()))
            ):
                time.sleep(SETTLE_S / 2)
            for _ in range(PICK_TRIES):  # the list renders again as its results arrive
                time.sleep(SETTLE_S)
                exact = re.compile(rf"^\s*{re.escape(_one(answer))}\s*$")
                item = items.filter(has_text=exact)
                if not items.count():
                    control.press("Enter")  # a new entry, where the list takes one
                    break
                try:
                    _click(page, item.first if item.count() else items.first)
                    break
                except BrowserError, Overlaid:  # rendered again under the click
                    continue
        case "radio":  # its label wraps it or names it by `for`
            if not form.evaluate(_RADIO, [raw["id"], _one(answer)]):
                raise NotTaken(f"{raw['id']} has no option {_one(answer)!r}")
        case "checkbox":
            if control.is_checked() != (_one(answer) == "Yes"):
                control.evaluate("el => el.click()")  # the box is drawn over; React sees a click
        case "checkboxes":
            wanted = [answer] if isinstance(answer, str) else answer
            group = control.locator("xpath=ancestor::*[contains(@class, 'form-group')][1]")
            for box in group.locator("input[type=checkbox]").all():
                label = box.locator("xpath=ancestor::label[1]")
                text = label.inner_text().strip() if label.count() else ""
                if box.is_checked() != (text in wanted):
                    box.evaluate("el => el.click()")
        case "date" if control.get_attribute("type") == "date":  # the browser's own, ISO
            control.fill(_one(answer))
            control.blur()
        case "date":  # picked from the calendar: a typed date does not reach the form
            _click(page, control)
            _pick_day(page, dom, _one(answer))
        case "month":  # the months' names under a year list, or a year paged through
            month, _, year = _one(answer).partition("/")
            _click(page, control)
            picker = page.locator(PICKER).last
            if picker.locator("select.range-select").count():
                if not picker.locator("select.range-select").evaluate(_SELECT, str(int(year))):
                    raise NotTaken(f"{dom} does not list the year {year}")
            else:
                _page_to(page, picker, ".react-datepicker-year-header", int(year), dom)
            _click(page, picker.locator(".react-datepicker__month-text").nth(int(month) - 1))
        case "year":  # pages of twelve years
            _click(page, control)
            picker = page.locator(PICKER).last
            year = int(_one(answer))
            for _ in range(MAX_MONTH_STEPS):
                cell = picker.locator(".react-datepicker__year-text").filter(
                    has_text=re.compile(rf"^{year}$")
                )
                if cell.count():
                    _click(page, cell.first)
                    break
                first = int(picker.locator(".react-datepicker__year-text").first.inner_text())
                step = "next" if year > first else "previous"
                _click(page, picker.locator(f".react-datepicker__navigation--{step}"))
            else:
                raise NotTaken(f"{dom} does not list the year {year}")
        case "phone":  # a national number goes without the code the form asks on its own
            digits = re.sub(r"\D", "", _one(answer))
            dial = form.evaluate(_DIAL) if raw.get("national") else None
            if dial and _one(answer).startswith("+") and digits.startswith(dial):
                digits = digits[len(dial) :]
            control.fill(digits)
            control.blur()
        case _:
            control.fill(_one(answer), timeout=FIELD_TIMEOUT_MS)
            control.blur()
