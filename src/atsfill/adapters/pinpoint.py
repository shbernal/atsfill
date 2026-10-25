"""Pinpoint (`<employer>.pinpointhq.com` or a career domain, `/<lang>/postings/<uuid>`), as
docs/adapters/pinpoint.md observes.

The apply page is rendered on the server: the standard fields as native controls, every other
field as a React component whose props sit in a JSON script next to it. `request_read` fetches that
HTML over HTTP, `browser_read` takes the page's form, and both list the fields from it with one
parser. A document is uploaded to Pinpoint once attached.
"""

import json
import re
import urllib.request
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import html
from atsfill.core.adapter import Gap, NotTaken, Unsupported, known_gap
from atsfill.core.browser import HTTP_TIMEOUT_S, Locator, Page, Timeout
from atsfill.core.results import Answer, AnyOf, Condition, Field, FieldType, Is, SideEffect

HOST = re.compile(r"(?:^|\.)pinpointhq\.com$")
JOB = re.compile(
    r"^/([a-z]{2}(?:-[a-z]{2})?)/postings/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{12})(?:/applications/new)?/?$",
    re.IGNORECASE,
)
FORM = "#application-form"
SUBMIT = f"{FORM} button[type=submit]"
COMPONENT = "js-react-on-rails-component"
FORM_TIMEOUT_MS = 30_000
HYDRATE_TIMEOUT_MS = 30_000
OPTION_TIMEOUT_MS = 5_000
SHOWN_TIMEOUT_MS = 2_000
US_STATE = ".react-select:has(input[type=hidden][name$='[town]'])"  # the town's place in the US
UPLOAD_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
YES_NO = ["Yes", "No"]
US = "United States"  # the country whose address asks for a city and a state
JOB_PREFERENCES = "job_preferences"
TEXT_QUESTIONS = {"Shorttext", "Longtext", "Url", "Numberinput"}  # Url is a `url` field
FILE_QUESTIONS = {"Document", "Multipledocuments"}
NOT_FIELDS = {"New", "Chatbot", "Stagescarousel"}  # components without a field, by last name

# What each field holds, by how it renders: a control's value, a react-select's shown value(s),
# a radio's label, a date's three selects, a document's uploaded names.
_READ_BACK = r"""(form, specs) => {
  const text = e => e ? e.textContent.replace(/\s+/g, " ").trim() : null;
  const root = s => s.root ? form.querySelector(`[id="${s.root}"]`) : form;
  const control = s => root(s)?.querySelector(`[name="${s.name}"]:not([type=hidden])`);
  const shown = box => {
    const multi = [...(box?.querySelectorAll(".react-select__multi-value__label") ?? [])];
    if (multi.length) return multi.map(text);
    return text(box?.querySelector(".react-select__single-value"));
  };
  const out = {};
  for (const s of specs) {
    const us = !!root(s)?.querySelector(`.react-select input[type=hidden][name$="[town]"]`);
    if (s.kind === "text" || s.kind === "town") out[s.id] = control(s)?.value ?? null;
    else if (s.kind === "line2" || s.kind === "city")
      out[s.id] = us === (s.kind === "city") ? control(s)?.value ?? null : null;
    else if (s.kind === "check")
      out[s.id] = control(s) ? (control(s).checked ? "Yes" : "No") : null;
    else if (s.kind === "radio") {
      const on = root(s)?.querySelector(`input[name="${s.name}"]:checked`);
      out[s.id] = on ? text(form.querySelector(`label[for="${on.id}"]`)) : null;
    } else if (s.kind === "react" || s.kind === "state") {
      const hidden = s.name && root(s)?.querySelector(`input[type=hidden][name="${s.name}"]`);
      out[s.id] = shown(s.name ? hidden?.closest(".react-select") : root(s));
    } else if (s.kind === "date") {
      const part = n => root(s)?.querySelector(`[name="${s.name}(${n}i)]"]`)?.value;
      const [y, m, d] = [part(1), part(2), part(3)];
      out[s.id] = y && m && d ? `${y}-${m.padStart(2, "0")}-${d.padStart(2, "0")}` : null;
    } else if (s.kind === "file") {
      const names = [...(root(s)?.querySelectorAll(".bp3-tag a.link") ?? [])];
      out[s.id] = text(names[names.length - 1]);
    }
  }
  return out;
}"""


