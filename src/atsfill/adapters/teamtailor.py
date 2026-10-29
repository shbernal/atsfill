"""Teamtailor (`<employer>.teamtailor.com` and career sites on the employer's domain), as
docs/adapters/teamtailor.md observes.

The form posts nothing before submit except the files, which go to Teamtailor's storage as
soon as they are picked. The read-back is the form's `FormData`, and for a video question the
video Teamtailor holds under the id the form keeps.
"""

import re
import time
import unicodedata
import urllib.request
from dataclasses import replace
from pathlib import PurePath
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser, html, matching
from atsfill.core.adapter import Blocked, Gap, NotTaken, Unsupported, known_gap
from atsfill.core.browser import HTTP_TIMEOUT_S, Locator, Page, Response
from atsfill.core.html import Node
from atsfill.core.results import Answer, Field, FieldType, Is, SideEffect

FORM = "#job-application-form"
# A custom question, or a qualifying one: the rest of the form stays disabled until it is
# answered with the answer the employer requires.
QUESTION = ".question, [data-careersite--form-target=qualifyingAnswers]"
LOCKED = "[data-careersite--form-target=formContentWrapper].cursor-not-allowed"
LOCATION = "candidate[location][query]"
VIDEO = "[data-controller~='questions--tt-video-answer']"
UPLOAD = "forms--inputs--upload"  # a drop zone's controller
CAREER_SITE = "a[href*='//www.teamtailor.com'], script[src*='teamtailor-cdn.com/assets/careersite']"
JOB = re.compile(r"/jobs/(\d+)[^/]*")
EMAIL_LINK = "/applications/email_verification_needed"
SENT = re.compile(rf"/applications/[^/]+/thanks|{EMAIL_LINK}")
SUBMIT_TIMEOUT_MS = 90_000
FOLLOW_UP_TIMEOUT_MS = 1_000
UPLOAD_TIMEOUT_S = 60
UPLOAD_TIMEOUT_MS = UPLOAD_TIMEOUT_S * 1000
UPLOAD_POLL_MS = 500
KEY_DELAY_MS = 30
QUESTION_TYPES = {
    "text": FieldType.TEXT,
    "number": FieldType.TEXT,
    "date": FieldType.DATE,
    "range": FieldType.CHOICE,
    "boolean": FieldType.CHOICE,
    "choice": FieldType.CHOICE,
    "choices": FieldType.MULTI_CHOICE,
}
STANDARD_TYPES = {
    "text": FieldType.TEXT,
    "email": FieldType.TEXT,
    "textarea": FieldType.TEXT,
    "tel": FieldType.PHONE,
    "location": FieldType.TYPEAHEAD,
    "checkbox": FieldType.CHOICE,
    "choices": FieldType.MULTI_CHOICE,
    "file": FieldType.FILE,
}
CHECKBOX = ["Yes", "No"]

# A standard field the form shows but no answer is asked for.
NOT_ASKED = re.compile(r"linkedin|referrer|social_image")
STANDARD_KINDS = ("text", "email", "tel", "textarea", "checkbox", "choices", "file", "location")
ANSWER_KIND = re.compile(r"\]\[(\w+)\](\[\])?$")

