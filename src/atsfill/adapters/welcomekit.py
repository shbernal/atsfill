"""WelcomeKit, Welcome to the Jungle's ATS, as docs/adapters/welcomekit.md observes.

Two forms, by host. The older one (`welcomekit.co/candidates/new?o=<org>&j=<job>`) is a Rails
form rendered on the server: `request_read` fetches it over HTTP, `browser_read` takes the page's
HTML, one parser lists the fields. The newer one (`ats.welcometothejungle.com/board/<org>/job/
<id>/apply`) is a React app over a JSON API: fixed personal fields, then the job's questions as
the API declares them; both reads list the fields from that JSON.
"""

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from atsfill.core import html
from atsfill.core.adapter import Blocked, Gap, Unsupported
from atsfill.core.browser import HTTP_TIMEOUT_S, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

OLD_ORIGIN = "https://www.welcomekit.co"
OLD_HOST = re.compile(r"^(?:www\.)?welcomekit\.co$")
OLD_PATH = "/candidates/new"
OLD = "form#new_candidate"
NEW_ORIGIN = "https://ats.welcometothejungle.com"
NEW_HOST = "ats.welcometothejungle.com"
NEW_PATH = re.compile(r"^/board/([\w-]+)/job/([0-9a-f-]{36})(?:/apply)?/?$")
API = "https://api.ats.welcometothejungle.com/api/v1/board/organizations/{org}/jobs/{job}"
NEW_SUBMIT = "[data-testid=submit-application-button]"
CAPTCHA = "iframe[title=reCAPTCHA]"
CHALLENGE = "iframe[src*='recaptcha/api2/bframe']"
FORM_TIMEOUT_MS = 30_000
UPLOAD_TIMEOUT_MS = 30_000
CAPTCHA_TIMEOUT_MS = 20_000
SUBMIT_TIMEOUT_MS = 90_000
YES_NO = ["Yes", "No"]
PHOTO = ["image/png", "image/jpeg", "image/gif"]  # the photo's input declares no type
# The newer form's personal fields, always drawn: (name, label, type, required)
PERSONAL = [
    ("firstname", "First name", FieldType.TEXT, True),
    ("lastname", "Last name", FieldType.TEXT, True),
    ("email", "Email address", FieldType.TEXT, True),
    ("linkedin_url", "LinkedIn profile URL", FieldType.URL, False),
    ("phone_number", "Phone number", FieldType.PHONE, False),
    ("personal_website_url", "Personal website URL", FieldType.URL, False),
]
QUESTION_KINDS = {"text": FieldType.TEXT, "boolean": FieldType.CHOICE, "document": FieldType.FILE}

# What each control holds, by name: a value, a select's option text, a box as Yes/No, a file's
# name; a newer form's boolean question, by its place among them, as its selected button.
_READ_BACK = """(form, controls) => {
  const out = {};
  const booleans = [...form.querySelectorAll("[data-testid=board-boolean-field-true]")]
    .map(b => b.parentElement);
  for (const c of controls) {
    if (c.kind === "boolean") {
      const selected = booleans[c.place]?.querySelector("[data-selected=true]");
      out[c.id] = selected?.textContent.trim() ?? "";
      continue;
    }
    const el = form.querySelector(`[name="${c.name}"]:not([type=hidden])`);
    if (!el) continue;
    if (c.kind === "select") out[c.id] = el.selectedOptions[0]?.textContent.trim() ?? "";
    else if (c.kind === "checkbox") out[c.id] = el.checked ? "Yes" : "No";
    else if (c.kind === "file") out[c.id] = el.files[0]?.name ?? "";
    else if (c.kind === "upload") {
      const done = el.closest("[data-testid^=file-input-]")?.querySelector("svg.name-check");
      out[c.id] = done?.parentElement.textContent.trim().replace(/ \\([^()]*\\)$/, "") ?? "";
    }
    else out[c.id] = el.value ?? "";
  }
  return out;
}"""


