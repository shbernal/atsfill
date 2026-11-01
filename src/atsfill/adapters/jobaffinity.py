"""JobAffinity (`jobaffinity.fr/apply/<token>`), as docs/adapters/jobaffinity.md observes.

The apply page is rendered on the server in one of two generations: the older form
(`form#identity`, its documents in forms of their own) and the newer one
(`form.candidate-application-form`). The older one may have a questionnaire page after it, also
rendered on the server and reachable by its URL before the first page is posted. `request_read`
fetches both pages over HTTP, `browser_read` takes them from the browser, and both list the
fields with one parser. The dry run fills the questionnaire in a second tab; a send posts the
first page, then fills and posts the questionnaire the server answers with.
"""

import json
import re
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urljoin, urlsplit

from atsfill.core import html
from atsfill.core.adapter import Gap, Unsupported, known_gap
from atsfill.core.browser import HTTP_TIMEOUT_S, Page, Timeout, machine_locale
from atsfill.core.results import Answer, Field, FieldType, Is, SideEffect

ORIGIN = "https://jobaffinity.fr"
HOST = re.compile(r"^(?:www\.)?jobaffinity\.fr$")
APPLY = re.compile(r"^/apply/([a-z0-9]+)/?$")
OLD = "form#identity"
NEW = "form.candidate-application-form"
QUESTIONNAIRE = "form#questionnaire"
APPLIED = "/application_form/submission/candidate_application"
FORM_TIMEOUT_MS = 30_000
UPLOAD_TIMEOUT_MS = 30_000
ANALYSIS_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
YES_NO = ["Yes", "No"]
BROWSER_LANGUAGE = "en-US"  # what the browser sends on a machine with no locale
CONSENT = "gdpr_agreement"
AVAILABILITY, AVAILABLE_FROM, FROM_DATE = "availability", "availability_date", "date"
DMY = "JJ/MM/AAAA"  # the hint under the older form's text date input
KINDS = {
    "text": FieldType.TEXT,
    "number": FieldType.TEXT,
    "textarea": FieldType.TEXT,
    "date": FieldType.DATE,
    "select": FieldType.CHOICE,
    "radio": FieldType.CHOICE,
}
# A rule of the page's own stylesheet that hides the elements of a class.
HIDDEN_CLASS = re.compile(r"\.([\w-]+)\s*\{\s*display:\s*none;?\s*\}")
# The classes JobAffinity's stylesheet hides on every page: `none` is a newer form's hidden row.
HIDDEN = {"experience-field", "none"}
NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")

# What each control holds, by name: a value, a ticked radio's or box's id, a document's names.
_READ_BACK = """(form, fields) => {
  const out = {};
  for (const [id, kind] of fields) {
    if (kind === "old_file") {
      out[id] = [...document.querySelectorAll(`#js-${id}-form .js-newcandidate__uploaded__name`)]
        .map(e => e.textContent.trim()).filter(Boolean);
    } else if (kind === "new_file") {
      const kept = form.querySelector(`input[type=hidden][name="${id}"]`);
      out[id] = JSON.parse(kept?.value || "[]").map(f => f.filename);
    } else {
      const els = [...form.querySelectorAll(`[name="${id}"]`)];
      if (kind === "radio") out[id] = els.find(e => e.checked)?.id ?? "";
      else if (kind === "checkbox") out[id] = els[0]?.checked ? "Yes" : "No";
      else out[id] = els[0]?.value ?? "";
    }
  }
  return out;
}"""
# The fields a form shows, by the names `_parse` gives them: its controls, a radio group once,
# and its documents, the older form's in forms of their own beside it.
_SHOWN = """form => {
  const shown = el => el.getClientRects().length > 0;
  const skipped = ["hidden", "submit", "button", "file"];
  const names = [...form.querySelectorAll("input, select, textarea")]
    .filter(el => el.name && !skipped.includes(el.type) && shown(el)).map(el => el.name);
  const newer = [...form.querySelectorAll("[data-form-field-name]")]
    .filter(shown).map(el => el.dataset.formFieldName);
  const older = [...document.querySelectorAll("form[id^='js-'][id$='-form']")]
    .filter(shown).map(el => el.id.slice("js-".length, -"-form".length));
  return [...new Set([...names, ...newer, ...older])];
}"""


