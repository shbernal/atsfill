"""Gupy (`<employer>.gupy.io/jobs/<id>`), as docs/adapters/gupy.md observes.

Applying needs an account, one for the whole system, that atsfill signs in to; its sign-up asks
more than `login` takes. Apply opens the application's register step: the account's resume,
filled once and shared by every application, then the posting's Additional information and the
employer's questions, which the page lists from `question-forms`. A fill saves Additional
information; the questions are saved by submit, for good.
"""

import re
import time
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser
from atsfill.core.adapter import Blocked, Gap, NotTaken, Unsupported, known_gap
from atsfill.core.browser import Locator, Page, Request, Response, Timeout
from atsfill.core.results import Answer, Field, FieldType, Is, SideEffect

HOST = re.compile(r"^([a-z0-9-]+)\.gupy\.io$")
JOB = re.compile(r"^/(?:candidates/)?jobs/(\d+)")
API = "https://private-api.gupy.io"
ACCOUNT = f"{API}/authentication/candidate/account/current"
LOGIN = "https://login.gupy.io/candidates/signin"
FORMS = re.compile(r"/question-forms/candidates/applications/\d+/steps/\d+/forms$")
ADDITIONAL = "input[name=radioGroupIsIndicatedTitle]"
ANSWER_NOW = "button:has-text('Answer now')"  # Additional information saved on an earlier visit
QUESTIONS = "[name^=question-], [name^=checkbox-], textarea, input[id^=input-]"
STEP_TIMEOUT_S = 40
STEP_POLL_MS = 500
RESUME_WAIT_S = 15
SAVE_TIMEOUT_MS = 30_000
SIGN_IN_TIMEOUT_MS = 30_000
FORMS_TIMEOUT_S = 10
YES_NO = ["Yes", "No"]
TYPES = {
    "SELECT": FieldType.CHOICE,
    "MULTI_SELECT": FieldType.MULTI_CHOICE,
    "TEXT": FieldType.TEXT,
    "TEXT_AREA": FieldType.TEXT,
}

# Additional information: a radio group by its name, under its fieldset's legend; a text input.
_READ_ADDITIONAL = """() => {
  const groups = {};
  for (const e of document.querySelectorAll("main input")) {
    if (e.type === "radio") {
      const g = groups[e.name] ??= {id: e.name, kind: "radio", options: [], value: null,
        label: e.closest("fieldset")?.querySelector("legend")?.textContent ?? e.name};
      const text = e.labels[0]?.innerText.trim() ?? e.value;
      g.options.push(text);
      if (e.checked) g.value = text;
    } else if (e.type === "text" && e.offsetParent !== null) {
      groups[e.name || e.id] = {id: e.name || e.id, kind: e.getAttribute("role") || "text",
        label: e.labels[0]?.innerText.trim() ?? e.name, options: null, value: e.value || null};
    }
  }
  return Object.values(groups).map(g => ({...g, label: g.label.replace(/\\s+/g, " ").trim()}));
}"""

# The employer's questions, by question id: the checked labels, or the text typed.
_READ_QUESTIONS = """(ids) => Object.fromEntries(ids.map(([id, title]) => {
  const radios = [...document.querySelectorAll(`input[name="question-${id}"]`)];
  const boxes = [...document.querySelectorAll(`input[name^="checkbox-${id}-"]`)];
  const picked = [...radios, ...boxes].filter(e => e.checked)
    .map(e => e.labels[0]?.innerText.trim());
  if (boxes.length) return [`question-${id}`, picked];
  if (radios.length) return [`question-${id}`, picked[0] ?? null];
  const text = [...document.querySelectorAll("textarea, input[type=text]")]
    .find(e => e.name === title || e.id === `input-${title}`);
  return [`question-${id}`, text?.value ?? null];
}))"""