@dataclass
class _Control:
    field: Field
    kind: str  # text | select | checkbox | file | boolean | upload
    name: str  # the control's `name`
    place: int = 0  # a boolean question's place among the form's boolean questions


class WelcomeKit:
    name = "welcomekit"
    known_gaps: dict[str, Gap] = {}
    side_effects = [
        SideEffect("upload", "hiring_system", "the newer form uploads a file once it is attached"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._controls: list[_Control] = []
        self._new = False

    def detect(self, url: str, page: Page | None) -> bool:
        return _old(url) is not None or _new(url) is not None

    def posting(self, url: str) -> str:
        if old := _old(url):
            return f"{OLD_ORIGIN}{OLD_PATH}?o={old[0]}&j={old[1]}"
        if new := _new(url):
            return f"{NEW_ORIGIN}/board/{new[0]}/job/{new[1]}/apply"
        return url

    def request_read(self, url: str) -> list[Field] | None:
        try:
            if _old(url):
                controls = _old_controls(_get(self.posting(url)))
            elif new := _new(url):
                controls = _new_controls(json.loads(_get(API.format(org=new[0], job=new[1]))))
            else:
                return None
        except urllib.error.HTTPError, OSError, ValueError:
            return None
        return [c.field for c in controls] if controls else None

    def browser_read(self, page: Page) -> list[Field]:
        if new := _new(page.url):
            self._new = True
            if not urlsplit(page.url).path.rstrip("/").endswith("/apply"):
                page.goto(self.posting(page.url))
            api = API.format(org=new[0], job=new[1])
            data = page.evaluate(_FETCH, api)
            if data is None or not (controls := _new_controls(data)):
                raise Unsupported("posting_gone: the job is not published")
            try:
                page.locator(NEW_SUBMIT).wait_for(timeout=FORM_TIMEOUT_MS)
            except Timeout:
                raise Unsupported("posting_gone: no application form on the page") from None
            for c in controls:  # each declared question is drawn
                if c.kind == "boolean":
                    page.locator("[data-testid=board-boolean-field-true]").nth(c.place).wait_for(
                        state="attached", timeout=FORM_TIMEOUT_MS
                    )
                elif c.kind in ("text", "upload") and c.name.startswith("custom_questions"):
                    page.locator(f"[name='{c.name}']").wait_for(
                        state="attached", timeout=FORM_TIMEOUT_MS
                    )
        else:
            try:
                page.locator(OLD).wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
            except Timeout:
                raise Unsupported("posting_gone: no application form on the page") from None
            controls = _old_controls(page.content())
        self._controls = controls
        return [c.field for c in controls]

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        form = page.locator("form").first if self._new else page.locator(OLD)
        for c in self._controls:
            if c.field.id not in answers or c.field.unfillable:
                continue
            value = answers[c.field.id]
            value = value if isinstance(value, str) else value[0]
            control = form.locator(f"[name='{c.name}']:not([type=hidden])")
            match c.kind:
                case "select":
                    control.select_option(label=value)
                case "checkbox":  # drawn over by its iCheck box: its label toggles it
                    if control.is_checked() != (value == "Yes"):
                        form.locator(f"label[for='{control.get_attribute('id')}']").click()
                case "file":
                    control.set_input_files(value)
                case "upload":  # the file goes to storage at once; a ticked badge then names it
                    control.set_input_files(value)
                    name = value.rsplit("/", 1)[-1]
                    input_ = page.locator(f"input[name='{c.name}']")
                    widget = form.locator("[data-testid^=file-input-]").filter(has=input_)
                    badge = widget.locator("*:has(> svg.name-check)", has_text=name)
                    badge.first.wait_for(timeout=UPLOAD_TIMEOUT_MS)
                case "boolean":
                    testid = "true" if value == "Yes" else "false"
                    buttons = form.locator(f"[data-testid=board-boolean-field-{testid}]")
                    button = buttons.nth(c.place)
                    if button.get_attribute("data-selected") != "true":
                        button.click()
                case _:
                    control.fill(value)

    def read_back(self, page: Page) -> dict[str, Answer]:
        form = page.locator("form").first if self._new else page.locator(OLD)
        controls = [
            {"id": c.field.id, "name": c.name, "kind": c.kind, "place": c.place}
            for c in self._controls
            if not c.field.unfillable
        ]
        values = form.evaluate(_READ_BACK, controls)
        return {k: v for k, v in values.items() if v not in (None, "")}

    def submit(self, page: Page) -> str:
        """The older form: ticks the reCAPTCHA box, `Blocked` on an image challenge, posts the
        form and lands on the page after it, the evidence. The newer form: clicks Submit
        application; the evidence is the answer to the apply POST."""
        if self._new:
            return _submit_new(page)
        captcha = page.locator(CAPTCHA)
        if captcha.count():
            captcha.content_frame.locator("#recaptcha-anchor").click()
            token = page.locator("#g-recaptcha-response")
            try:
                page.wait_for_function(
                    "el => el.value !== ''", arg=token.element_handle(), timeout=CAPTCHA_TIMEOUT_MS
                )
            except Timeout:
                if page.locator(CHALLENGE).is_visible():
                    raise Blocked("captcha: reCAPTCHA image challenge") from None
                raise
        with page.expect_navigation(timeout=SUBMIT_TIMEOUT_MS):
            page.locator(f"{OLD} [type=submit]").click()
        if page.locator(OLD).count():
            raise RuntimeError(f"the form was not taken: {_errors(page)}")
        return page.url


# The newer form's job, fetched from inside the page so it is the browser that asks.
_FETCH = "url => fetch(url).then(r => r.ok ? r.json() : null)"


def _old(url: str) -> tuple[str, str] | None:
    """The organization and job references, from `welcomekit.co/candidates/new?o=..&j=..`."""
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    o, j = query.get("o", [""])[0], query.get("j", [""])[0]
    ok = OLD_HOST.match(parts.hostname or "") and parts.path.rstrip("/") == OLD_PATH
    return (o, j) if ok and o and j else None


def _new(url: str) -> tuple[str, str] | None:
    """The organization slug and the job id, from `ats.welcometothejungle.com/board/..`."""
    parts = urlsplit(url)
    match = NEW_PATH.match(parts.path)
    return (match[1], match[2]) if parts.hostname == NEW_HOST and match else None


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "atsfill"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return response.read().decode()


def _old_controls(source: str) -> list[_Control]:
    """The older form's controls, in the page's order; an unknown job's page has no form."""
    page = html.parse(source)
    form = page.find(lambda n: n.tag == "form" and n.attrs.get("id") == "new_candidate")
    if form is None:
        return []
    labels = {n.attrs["for"]: n for n in form.walk() if n.tag == "label" and "for" in n.attrs}
    out: list[_Control] = []
    for node in form.walk():
        name = node.attrs.get("name", "")
        kind = node.attrs.get("type", "text") if node.tag == "input" else node.tag
        if node.tag not in ("input", "select", "textarea") or not name.startswith("candidate["):
            continue
        if kind == "hidden" or node.closest(lambda n: "hidden" in n.classes()):
            continue
        field_id = _id(name)
        own = labels.get(node.attrs.get("id", ""))
        label = _text(own) if own is not None else _group_label(node, field_id)
        required = "required" in node.attrs
        match kind:
            case "text" | "email" | "textarea":
                field, kept = Field(field_id, label, FieldType.TEXT, required), "text"
            case "tel":
                field, kept = Field(field_id, label, FieldType.PHONE, required), "text"
            case "select":
                options = [
                    (o, " ".join(o.text().split()))
                    for o in node.walk()
                    if o.tag == "option" and o.attrs.get("value")
                ]
                default = next((t for o, t in options if "selected" in o.attrs), None)
                texts = [t for _, t in options]
                field = Field(field_id, label, FieldType.CHOICE, required, texts, default=default)
                kept = "select"
            case "file":
                accept = node.attrs.get("accept")
                types = accept.split(",") if accept else PHOTO if field_id == "image" else None
                field, kept = Field(field_id, label, FieldType.FILE, required, accept=types), "file"
            case "checkbox":
                field = Field(field_id, label, FieldType.CHOICE, required, YES_NO)
                kept = "checkbox"
            case _:
                reason = f"unhandled_rendering: {field_id} is a {kind}"
                field = Field(field_id, label, FieldType.TEXT, required, unfillable=reason)
                kept = "text"
        out.append(_Control(field, kept, name))
    return out


def _id(name: str) -> str:
    """`candidate[profile_attributes][firstname]` is `firstname`, `candidate[answers[0][answer]]`
    `answers.0`."""
    parts = re.findall(r"\w+", name)[1:]
    parts = [p for p in parts if p != "profile_attributes"]
    return ".".join(parts[:-1] if parts[0] == "answers" else parts)


def _group_label(node: html.Node, field_id: str) -> str:
    """An address part's label: its group's, with the part's placeholder; the country list,
    which has none and lists its countries in English, `Country`."""
    group = node.closest(lambda n: "address" in n.classes() and "form-group" in n.classes())
    label = group.find(lambda n: n.tag == "label") if group else None
    placeholder = node.attrs.get("placeholder", "Country" if node.tag == "select" else "")
    if label is None:
        return placeholder or field_id
    return f"{_text(label)}: {placeholder}" if placeholder else _text(label)


def _text(label: html.Node) -> str:
    return " ".join(label.text().split()).removesuffix("*").strip()


def _new_controls(data: dict) -> list[_Control]:
    """The newer form's controls from its job's JSON: the personal fields, the résumé, then the
    questions by their order; none for a job that is not published."""
    job = data.get("job") or {}
    if job.get("status") != "published":
        return []
    out = [_Control(Field(n, t, k, r), "text", n) for n, t, k, r in PERSONAL]
    cv = Field("cv", "Resume", FieldType.FILE, True, accept=[".pdf"])
    out.append(_Control(cv, "upload", "cv"))
    booleans = 0
    questions = sorted(job.get("questions") or [], key=lambda q: q.get("order") or 0)
    for n, q in enumerate(questions):
        name = f"custom_questions.{n}.answer"
        field_id, label, required = f"custom_questions.{n}", q["label"].strip(), q["required"]
        match q["type"]:
            case "boolean":
                field = Field(field_id, label, FieldType.CHOICE, required, YES_NO)
                out.append(_Control(field, "boolean", name, booleans))
                booleans += 1
            case "document":
                field = Field(field_id, label, FieldType.FILE, required, accept=[".pdf"])
                out.append(_Control(field, "upload", name))
            case "text":
                out.append(_Control(Field(field_id, label, FieldType.TEXT, required), "text", name))
            case other:
                reason = f"unhandled_rendering: {field_id} is a {other} question"
                field = Field(field_id, label, FieldType.TEXT, required, unfillable=reason)
                out.append(_Control(field, "text", name))
    return out


def _submit_new(page: Page) -> str:
    new = _new(page.url)
    assert new is not None
    path = urlsplit(API.format(org=new[0], job=new[1])).path + "/apply"
    try:
        with page.expect_response(
            lambda r: r.request.method == "POST" and urlsplit(r.url).path == path,
            timeout=SUBMIT_TIMEOUT_MS,
        ) as applied:
            page.locator(NEW_SUBMIT).click()
    except Timeout:
        raise RuntimeError(f"nothing posted: {_errors(page)}") from None
    response = applied.value
    if not response.ok:
        raise RuntimeError(f"apply answered {response.status}: {response.text()[:200]}")
    return f"{response.url} {response.status}"


def _errors(page: Page) -> str:
    shown = page.locator(".help-block:visible, .error:visible, [role=alert]:visible")
    return " ".join(" ".join(shown.all_inner_texts()).split())[:200] or "no error shown"