_SHOWN = r"""(form, specs) => specs.filter(s => {
  const root = s.root ? form.querySelector(`[id="${s.root}"]`) : form;
  if (!root?.querySelector("input, select, textarea")) return false;
  const us = !!root.querySelector(`.react-select input[type=hidden][name$="[town]"]`);
  if (s.kind === "line2" || s.kind === "town") return !us;
  if (s.kind === "city" || s.kind === "state") return us;
  return true;
}).map(s => s.id)"""


@dataclass
class _Spec:
    """Where a field's control is: the component's root id (random per page load), the control's
    name, and how it renders."""

    kind: (
        str  # text | check | radio | react | date | file; the address's line2 | city | town | state
    )
    name: str | None = None
    root: str | None = None
    options: dict[str, str] | None = None  # a radio's value by its label


class Pinpoint:
    name = "pinpoint"
    known_gaps: dict[str, Gap] = {JOB_PREFERENCES: Gap.RENDERING}
    side_effects = [
        SideEffect("upload", "hiring_system", "a document is uploaded to Pinpoint at once"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._fields: list[Field] = []
        self._specs: dict[str, _Spec] = {}

    def detect(self, url: str, page: Page | None) -> bool:
        return _job(url) is not None

    def posting(self, url: str) -> str:
        job = _job(url)
        return f"https://{urlsplit(url).hostname}/en/postings/{job[1]}" if job else url

    def request_read(self, url: str) -> list[Field] | None:
        if _job(url) is None:
            return None
        request = urllib.request.Request(_apply(url), headers={"User-Agent": "atsfill"})
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
                page = html.parse(response.read().decode())
        except OSError, ValueError:
            return None
        form = next((n for n in page.walk() if n.attrs.get("id") == "application-form"), None)
        return [f for f, _ in _form(form, self)] if form else None

    def browser_read(self, page: Page) -> list[Field]:
        if _job(page.url) and not urlsplit(page.url).path.rstrip("/").endswith("/new"):
            page.goto(_apply(page.url))
        try:
            page.locator(SUBMIT).wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        _hydrated(page)
        listed = _form(html.parse(page.locator(FORM).evaluate("f => f.outerHTML")), self)
        self._specs = {f.id: s for f, s in listed if not f.unfillable}
        if "country" in self._specs:  # the visitor's, guessed by the page
            held = self.read_back(page).get("country")
            listed = [(replace(f, default=held) if f.id == "country" else f, s) for f, s in listed]
        self._fields = [f for f, _ in listed if not f.unfillable]
        return [f for f, _ in listed]

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        form = page.locator(FORM)
        for f in self._fields:
            if f.id not in answers:
                continue
            answer, spec = answers[f.id], self._specs[f.id]
            values = [answer] if isinstance(answer, str) else answer
            root = form.locator(f'[id="{spec.root}"]') if spec.root else form
            if spec.root and not _shown(root):
                continue  # a question its parent's answer keeps hidden
            control = root.locator(f'[name="{spec.name}"]:not([type=hidden])')
            us = root.locator(US_STATE)
            match spec.kind:
                case "text":
                    control.fill(values[0])
                case "line2" | "town" if not us.count():
                    control.fill(values[0])
                case "city" if us.count():
                    control.fill(values[0])
                case "state" if us.count():
                    _pick(us, values[0], typed=True)
                case "check":
                    control.set_checked(values[0] == YES_NO[0])
                case "radio":
                    value = (spec.options or {}).get(values[0], values[0])
                    radio = root.locator(f'input[name="{spec.name}"][value="{value}"]')
                    form.locator(f'label[for="{radio.get_attribute("id")}"]').click()
                case "react":
                    box = root.locator(".react-select").first
                    if spec.name:
                        box = root.locator(f'.react-select:has(input[name="{spec.name}"])')
                    for value in values:
                        _pick(box, value)
                    if box.locator(".react-select__option").count():  # a multi-select's menu
                        box.locator("input[role=combobox]").press("Escape")
                case "date":
                    day = date.fromisoformat(values[0])
                    for n, part in ((1, day.year), (2, day.month), (3, day.day)):
                        _choose(root.locator(f'select[name="{spec.name}({n}i)]"]'), str(part))
                case "file":
                    _attach(root, values[0])

    def shown_fields(self, page: Page) -> list[str]:
        """The fields of the filled page's form whose control shows: a question component
        renders none while its parent's answer keeps it hidden, an address line one layout's."""
        listed = _form(html.parse(page.locator(FORM).evaluate("f => f.outerHTML")), self)
        specs = [{"id": f.id, "kind": s.kind, "root": s.root} for f, s in listed]
        return page.locator(FORM).evaluate(_SHOWN, specs)

    def read_back(self, page: Page) -> dict[str, Answer]:
        specs = [
            {"id": i, "kind": s.kind, "name": s.name, "root": s.root}
            for i, s in self._specs.items()
        ]
        values = page.locator(FORM).evaluate(_READ_BACK, specs)
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        """Clicks submit; the evidence is the page the form's POST leads to, unless it is the
        form again (`/applications`, a rejected answer)."""
        page.locator(SUBMIT).click()
        page.wait_for_url(
            lambda u: not urlsplit(u).path.endswith("/applications/new"), timeout=SUBMIT_TIMEOUT_MS
        )
        if urlsplit(page.url).path.rstrip("/").endswith("/applications"):
            raise RuntimeError("the form came back after the submit")
        return page.url


def _job(url: str) -> tuple[str, str] | None:
    """The language and posting uuid, from `/<lang>/postings/<uuid>[/applications/new]`."""
    match = JOB.match(urlsplit(url).path)
    return (match[1], match[2].lower()) if match else None


def _apply(url: str) -> str:
    """The posting's apply page."""
    job = _job(url)
    assert job is not None
    return f"https://{urlsplit(url).hostname}/{job[0]}/postings/{job[1]}/applications/new"


def _hydrated(page: Page) -> None:
    """Waits for React to render the components with a field; a question's may stay empty,
    kept hidden by an earlier answer."""
    with suppress(Timeout):
        page.wait_for_function(
            """skip => [...document.querySelectorAll("#application-form script.COMPONENT")]
              .filter(s => !s.dataset.componentName.includes("Questions")
                && !skip.includes(s.dataset.componentName.split("::").pop()))
              .every(s => document.getElementById(s.dataset.domId)?.childElementCount)""".replace(
                "COMPONENT", COMPONENT
            ),
            arg=sorted(NOT_FIELDS),
            timeout=HYDRATE_TIMEOUT_MS,
        )


def _shown(root: Locator) -> bool:
    """Whether a component shows a control, waiting a moment for one an earlier answer opens."""
    try:
        root.locator("input, select, textarea").first.wait_for(
            state="attached", timeout=SHOWN_TIMEOUT_MS
        )
    except Timeout:
        return False
    return True


def _pick(box: Locator, value: str, typed: bool = False) -> None:
    """Opens a react-select, `typed` types the answer to narrow a long list, and clicks the
    option that is the answer."""
    options = box.locator(".react-select__option")
    if not options.count():  # a multi-select's menu stays open between picks
        box.locator(".react-select__control").click()
    if typed:
        box.locator("input.react-select__input").press_sequentially(value)
    words = r"\s+".join(re.escape(w) for w in value.split())
    option = options.filter(has_text=re.compile(rf"^\s*{words}\s*$")).first
    try:
        option.wait_for(timeout=OPTION_TIMEOUT_MS)
    except Timeout:
        box.locator(".react-select__control").press("Escape")
        raise NotTaken(f"no option {value!r}") from None
    option.click()


def _choose(select: Locator, value: str) -> None:
    """Picks a date part's option, or says the select has none for it."""
    if not select.locator(f'option[value="{value}"]').count():
        raise NotTaken(f"no option {value!r} in {select.get_attribute('name')}")
    select.select_option(value)


def _attach(root: Locator, path: str) -> None:
    """Attaches a document and waits for Pinpoint's upload to show its name."""
    name = path.rsplit("/", 1)[-1]
    root.locator("input[type=file]").first.set_input_files(path)
    with suppress(Timeout):
        root.locator(".bp3-tag a.link", has_text=name).first.wait_for(timeout=UPLOAD_TIMEOUT_MS)


def _key(name: str) -> str:
    """A control's attribute: `first_name` for `application_form[application][first_name]`."""
    return re.findall(r"\w+", re.sub(r"\(\di\)", "", name))[-1]


def _text(label: object) -> str:
    """A label with its runs of whitespace as one space, trimmed."""
    return " ".join(str(label).split())


def _in_component(node: html.Node) -> bool:
    """Whether a control is one a React component renders, read from its props instead."""
    return node.parent is not None and bool(
        node.parent.closest(lambda n: "-react-component-" in n.attrs.get("id", ""))
    )


def _accept(extensions: list[str] | None) -> list[str] | None:
    return [f".{e}" for e in extensions] if extensions else None


def _form(form: html.Node, adapter: Pinpoint) -> list[tuple[Field, _Spec]]:
    """Each field of the form, in its order, with where its control is."""
    nodes = form.walk()
    labels = {
        n.attrs["for"]: _text(n.text()) for n in nodes if n.tag == "label" and "for" in n.attrs
    }
    out: list[tuple[Field, _Spec]] = []
    titles: dict[int, tuple[str, dict[str, str]]] = {}  # a question's id, its values' labels
    for node in nodes:
        if node.tag == "script" and COMPONENT in node.classes():
            props = json.loads(_text(node.text()) or "{}")
            name = node.attrs.get("data-component-name", "")
            out += _component(name, props, node, labels, titles, adapter)
        elif node.tag in ("input", "select", "textarea") and not _in_component(node):
            out += _plain(node, labels, out)
    return out


def _plain(node: html.Node, labels: dict[str, str], listed: list) -> list[tuple[Field, _Spec]]:
    """A server-rendered control: a name, the email, the summary, a date of birth, the consent."""
    name, kind = node.attrs.get("name", ""), node.attrs.get("type", node.tag)
    if not name or kind in ("hidden", "submit", "button") or "g-recaptcha" in name:
        return []
    field_id, required = _key(name), "required" in node.attrs
    label = labels.get(node.attrs.get("id", ""), "").rstrip(" *") or field_id
    if re.search(r"\(\di\)\]$", name):  # a date's day, month and year selects
        if any(f.id == field_id for f, _ in listed):
            return []
        label = labels.get(node.attrs.get("id", "")[: -len("_3i")], label)
        prefix = name[: name.rindex("(")]  # the selects' names are `<prefix>(<n>i)]`
        return [(Field(field_id, label, FieldType.DATE, required), _Spec("date", prefix))]
    if kind == "checkbox":
        return [(Field(field_id, label, FieldType.CHOICE, required, YES_NO), _Spec("check", name))]
    if kind in ("text", "email", "textarea"):
        length = int(node.attrs.get("maxlength", 0)) or None
        return [
            (
                Field(field_id, label, FieldType.TEXT, required, max_length=length),
                _Spec("text", name),
            )
        ]
    reason = f"unhandled_rendering: {field_id} is a {kind}"
    return [(Field(field_id, label, FieldType.TEXT, required, unfillable=reason), _Spec(kind))]


def _component(
    name: str,
    props: dict[str, Any],
    script: html.Node,
    labels: dict[str, str],
    titles: dict[int, tuple[str, dict[str, str]]],
    adapter: Pinpoint,
) -> list[tuple[Field, _Spec]]:
    """The fields a React component renders, from its props."""
    root = script.attrs.get("data-dom-id")
    short = name.removeprefix("Shared::Form::").removeprefix("Questions::")
    if name.endswith("Phonenumberinput"):
        label = labels.get(props.get("id", ""), "Phone")
        spec = _Spec("text", props["name"], root)
        return [
            (Field(_key(props["name"]), label, FieldType.PHONE, bool(props.get("required"))), spec)
        ]
    if name.endswith("::Address"):
        return _address(props, root)
    if name.endswith("::Filefield"):
        label, required = _file_label(script, props)
        accept = _accept(props.get("accept"))
        return [
            (
                Field(_key(props["name"]), label, FieldType.FILE, required, accept=accept),
                _Spec("file", props["name"], root),
            )
        ]
    if name.endswith("Equalitymonitoring::Tsselect"):
        field_id = props["inputId"].removeprefix("application_form_")
        options = [_text(o["label"]) for o in props.get("options", [])]
        label = labels.get(props["inputId"], field_id)
        spec = _Spec("react", None, root)
        return [
            (Field(field_id, label, FieldType.CHOICE, bool(props.get("required")), options), spec)
        ]
    if name.endswith("::Jobpreferences"):
        field_id = props.get("jobPreferenceName", JOB_PREFERENCES)
        reason = known_gap(adapter, JOB_PREFERENCES, f"{field_id} is a further job to apply for")
        label = f"{props.get('ordinalizedNumber', '')} preference".strip()
        return [
            (Field(field_id, label, FieldType.CHOICE, False, unfillable=reason), _Spec("react"))
        ]
    details = props.get("questionDetails")
    if not details:
        if name.split("::")[-1] in NOT_FIELDS or "name" not in props:
            return []
        reason = f"unhandled_rendering: {name}"
        return [
            (
                Field(_key(props["name"]), name, FieldType.TEXT, False, unfillable=reason),
                _Spec(name),
            )
        ]
    qid = details["questionId"]
    field_id = f"question_{qid}"
    label = " ".join(
        (details.get("title") or details.get("additionalInformation") or field_id).split()
    )
    required = bool(details.get("required"))
    options = None
    prefix = f"application_form[application][answers_attributes][{details['index']}]"
    yes_no = props.get("translations", {})
    if short in TEXT_QUESTIONS:
        field_type, spec = FieldType.TEXT, _Spec("text", props["name"], root)
        if short == "Url":
            field_type = FieldType.URL
        if short == "Numberinput":
            label += " (a number)"
    elif short == "Boolean":
        options = [yes_no.get("yes", "Yes"), yes_no.get("no", "No")]
        field_type = FieldType.CHOICE
        spec = _Spec(
            "radio",
            f"{prefix}[boolean_answer]",
            root,
            dict(zip(options, ("true", "false"), strict=True)),
        )
    elif short in ("Singleselect", "Multiselect"):
        options = [_text(o["label"]) for o in props.get("options", [])]
        field_type = FieldType.CHOICE if short == "Singleselect" else FieldType.MULTI_CHOICE
        spec = _Spec("react", None, root)
    elif short == "Dateselect":
        field_type, spec = FieldType.DATE, _Spec("date", f"{prefix}[date_answer", root)
    elif short in FILE_QUESTIONS:
        field_type, spec = FieldType.FILE, _Spec("file", None, root)
    else:
        reason = f"unhandled_rendering: {field_id} is a {short}"
        titles[qid] = (field_id, {})
        return [(Field(field_id, label, FieldType.TEXT, required, unfillable=reason), _Spec(short))]
    shown_when = None
    if parent := titles.get(props.get("conditionalQuestionParentQuestionId") or -1):
        shown_when = _shown_when(parent, props.get("conditionalQuestionConditions") or [])
    if short == "Boolean":
        words = dict(zip(("true", "false"), options or [], strict=False))
    else:
        words = {str(o.get("value")): _text(o["label"]) for o in props.get("options", [])}
    titles[qid] = (field_id, words)
    accept = _accept(props.get("accept")) if field_type is FieldType.FILE else None
    field = Field(
        field_id, label, field_type, required, options, accept=accept, shown_when=shown_when
    )
    return [(field, spec)]


def _shown_when(parent: tuple[str, dict[str, str]], conditions: list[dict]) -> Condition | None:
    """The parent's answer `equals` or `not_equals` the values, each in its option's label (a
    boolean's `true` in its words); a `not_equals` one stays hidden while the parent is
    unanswered."""
    field_id, words = parent
    held: list[Condition] = []
    for operator, negated in (("equals", False), ("not_equals", True)):
        values = [
            words.get(str(c["value"]), str(c["value"]))
            for c in conditions
            if c.get("operator", "equals") == operator
        ]
        if values:
            held.append(Is(field_id, values, negated))
    return None if not held else held[0] if len(held) == 1 else AnyOf(held)


def _file_label(script: html.Node, props: dict) -> tuple[str, bool]:
    """A document's label, from the `external-form__label` block before its component."""
    parent = script.parent
    siblings = [c for c in parent.children if isinstance(c, html.Node)] if parent else []
    before = siblings[: siblings.index(script)] if script in siblings else []
    block = next((n for n in reversed(before) if "external-form__label" in n.classes()), None)
    if block is None:
        return props.get("labelText") or _key(props["name"]), False
    title = next((n for n in block.walk() if n.tag == "div"), block)
    required = any("external-form__label--required" in n.classes() for n in [block, *block.walk()])
    return _text(title.text()), required


def _address(props: dict, root: str | None) -> list[tuple[Field, _Spec]]:
    """The address: a country, then lines whose labels and kinds follow it. The United States
    asks for a city in the second line and a state (a select) in the town's place; with no
    country the lines are the others'."""
    names, words = props["fieldNames"], props.get("translations", {})
    country = words.get("country", "Country")
    us = Is("country", [US])
    not_us = AnyOf([Is("country", [US], negated=True), Is("country", negated=True)])
    countries = [_text(o["label"]) for o in props.get("countryOptions", [])]
    states = [_text(o["label"]) for o in props.get("stateOptions", [])]
    line1 = f"{words.get('addressOne', 'Address Line 1')} ({words.get('streetAddress')})"
    postcode = f"{words.get('postcode', 'Postcode')} ({words.get('zipcode')})"
    rows = [  # id, label, type, required, options, kind, name, shown when
        ("country", country, FieldType.CHOICE, True, countries, "react", "country", None),
        ("address1", line1, FieldType.TEXT, True, None, "text", "addressOne", None),
        ("address2", words.get("addressTwo"), FieldType.TEXT, False, None, "line2", "addressTwo",
         not_us),
        ("city", words.get("city"), FieldType.TEXT, False, None, "city", "addressTwo", us),
        ("town", words.get("town"), FieldType.TEXT, False, None, "town", "town", not_us),
        ("state", words.get("state"), FieldType.CHOICE, False, states, "state", "town", us),
        ("postcode", postcode, FieldType.TEXT, True, None, "text", "postcode", None),
    ]  # fmt: skip
    return [
        (Field(i, str(label), t, required, options, shown_when=when), _Spec(kind, names[n], root))
        for i, label, t, required, options, kind, n, when in rows
    ]