class Gupy:
    name = "gupy"
    per_employer = False
    known_gaps = {"resume": Gap.FLOW}
    side_effects = [
        SideEffect("load", "employer", "opening apply creates the application, incomplete"),
        SideEffect("fill", "hiring_system", "a fill saves the Additional information"),
        SideEffect("submit", "employer", "the answers are saved for good, the application sent"),
    ]

    def __init__(self) -> None:
        self._questions: dict[str, dict[str, Any]] = {}
        self._fields: dict[str, Field] = {}
        self._additional: dict[str, Answer] = {}

    def detect(self, url: str, page: Page | None) -> bool:
        parts = urlsplit(url)
        return bool(HOST.match(parts.hostname or "")) and bool(JOB.match(parts.path))

    def posting(self, url: str) -> str:
        parts = urlsplit(url)
        match = JOB.match(parts.path)
        return f"https://{parts.hostname}/jobs/{match[1]}" if match else url

    def account_scope(self, url: str) -> None:
        return None

    def login_url(self, scope: str | None) -> str:
        return LOGIN

    def signed_in(self, request: Request, scope: str | None) -> bool:
        """The account endpoint answers the account, 401 to a visitor; without an Origin it
        answers 500."""
        return request.get(ACCOUNT, headers={"origin": "https://login.gupy.io"}).status == 200

    def sign_in(self, page: Page, email: str, password: str) -> None:
        form = page.locator("main, body").first
        try:
            form.locator("input[type=password]").wait_for(timeout=SIGN_IN_TIMEOUT_MS)
        except Timeout:
            raise Unsupported(f"unhandled_rendering: no sign-in form on {page.url}") from None
        form.locator("input:not([type=password]):not([type=hidden])").first.fill(email)
        form.locator("input[type=password]").fill(password)
        on_form = page.url
        browser.click(page.locator("button:has-text('Access account')"))
        try:
            page.wait_for_url(lambda url: url != on_form, timeout=SIGN_IN_TIMEOUT_MS)
        except Timeout:
            said = page.locator("main").inner_text().strip().replace("\n", " ")
            raise Blocked(f"sign_in: the sign-in stayed on its page: {said[:200]}") from None

    def browser_read(self, page: Page) -> list[Field]:
        """Opens apply and walks to the posting's Additional information, the employer's
        questions listed from the page's `question-forms` answer."""
        forms: list[Response] = []

        def on_response(response: Response) -> None:
            if FORMS.search(urlsplit(response.url).path) and response.request.method == "GET":
                forms.append(response)

        parts = urlsplit(page.url)
        match = JOB.match(parts.path)
        if not match:
            raise RuntimeError(f"{page.url} is not a Gupy job")
        page.on("response", on_response)
        try:
            page.goto(f"https://{parts.hostname}/candidates/jobs/{match[1]}/apply")
            self._walk(page)
            deadline = time.monotonic() + FORMS_TIMEOUT_S
            while not forms and time.monotonic() < deadline:
                page.wait_for_timeout(STEP_POLL_MS)
        finally:
            page.remove_listener("response", on_response)
        fields = [_additional(raw) for raw in page.evaluate(_READ_ADDITIONAL)]
        questions = (forms[-1].json().get("questionForm") or {}).get("questions") if forms else []
        self._questions = {f"question-{q['questionId']}": q for q in questions or []}
        fields += [_question(q) for q in self._questions.values()]
        self._fields = {f.id: f for f in fields if not f.unfillable}
        return fields

    def _walk(self, page: Page) -> None:
        """Past the interstitials to Additional information: the dialog asking to confirm the
        account's contact details, Complete resume, Continue Application, Continue."""
        deadline, disabled_since = time.monotonic() + STEP_TIMEOUT_S, None
        while time.monotonic() < deadline:
            if "/candidates/signin" in page.url:
                raise Blocked("login_required: run `atsfill login gupy`")
            if page.locator(f"{ADDITIONAL}, {ANSWER_NOW}").count():
                return
            for name in ("Remind me later", "Complete resume", "Continue Application", "Continue"):
                button = page.locator(f"button:has-text('{name}'):visible").first
                if not button.count():
                    continue
                if button.is_enabled():
                    disabled_since = None
                    browser.click(button)
                # Continue Application is disabled while the resume loads, for good when the
                # resume is incomplete
                elif (disabled_since := disabled_since or time.monotonic()) < (
                    time.monotonic() - RESUME_WAIT_S
                ):
                    detail = "the account's resume is incomplete; complete it once in Gupy"
                    raise Unsupported(known_gap(self, "resume", detail))
                break
            page.wait_for_timeout(STEP_POLL_MS)
        raise Unsupported(f"unhandled_rendering: no Additional information on {page.url}")

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        """Fills Additional information and, where the posting has questions, saves it and
        opens them; the questions are filled and left unsaved for submit."""
        for field_id, answer in answers.items():
            if field_id not in self._questions:
                _set(page, self._fields[field_id], field_id, answer)
        self._additional = self._read_additional(page)
        if not self._questions:
            return
        if page.locator(ADDITIONAL).count():
            browser.click(page.locator("button:has-text('Save and continue')").first)
        answer_now = page.locator(ANSWER_NOW).first
        answer_now.wait_for(timeout=SAVE_TIMEOUT_MS)
        browser.click(answer_now)
        page.locator(QUESTIONS).first.wait_for(timeout=SAVE_TIMEOUT_MS)
        for field_id, answer in answers.items():
            if (question := self._questions.get(field_id)) is not None:
                _answer(page, self._fields[field_id], question, answer)

    def _read_additional(self, page: Page) -> dict[str, Answer]:
        values = {raw["id"]: raw["value"] for raw in page.evaluate(_READ_ADDITIONAL)}
        return {k: v for k, v in values.items() if k in self._fields and v}

    def read_back(self, page: Page) -> dict[str, Answer]:
        """Additional information as it was saved, or as the page holds it; the questions'."""
        additional = self._additional or self._read_additional(page)
        ids = [[q["questionId"], q["title"]] for q in self._questions.values()]
        values = page.evaluate(_READ_QUESTIONS, ids) if page.locator(QUESTIONS).count() else {}
        return {**additional, **{k: v for k, v in values.items() if v not in (None, "", [])}}

    def submit(self, page: Page) -> str | None:
        """Save and continue on the last page: the questions, or Additional information."""
        with page.expect_response(
            lambda r: r.request.method in ("PUT", "POST", "PATCH") and API in r.url,
            timeout=SAVE_TIMEOUT_MS,
        ) as saved:
            browser.click(page.locator("button:has-text('Save and continue')").first)
        response = saved.value
        if response.ok:
            return f"{response.request.method} {urlsplit(response.url).path} {response.status}"
        raise RuntimeError(f"{response.request.method} {response.url} answered {response.status}")