# Whether each custom or qualifying question shows, in the order `_read` lists them.
_VISIBLE = "(form, QUESTION) => [...form.querySelectorAll(QUESTION)].map(q => q.checkVisibility())"
# What the form will send: each field's value, a choice as its option's text, a file as the
# uploaded file's name, an address once a suggestion is picked, a video question as the id of
# the video it holds.
_READ_BACK = """(form, QUESTION) => {
  const data = new FormData(form);
  const text = e => {
    const label = e.labels?.[0];
    if (label) return label.textContent.replace(/\\s+/g, " ").trim();
    const box = e.closest(`${QUESTION}, fieldset`);
    const item = box?.querySelector(`[role^=menuitem][data-value="${e.value}"]`);
    return item?.dataset.searchText ?? e.value;
  };
  const file = url => url ? decodeURIComponent(new URL(url).pathname.split("/").pop()) : null;
  const out = {};
  for (const el of form.querySelectorAll("input[type=checkbox][name^='candidate['][value='1']")) {
    out[el.name] = data.getAll(el.name).includes("1") ? "Yes" : "No";
  }
  for (const el of form.querySelectorAll("input[type=checkbox][name^='candidate['][name$='[]']")) {
    out[el.name] = [...form.querySelectorAll(`[name="${el.name}"]:checked`)].map(text);
  }
  for (const name of ["first_name", "last_name", "email", "phone"]) {
    out[`candidate[${name}]`] = data.get(`candidate[${name}]`);
  }
  for (const [name, value] of data) {
    if (/^candidate\\[\\w+_remote_url\\]$/.test(name)) out[name] = file(value);
    else if (/^candidate\\[uploads_attributes\\]\\[\\d+\\]\\[file_remote_url\\]$/.test(name)) {
      out["candidate[file_remote_url]"] = file(value);  // "Additional files", one per upload
    } else if (/cover_letter/.test(name)) out[name] = value;
  }
  if (data.get("candidate[location][place_id]")) {
    out["candidate[location][query]"] = data.get("candidate[location][query]");
  }
  for (const q of form.querySelectorAll(QUESTION)) {
    const id = `question:${q.querySelector("input[name$='[question_id]']")?.value}`;
    const checked = [...q.querySelectorAll("input:checked")];
    const named = q.querySelector("[name^='candidate[answers_attributes]']:not([type=hidden])");
    const video = q.querySelector("[name$='[picked_video_attributes][video_id]']");
    if (video) out[id] = data.get(video.name);
    else if (q.querySelector("input[type=checkbox]")) out[id] = checked.map(text);
    else if (q.querySelector("input[type=radio]")) out[id] = checked[0] ? text(checked[0]) : null;
    else if (named) out[id] = named.value;
  }
  return out;
}"""