@dataclass
class _Control:
    field: Field
    kind: str  # text | number | select | radio | checkbox | dmy | old_file | new_file
    options: dict[str, str] = field(default_factory=dict)  # option text by value, or by radio id
    questionnaire: bool = False


@dataclass
class _Form:
    new: bool
    controls: list[_Control]
    questionnaire_url: str | None = None  # the older form's second page
    new_questionnaire: bool = False  # the newer form's questionnaire step


class JobAffinity:
    name = "jobaffinity"
    known_gaps = {"questionnaire_step": Gap.FLOW}
    side_effects = [
        SideEffect("upload", "hiring_system", "a document is uploaded to JobAffinity at once"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._form = _Form(False, [])
        self._answers: dict[str, Answer] = {}
        self._questions: Page | None = None  # the questionnaire's tab, in a dry run

    def detect(self, url: str, page: Page | None) -> bool:
        return _token(url) is not None

    def posting(self, url: str) -> str:
        token = _token(url)
        return f"{ORIGIN}/apply/{token}" if token else url

    def request_read(self, url: str) -> list[Field] | None:
        if _token(url) is None:
            return None
        try:
            form = _parse(_get(self.posting(url)), _get)
        except OSError, ValueError:
            return None
        if form is None or form.new_questionnaire:
            return None
        return [c.field for c in form.controls]

    def browser_read(self, page: Page) -> list[Field]:
        try:
            page.locator(f"{OLD}, {NEW}").first.wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        form = _parse(page.content(), lambda url: page.evaluate(_FETCH, url))
        assert form is not None
        if form.new_questionnaire:
            detail = "the newer form's questionnaire step, after a validation of the first"
            raise Unsupported(known_gap(self, "questionnaire_step", detail))
        self._form = form
        return [c.field for c in form.controls]

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        self._answers = answers
        shown = [c for c in self._form.controls if c.field.id in answers and not c.field.unfillable]
        for c in shown:  # documents first: the older form's CV analysis fills only empty fields
            if c.kind in ("old_file", "new_file"):
                _attach(page, c, _one(answers[c.field.id]))
        if any(c.kind == "old_file" for c in shown):
            page.locator("#newcandidate__analyze__progress").wait_for(
                state="hidden", timeout=ANALYSIS_TIMEOUT_MS
            )
        form = page.locator(NEW if self._form.new else OLD)
        for c in shown:
            if not c.questionnaire and c.kind not in ("old_file", "new_file"):
                _set(form, c, answers[c.field.id])
        if self._form.questionnaire_url and any(c.questionnaire for c in shown):
            self._questions = page.context.new_page()
            self._questions.goto(self._form.questionnaire_url)
            questions = self._questions.locator(QUESTIONNAIRE)
            for c in shown:
                if c.questionnaire:
                    _set(questions, c, answers[c.field.id])

    def shown_fields(self, page: Page) -> list[str]:
        shown = page.locator(NEW if self._form.new else OLD).evaluate(_SHOWN)
        if self._questions:
            shown += self._questions.locator(QUESTIONNAIRE).evaluate(_SHOWN)
        return shown

    def read_back(self, page: Page) -> dict[str, Answer]:
        main = [c for c in self._form.controls if not c.questionnaire]
        values = _values(page, NEW if self._form.new else OLD, main)
        if self._questions:
            asked = [c for c in self._form.controls if c.questionnaire]
            values |= _values(self._questions, QUESTIONNAIRE, asked)
        return values

    def submit(self, page: Page) -> str | None:
        """Posts the form; the older one's questionnaire, when the server answers with it, is
        filled again with the dry run's answers and posted. The evidence is the last post's
        answer."""
        if self._questions:
            self._questions.close()
            self._questions = None
        if self._form.new:
            with page.expect_response(
                lambda r: r.request.method == "POST" and urlsplit(r.url).path == APPLIED,
                timeout=SUBMIT_TIMEOUT_MS,
            ) as applied:
                page.locator(f"{NEW} .js-cap-submit-actions [type=submit]").click()
            answer = applied.value.json()
            if not answer.get("ok"):
                raise RuntimeError(f"apply answered {json.dumps(answer.get('errors'))[:200]}")
            return f"{applied.value.url} {applied.value.status}"
        with page.expect_navigation(timeout=SUBMIT_TIMEOUT_MS):
            page.locator(f"{OLD} [type=submit]").click()
        if page.locator(OLD).count():
            raise RuntimeError(f"the first page was not taken: {_errors(page)}")
        if page.locator(QUESTIONNAIRE).count():
            questions = page.locator(QUESTIONNAIRE)
            for c in self._form.controls:
                if c.questionnaire and c.field.id in self._answers:
                    _set(questions, c, self._answers[c.field.id])
            with page.expect_navigation(timeout=SUBMIT_TIMEOUT_MS):
                questions.locator("[type=submit]").click()
            if page.locator(QUESTIONNAIRE).count():
                raise RuntimeError(f"the questionnaire was not taken: {_errors(page)}")
        return page.url


# A page's HTML fetched from inside it, so it is the browser's session that asks.
_FETCH = "url => fetch(url, {credentials: 'include'}).then(r => r.text())"


def _token(url: str) -> str | None:
    parts = urlsplit(url)
    path = APPLY.match(parts.path)
    return path[1] if HOST.match(parts.hostname or "") and path else None


def _get(url: str) -> str:
    """The page as the browser gets it: the server localizes the country list."""
    language = machine_locale() or BROWSER_LANGUAGE
    headers = {"User-Agent": "atsfill", "Accept-Language": language}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return response.read().decode()


def _parse(source: str, fetch) -> _Form | None:
    """The apply page's fields, the older form's questionnaire fetched by `fetch` when its
    stepper names a page after the first."""
    page = html.parse(source)
    nodes = page.walk()
    hidden = HIDDEN | {
        m[1] for n in nodes if n.tag == "style" for m in HIDDEN_CLASS.finditer(n.text())
    }
    labels = {n.attrs["for"]: n for n in nodes if n.tag == "label" and "for" in n.attrs}
    new = next(
        (n for n in nodes if n.tag == "form" and _has(n, "candidate-application-form")), None
    )
    if new is not None:
        files = [
            _new_file(n, labels) for n in new.walk() if "data-form-field-name" in n.attrs
        ]  # fmt: skip
        panel = new.closest(lambda n: "data-cap-wizard" in n.attrs)
        return _Form(
            True,
            files + _controls(new, labels, hidden),
            new_questionnaire=panel is not None and panel.attrs["data-cap-wizard"] != "0",
        )
    old = next((n for n in nodes if n.tag == "form" and n.attrs.get("id") == "identity"), None)
    if old is None:
        return None
    files = [
        _old_file(n)
        for n in nodes
        if n.tag == "form" and re.fullmatch(r"js-\w+-form", n.attrs.get("id", ""))
    ]
    form = _Form(False, files + _controls(old, labels, hidden))
    stepper = page.find(lambda n: n.attrs.get("id") == "stabilo-haut")
    if stepper and stepper.find(lambda n: _has(n, "step-separator")):
        form.questionnaire_url = _questionnaire_url(old)
        questions = html.parse(fetch(form.questionnaire_url))
        asked = questions.find(lambda n: n.tag == "form" and n.attrs.get("id") == "questionnaire")
        if asked is not None:
            q_labels = {
                n.attrs["for"]: n for n in asked.walk() if n.tag == "label" and "for" in n.attrs
            }
            for c in _controls(asked, q_labels, HIDDEN):
                c.questionnaire = True
                form.controls.append(c)
    return form


def _questionnaire_url(form: html.Node) -> str:
    """The older form's questionnaire, by its wizard, job and session."""
    values = {
        n.attrs.get("name"): n.attrs.get("value", "") for n in form.walk() if n.tag == "input"
    }
    session = parse_qs(urlsplit(form.attrs.get("action", "")).query).get("session_id", [""])[0]
    path = f"/newcandidate/questionnaire/{values['wizard_id']}/{values['job_id']}"
    return urljoin(ORIGIN, f"{path}?session_id={session}&ui=False")


def _controls(form: html.Node, labels: dict[str, html.Node], hidden: set[str]) -> list[_Control]:
    """Each named control of the form, a radio group once; those its page's styles hide left out."""
    out: list[_Control] = []
    seen: set[str] = set()
    for node in form.walk():
        name = node.attrs.get("name")
        if node.tag not in ("input", "select", "textarea") or not name or name in seen:
            continue
        kind = node.attrs.get("type", "text") if node.tag == "input" else node.tag
        if kind in ("hidden", "submit", "file", "button"):
            continue
        # the date's row is hidden until its availability shows it
        if _hidden(node, hidden - {"none"} if name == AVAILABLE_FROM else hidden):
            continue
        seen.add(name)
        row = node.closest(lambda n: _has(n, "form-row-wrapper") or _has(n, "form-group"))
        own = labels.get(node.attrs.get("id", ""))
        if kind in ("radio", "checkbox") and name != CONSENT:
            block = node.closest(lambda n: _has(n, "questionnaire-question-block"))
            group = block.find(lambda n: _has(n, "input-control-label")) if block else None
            own = group
        label = _label(own) if own is not None else name
        required = own is not None and _starred(own)
        if name == CONSENT and kind == "checkbox":
            f = Field(name, label, FieldType.CHOICE, True, YES_NO)
            out.append(_Control(f, "checkbox"))
            continue
        if kind not in KINDS or "multiple" in node.attrs:
            reason = f"unhandled_rendering: {name} is a {kind}"
            out.append(
                _Control(Field(name, label, FieldType.TEXT, required, unfillable=reason), kind)
            )
            continue
        options: dict[str, str] = {}
        if kind == "select":
            options = {
                o.attrs["value"]: " ".join(o.text().split())
                for o in node.walk()
                if o.tag == "option" and o.attrs.get("value")
            }
        elif kind == "radio":
            options = {
                r.attrs["id"]: _label(labels[r.attrs["id"]])
                for r in form.walk()
                if r.tag == "input" and r.attrs.get("name") == name and r.attrs.get("id") in labels
            }
        field_type = KINDS[kind]
        hint = row.find(lambda n: _has(n, "form-text")) if row else None
        dmy = kind == "text" and hint is not None and DMY in hint.text()
        if dmy:
            field_type = FieldType.DATE
        elif re.search("phone", name, re.IGNORECASE):
            field_type = FieldType.PHONE
        shown_when = None
        if name == AVAILABLE_FROM:
            shown_when = Is(
                AVAILABILITY, [t for v, t in _availability(form).items() if v == FROM_DATE]
            )
        max_length = int(node.attrs.get("maxlength", 0)) or None
        f = Field(
            name,
            label,
            field_type,
            required,
            list(options.values()) or None,
            max_length if field_type in (FieldType.TEXT, FieldType.PHONE) else None,
            shown_when=shown_when,
        )
        kept = "dmy" if dmy else kind if kind in ("radio", "select", "number") else "text"
        out.append(_Control(f, kept, options))
    return out


def _hidden(node: html.Node, classes: set[str]) -> bool:
    return node.closest(lambda n: any(c in classes for c in n.classes())) is not None


def _availability(form: html.Node) -> dict[str, str]:
    select = form.find(lambda n: n.tag == "select" and n.attrs.get("name") == AVAILABILITY)
    if select is None:
        return {}
    return {
        o.attrs["value"]: " ".join(o.text().split())
        for o in select.walk()
        if o.tag == "option" and o.attrs.get("value")
    }


def _old_file(form: html.Node) -> _Control:
    """A document of the older form, in a form of its own: `js-cv-form` is `cv`."""
    name = form.attrs["id"].removeprefix("js-").removesuffix("-form")
    label = form.find(lambda n: n.tag == "label" and _has(n, "description"))
    text = _label(label) if label is not None else name
    required = label is not None and _starred(label)
    return _Control(Field(name, text, FieldType.FILE, required), "old_file")


def _new_file(node: html.Node, labels: dict[str, html.Node]) -> _Control:
    """A document of the newer form: an upload widget named by `data-form-field-name`."""
    name = node.attrs["data-form-field-name"]
    label = labels.get(f"_field-{name}")
    text = _label(label) if label is not None else name
    required = label is not None and _starred(label)
    return _Control(Field(name, text, FieldType.FILE, required), "new_file")


def _has(node: html.Node, cls: str) -> bool:
    return cls in node.classes()


def _label(node: html.Node) -> str:
    """A label's own text, without its required marker and its help popup."""
    parts = (
        c
        for c in node.children
        if isinstance(c, str) or c.tag not in ("span", "div", "script", "button")
    )
    return " ".join("".join(c if isinstance(c, str) else c.text() for c in parts).split())


def _starred(node: html.Node) -> bool:
    """A required marker, which the newer form renders hidden (`required none`) when not."""
    return any(
        isinstance(c, html.Node) and _has(c, "required") and not _has(c, "none")
        for c in node.children
    )


def _one(answer: Answer) -> str:
    return answer if isinstance(answer, str) else answer[0]


def _set(form, c: _Control, answer: Answer) -> None:
    """Puts an answer in its control, inside the form locator `form`."""
    value = _one(answer)
    control = form.locator(f"[name='{c.field.id}']")
    match c.kind:
        case "select":
            by_text = {t: v for v, t in c.options.items()}
            control.select_option(value=by_text.get(value, value))
        case "radio":
            radio = next((i for i, t in c.options.items() if t == value), value)
            form.locator(f"label[for='{radio}']").click()
        case "checkbox":  # under its label, whose notice link takes a click without ticking it
            if control.is_checked() != (value == "Yes"):
                control.evaluate("box => box.click()")
        case "number" if not NUMBER.fullmatch(value.strip()):
            pass  # the input takes no text: left empty, it reads back as a mismatch
        case "dmy":
            year, month, day = value.split("-")
            control.fill(f"{day}/{month}/{year}")
        case _:
            control.fill(value)


def _attach(page: Page, c: _Control, path: str) -> None:
    """Attaches a document and waits for JobAffinity's upload to list its name."""
    name = path.rsplit("/", 1)[-1]
    if c.kind == "old_file":
        page.locator(f"#js-{c.field.id}-form input[type=file]").set_input_files(path)
        shown = page.locator(f"#js-{c.field.id}-form .js-newcandidate__uploaded__name")
    else:
        widget = page.locator(f".js-file-upload__{c.field.id}")
        widget.locator("input[type=file]").set_input_files(path)
        shown = widget.locator(".file-item")
    shown.filter(has_text=name).first.wait_for(timeout=UPLOAD_TIMEOUT_MS)


def _values(page: Page, form: str, controls: list[_Control]) -> dict[str, Answer]:
    kinds = [[c.field.id, c.kind] for c in controls if not c.field.unfillable]
    values = page.locator(form).evaluate(_READ_BACK, kinds)
    out: dict[str, Answer] = {}
    for c in controls:
        value = values.get(c.field.id)
        if c.kind in ("old_file", "new_file"):
            value = value[-1] if value else None
        elif c.kind in ("select", "radio"):
            value = c.options.get(value, value)
        elif c.kind == "dmy" and value and (d := re.fullmatch(r"(\d\d)/(\d\d)/(\d{4})", value)):
            value = f"{d[3]}-{d[2]}-{d[1]}"
        if value not in (None, ""):
            out[c.field.id] = value
    return out


def _errors(page: Page) -> str:
    shown = page.locator(".error, .small-error:visible, .invalid-feedback:visible")
    return " ".join(" ".join(shown.all_inner_texts()).split())[:200] or "no error shown"