def _additional(raw: dict[str, Any]) -> Field:
    """A radio group is a choice; the source list, a combobox, is not filled."""
    name, label = raw["id"], raw["label"]
    match raw["kind"]:
        case "radio":
            options, value = raw["options"], raw["value"]
            return Field(name, label, FieldType.CHOICE, True, options, prefilled=value)
        case "text":
            return Field(name, label, FieldType.TEXT, "optional" not in label.lower())
    reason = f"unhandled_rendering: {label!r} is a {raw['kind']}"
    return Field(name, label, FieldType.TEXT, "optional" not in label.lower(), unfillable=reason)


def _question(q: dict[str, Any]) -> Field:
    field_id, kind = f"question-{q['questionId']}", TYPES.get(q["type"])
    options = [o["label"] for o in q.get("options") or []] or None
    shown = None
    if q.get("parentQuestionId"):
        shown = Is(f"question-{q['parentQuestionId']}", q.get("conditionalTriggerOptions") or None)
    if kind is None:
        reason = f"unhandled_rendering: {q['title']!r} is a {q['type']} question"
        return Field(field_id, q["title"], FieldType.TEXT, q["required"], unfillable=reason)
    return Field(
        field_id, q["title"], kind, bool(q["required"]),
        options if kind is not FieldType.TEXT else None, prefilled=q.get("answer") or None,
        shown_when=shown,
    )  # fmt: skip


def _label(page: Page, selector: str, text: str) -> Locator:
    """The label, wrapping it, of the input `selector` matches whose text is `text`."""
    inputs = page.locator(selector)
    for n in range(inputs.count()):
        if inputs.nth(n).evaluate("e => e.labels[0]?.innerText.trim()") == text:
            return inputs.nth(n).locator("xpath=ancestor::label[1]")
    raise NotTaken(f"no option {text!r} in {selector}")


def _set(page: Page, field: Field, field_id: str, answer: Answer) -> None:
    match field.type, answer:
        case FieldType.CHOICE, str():
            browser.click(_label(page, f'main input[name="{field_id}"]', answer))
        case FieldType.TEXT, str():
            page.locator(f'main input[name="{field_id}"], main input[id="{field_id}"]').fill(answer)
        case kind, _:
            raise Unsupported(f"unhandled_rendering: cannot fill {field_id}, a {kind}")


def _answer(page: Page, field: Field, question: dict[str, Any], answer: Answer) -> None:
    qid = question["questionId"]
    match field.type, answer:
        case FieldType.CHOICE, str():
            browser.click(_label(page, f'input[name="question-{qid}"]', answer))
        case FieldType.MULTI_CHOICE, list():
            for option in answer:
                box = _label(page, f'input[name^="checkbox-{qid}-"]', option)
                if not box.locator("input").is_checked():
                    browser.click(box)
        case FieldType.TEXT, str():
            title = question["title"]
            target = page.locator(f'textarea[name="{_css(title)}"], input[name="{_css(title)}"]')
            if not target.count():
                target = page.locator(f'[id="input-{_css(title)}"]')
            target.first.fill(answer)
        case kind, _:
            raise Unsupported(f"unhandled_rendering: cannot fill question-{qid}, a {kind}")


def _css(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