class Teamtailor:
    name = "teamtailor"
    known_gaps = {"file_question": Gap.RENDERING}
    side_effects = [
        SideEffect("upload", "hiring_system", "a picked file is stored by Teamtailor at once"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._fields: dict[str, Field] = {}
        self._qualifying: set[str] = set()
        self._follow_ups: set[str] = set()
        self._uploads: dict[str, str] = {}  # file field id: the answer's file name
        self._videos: dict[str, tuple[str, str]] = {}  # video field id: its video id, its URL
        self._form: str | None = None  # the form `open_form` loaded

    def detect(self, url: str, page: Page | None) -> bool:
        """A `*.teamtailor.com` job, or a job page with Teamtailor's "powered by" link or its
        career site script."""
        if not JOB.search(urlsplit(url).path):
            return False
        if (urlsplit(url).hostname or "").endswith(".teamtailor.com"):
            return True
        return page is not None and page.locator(CAREER_SITE).count() > 0

    def posting(self, url: str) -> str:
        parts = urlsplit(url)
        match = JOB.search(parts.path)
        assert match, url
        return f"https://{parts.hostname}/jobs/{match[1]}"

    def open_form(self, page: Page, url: str) -> None:
        """Opens the form's own page, never the overlay nor "Apply with LinkedIn"."""
        parts = urlsplit(url)
        job = JOB.search(parts.path)
        assert job, url  # detected on a job URL
        form = f"{parts.scheme}://{parts.netloc}{parts.path[: job.end()]}/applications/new"
        page.goto(form)
        self._form = form

    def browser_read(self, page: Page) -> list[Field]:
        if self._form is None:  # a page claimed once loaded: a custom domain
            self.open_form(page, page.url)
        assert self._form
        opened, self._form = self._form, None
        if urlsplit(page.url).hostname != urlsplit(opened).hostname:
            raise Unsupported(f"external_step: the job applies on another site ({page.url})")
        form = page.locator(FORM)
        try:
            form.wait_for()
        except browser.Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        return self._listed(form.evaluate("f => f.outerHTML"))

    def request_read(self, url: str) -> list[Field] | None:
        """The form's own page, on a `*.teamtailor.com` host: a custom domain is known only by
        its page."""
        parts = urlsplit(url)
        job = JOB.search(parts.path)
        if not job or not (parts.hostname or "").endswith(".teamtailor.com"):
            return None
        new = f"https://{parts.hostname}{parts.path[: job.end()]}/applications/new"
        request = urllib.request.Request(new, headers={"User-Agent": "atsfill"})
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
                if urlsplit(response.url).hostname != parts.hostname:
                    return None  # applies on another site
                page = html.parse(response.read().decode())
        except OSError, ValueError:
            return None
        form = page.find(lambda n: n.tag == "form" and n.attrs.get("id") == FORM[1:])
        return self._listed(form) if form else None

    def _listed(self, form: str | html.Node) -> list[Field]:
        if isinstance(form, str):
            form = html.parse(form)
        fields, self._qualifying, self._follow_ups = [], set(), set()
        for raw in _read(form):
            if raw.get("qualifying"):
                self._qualifying.add(raw["id"])
            field = self._field(raw)
            if shown_by := raw.get("shownBy"):  # a follow-up of an earlier question
                self._follow_ups.add(raw["id"])
                field = replace(field, shown_when=Is(shown_by["id"], [shown_by["option"]]))
            fields.append(field)
        self._fields = {f.id: f for f in fields}
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        form = page.locator(FORM)
        self._videos = {}
        for field_id in [i for i in answers if i in self._qualifying]:
            self._fill(form, field_id, answers[field_id])
        if form.locator(LOCKED).count():
            raise Unsupported("not_qualified: the qualifying answers leave the form locked")
        for field_id in [i for i in answers if i not in self._qualifying]:
            if field_id in self._follow_ups and not self._shown(form, field_id):
                continue  # not asked: the read-back leaves it out
            self._fill(form, field_id, answers[field_id])
        self._uploads = {
            i: PurePath(a).name
            for i, a in answers.items()
            if self._fields[i].type is FieldType.FILE and isinstance(a, str)
        }
        self._wait_for_uploads(form)

    def shown_fields(self, page: Page) -> list[str]:
        form = page.locator(FORM)
        raws = _read(html.parse(form.evaluate("f => f.outerHTML")))
        visible = iter(form.evaluate(_VISIBLE, QUESTION))
        return [raw["id"] for raw in raws if not raw.get("question") or next(visible)]

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = page.locator(FORM).evaluate(_READ_BACK, QUESTION)
        for field_id, (video_id, url) in self._videos.items():
            if values.get(field_id) == video_id:  # the name Teamtailor holds the video under
                values[field_id] = page.evaluate(_VIDEO, url)
        for field_id, name in self._uploads.items():
            if values.get(field_id) == _stored_name(name):
                values[field_id] = name
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        browser.click(page.locator(f"{FORM} [type=submit]"))
        page.wait_for_url(SENT, timeout=SUBMIT_TIMEOUT_MS, wait_until="domcontentloaded")
        if page.url.split("?")[0].endswith(EMAIL_LINK):
            raise Blocked("email_link: click the link emailed to the applicant to complete it")
        return page.url

    def _fill(self, form: Locator, field_id: str, answer: Answer) -> None:
        page, field, host = form.page, self._fields[field_id], self._host(form, field_id)
        match field.type, answer:
            case FieldType.FILE, str() if host.locator(VIDEO).count():
                self._videos[field_id] = _upload_video(page, host, answer)
            case FieldType.FILE, str():
                host.locator("input[type=file]").set_input_files(answer)
            case FieldType.CHOICE, str() if host.locator("input[type=checkbox]").count():
                box = host.locator("input[type=checkbox]")
                label = page.locator(f"label[for='{box.get_attribute('id')}']")
                if box.is_checked() != (answer == "Yes"):  # an empty label has no box to click
                    browser.click(label if label.is_visible() else box)
            case FieldType.CHOICE, str() if host.locator("input[type=range]").count():
                host.locator("input[type=range]").fill(answer)
            case FieldType.TYPEAHEAD, str():
                _suggest(field, host, answer)
            case FieldType.CHOICE | FieldType.MULTI_CHOICE, _:
                for value in [answer] if isinstance(answer, str) else answer:
                    _pick(host, value)
                trigger = host.locator("button[aria-haspopup][aria-expanded=true]")
                if trigger.count():  # a multiple choice menu stays open over the next fields
                    browser.click(trigger.first)
            case FieldType.TEXT, str() if host.locator("input[type=number]").count():
                if re.fullmatch(r"-?\d+(\.\d+)?", answer):  # else left empty: a mismatch
                    host.locator("input[type=number]").first.fill(answer)
            case FieldType.TEXT | FieldType.DATE | FieldType.PHONE, str():
                box = host.locator("input:not([type=hidden]):not([type=search]), textarea")
                box.first.fill(answer)
            case kind, _:
                raise Unsupported(
                    f"unhandled_rendering: cannot fill {field_id}, a {kind}, with {answer!r}"
                )

    def _field(self, raw: dict[str, Any]) -> Field:
        field_id, label, required = raw["id"], raw["label"], raw["required"]
        match raw["kind"]:
            case "video" if raw.get("question"):  # a clip is uploaded, never recorded
                return Field(field_id, label, FieldType.FILE, required, accept=raw.get("accept"))
            case "file" if raw.get("question"):
                gap = known_gap(self, "file_question", f"{field_id} is a question's file drop zone")
                return Field(field_id, label, FieldType.FILE, required, unfillable=gap)
        kind = (QUESTION_TYPES if raw.get("question") else STANDARD_TYPES).get(raw["kind"])
        if kind is None:
            reason = f"unhandled_rendering: {field_id} is a Teamtailor {raw['kind']} question"
            return Field(field_id, label, FieldType.TEXT, required, unfillable=reason)
        options = CHECKBOX if raw["kind"] == "checkbox" else raw.get("options")
        return Field(
            field_id, label, kind, required, options, raw.get("maxLength"), raw.get("accept")
        )

    def _shown(self, form: Locator, field_id: str) -> bool:
        """Whether a follow-up question shows, once the answer before it has opened it."""
        try:
            self._host(form, field_id).wait_for(timeout=FOLLOW_UP_TIMEOUT_MS)
        except browser.Timeout:
            return False
        return True

    def _host(self, form: Locator, field_id: str) -> Locator:
        """The element holding a field's inputs: its question, else its input's container."""
        if field_id.startswith("question:"):
            qid = field_id.partition(":")[2]
            return form.locator(QUESTION).filter(
                has=form.page.locator(f"input[name$='[question_id]'][value='{qid}']")
            )
        if field_id.endswith("[]"):
            return form.locator("fieldset").filter(has=form.page.locator(f"[name='{field_id}']"))
        if field_id == LOCATION:
            return form.locator("[data-controller~='forms--inputs--location']")
        if field_id.endswith("_remote_url]"):
            dom_id = "candidate_" + field_id[len("candidate[") : -1]
            return form.locator(f"[data-controller~='{UPLOAD}']").filter(
                has=form.page.locator(f"#{dom_id}")
            )
        return form.locator(f"[name='{field_id}']").first.locator("xpath=..")

    def _wait_for_uploads(self, form: Locator) -> None:
        deadline = time.monotonic() + UPLOAD_TIMEOUT_S
        while self._uploads and time.monotonic() < deadline:
            held = form.evaluate(_READ_BACK, QUESTION)
            stored = {
                i: self._videos[i][0] if i in self._videos else _stored_name(n)
                for i, n in self._uploads.items()
            }
            if all(held.get(i) == s for i, s in stored.items()):
                return
            form.page.wait_for_timeout(UPLOAD_POLL_MS)


def _stored_name(name: str) -> str:
    """The name Teamtailor stores a file under: ASCII, lowercase, each run of other
    characters than letters, digits, `.` and `-` one `_`."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9.\-]+", "_", ascii_name.lower())


def _upload_video(page: Page, host: Locator, clip: str) -> tuple[str, str]:
    """Uploads a clip through a video question's "Upload video" input, never its recorder.
    Teamtailor stores it as a video and the form keeps the video's id; returns that id and the
    URL the video is read back from."""

    def created(response: Response) -> bool:
        path = urlsplit(response.url).path
        return response.request.method == "POST" and path.endswith("/videos")

    try:
        with page.expect_response(created, timeout=UPLOAD_TIMEOUT_MS) as response:
            host.locator("input[type=file]").set_input_files(clip)
    except browser.Timeout:
        raise NotTaken(f"the video question took no video from {PurePath(clip).name!r}") from None
    video = response.value.json()
    return str(video["id"]), f"{response.value.url}/{video['sgid']}"


# The file name Teamtailor holds a video under.
_VIDEO = """url => fetch(url, {headers: {Accept: "application/json"}})
  .then(r => r.ok ? r.json() : {}).then(v => v.file_file_name ?? null)"""


def _suggest(field: Field, host: Locator, answer: str) -> None:
    """Types the address key by key, which loads the suggestions, and picks the first that
    matches it: the form keeps an address only once a suggestion is picked."""
    box = host.locator("input[role=combobox]")
    box.fill("")
    box.press_sequentially(answer, delay=KEY_DELAY_MS)
    options = host.locator("[role=option]")
    options.first.wait_for()
    for i in range(options.count()):
        if matching.matches(field, answer, options.nth(i).get_attribute("data-description") or ""):
            browser.click(options.nth(i))
            return
    raise NotTaken(f"no suggestion for {answer!r} in {field.id}")


def _pick(question: Locator, value: str) -> None:
    """Checks the option whose text is `value`, a radio or checkbox label or a dropdown item."""
    label = question.locator("label:visible").filter(
        has_text=re.compile(rf"^\s*{re.escape(value)}\s*$")
    )
    if label.count():
        browser.click(label.first)
        return
    trigger = question.locator("button[aria-haspopup]").first
    if trigger.get_attribute("aria-expanded") != "true":
        browser.click(trigger)
    items = question.locator("[role^=menuitem]")
    for item in items.all():  # the text may hold a quote, which a selector would have to escape
        if item.get_attribute("data-search-text") == value:
            browser.click(item)
            return


def _is_question(node: Node) -> bool:
    """A custom question, or a qualifying one (QUESTION)."""
    return (
        "question" in node.classes()
        or node.attrs.get("data-careersite--form-target") == "qualifyingAnswers"
    )


def _label_text(node: Node | None) -> str:
    """A label's text, without its required mark and its screen-reader text."""
    if node is None:
        return ""
    parts = [
        c if isinstance(c, str) else _label_text(c)
        for c in node.children
        if isinstance(c, str) or (c.tag != "sup" and "sr-only" not in c.classes())
    ]
    return "".join(parts)


def _plain(node: Node | None) -> str:
    return " ".join(_label_text(node).split())


def _required(node: Node | None) -> bool:
    return (
        node is not None
        and node.find(lambda n: n.tag == "sup" and "data-asterisk" in n.attrs) is not None
    )


def _is_upload(node: Node) -> bool:
    return UPLOAD in node.attrs.get("data-controller", "").split()


def _upload(box: Node) -> list[dict[str, Any]]:
    """A drop zone: its label names its field, `candidate_<name>`. Its `input[type=file]` is
    added by the page's script, so the server's HTML holds none."""
    label = box.find(lambda n: n.tag == "label" and n.attrs.get("for", "").startswith("candidate_"))
    if label is None:
        return []
    field_id = f"candidate[{label.attrs['for'].removeprefix('candidate_')}]"
    if NOT_ASKED.search(field_id):
        return []
    accept = box.attrs.get(f"data-{UPLOAD}-accepted-files-value")
    return [{"id": field_id, "kind": "file", "label": _plain(label), "required": _required(label),
             "options": None, "maxLength": None,
             "accept": accept.split(",") if accept else None}]  # fmt: skip


def _read(form: Node) -> list[dict[str, Any]]:
    """Every field of the form, described from its HTML. A custom question's id is
    `question:<Teamtailor question id>`; the others are the input's name."""
    nodes = form.walk()
    labels: dict[str, Node] = {}
    for n in nodes:
        if n.tag == "label" and n.attrs.get("for"):
            labels.setdefault(n.attrs["for"], n)

    def label_of(el: Node) -> Node | None:
        wrapping = el.parent.closest(lambda n: n.tag == "label") if el.parent else None
        if wrapping is not None and wrapping.attrs.get("for", el.attrs.get("id")) == el.attrs.get(
            "id"
        ):
            return wrapping
        return labels.get(el.attrs["id"]) if el.attrs.get("id") else None

    def menu_item(box: Node, value: str) -> Node | None:
        return box.find(
            lambda n: (
                n.attrs.get("role", "").startswith("menuitem")
                and n.attrs.get("data-value") == value
            )
        )

    def options(box: Node, inputs: list[Node]) -> list[str]:
        out = []
        for e in inputs:
            value = e.attrs.get("value", "")
            item = menu_item(box, value)
            out.append(
                _plain(label_of(e)) or (item.attrs.get("data-search-text") if item else "") or value
            )
        return out

    def kind_of(el: Node) -> str:
        return (
            "textarea" if el.tag == "textarea" else el.attrs.get("type", "text").lower() or "text"
        )

    def picks(box: Node) -> list[Node]:
        return [n for n in box.walk() if n.tag == "input" and kind_of(n) in ("radio", "checkbox")]

    def disabled(el: Node) -> bool:
        return (
            "disabled" in el.attrs
            or el.closest(lambda n: n.tag == "fieldset" and "disabled" in n.attrs) is not None
        )

    def question_id(q: Node) -> str | None:
        hidden = q.find(
            lambda n: n.tag == "input" and n.attrs.get("name", "").endswith("[question_id]")
        )
        return hidden.attrs.get("value") if hidden else None

    fields: list[dict[str, Any]] = []
    for el in nodes:
        if _is_upload(el) and not el.closest(_is_question):
            fields += _upload(el)
            continue
        if el.tag not in ("input", "textarea") or (
            kind_of(el) == "file" and el.closest(_is_upload)
        ):
            continue
        name = el.attrs.get("name", "")
        if el.closest(_is_question) or disabled(el) or any(f["id"] == name for f in fields):
            continue
        kind = kind_of(el)
        if kind != "file" and re.search(r"_remote_url\]$", name):  # an upload's address
            continue
        if name == LOCATION:
            kind = "location"
        elif kind == "checkbox" and name.endswith("[]"):
            kind = "choices"
        field_id = name
        if kind == "file":
            field_id = f"candidate[{el.attrs.get('id', '').removeprefix('candidate_')}]"
        if kind not in STANDARD_KINDS or not field_id or NOT_ASKED.search(field_id):
            continue
        box = el.closest(lambda n: n.tag == "fieldset") if kind == "choices" else None
        if box is not None:
            host = box.find(lambda n: n.tag == "legend")
        else:
            controller = el.closest(lambda n: "data-controller" in n.attrs)
            host = label_of(el) or (
                controller.find(lambda n: n.tag == "label") if controller else None
            )
        same = [n for n in nodes if n.attrs.get("name") == field_id]
        max_length = int(el.attrs.get("maxlength") or 0)
        accept = el.attrs.get("accept")
        fields.append({
            "id": field_id, "kind": kind, "label": _plain(host), "required": _required(host),
            "options": options(box, same) if box is not None else None,
            "maxLength": max_length if max_length > 0 else None,
            "accept": accept.split(",") if accept else None,
        })  # fmt: skip
    questions = [n for n in nodes if _is_question(n)]
    for q in questions:
        qualifying = "question" not in q.classes()
        named = q.find(
            lambda n: (
                n.attrs.get("name", "").startswith("candidate[answers_attributes]")
                and n.attrs.get("type") != "hidden"
            )
        )
        file = q.find(lambda n: n.tag == "input" and kind_of(n) == "file")
        name = named.attrs["name"] if named else ""
        if "picked_video" in name:
            kind = "video"
        elif (file is not None or q.find(_is_upload)) and named is None:
            kind = "file"
        else:
            kind = match[1] if (match := ANSWER_KIND.search(name)) else "unknown"
        listed = options(q, picks(q))
        if named is not None and kind_of(named) == "range":
            step = float(named.attrs.get("step") or 1) or 1
            value, top = float(named.attrs.get("min") or 0), float(named.attrs.get("max") or 0)
            while value <= top:
                listed.append(f"{value:g}")
                value += step
        menu = [
            n.attrs.get("data-search-text", "")
            for n in q.walk()
            if n.attrs.get("role", "").startswith("menuitem")
        ]
        shown_by = None
        if uuid := q.attrs.get("data-question-show-if-uuid"):
            parent = next((n for n in nodes if n.attrs.get("data-question-uuid") == uuid), None)
            value = q.attrs.get("data-question-show-if-value")
            pick = (
                next((e for e in picks(parent) if e.attrs.get("value") == value), None)
                if parent
                else None
            )
            if parent is not None and pick is not None:
                shown_by = {
                    "id": f"question:{question_id(parent)}",
                    "option": options(parent, [pick])[0],
                }
        # a video's accepted types are set by the page's script: none in the server's HTML
        accept = [a for a in (file.attrs.get("accept") or "").split(",") if a] if file else None
        fields.append({
            "id": f"question:{question_id(q)}", "kind": kind, "question": True,
            "qualifying": qualifying, "shownBy": shown_by,
            "label": _plain(q.find(lambda n: n.tag in ("legend", "label"))),
            "required": qualifying or q.attrs.get("data-question-mandatory") == "true",
            "accept": (accept or None) if kind == "video" else None,
            "options": listed or menu or None,
        })  # fmt: skip
    return fields
