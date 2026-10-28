"""Work at a Startup, Y Combinator's job board (`ycombinator.com/companies/<employer>/jobs/…`
and `workatastartup.com/jobs/<id>`), as docs/adapters/workatastartup.md observes.

Applying needs a Y Combinator account whose account profile is complete and visible: the form is
then a dialog on the job's Work at a Startup page, a message and the employer's questions.
Nothing is sent before Send, so the read-back is the dialog's state.
"""

import html
import json
import re
import secrets
import time
import urllib.request
from functools import cache
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser, otp
from atsfill.core.adapter import (
    Blocked,
    Gap,
    NotTaken,
    ProfileIncomplete,
    Refused,
    SignUpDetails,
    Unsupported,
)
from atsfill.core.browser import HTTP_TIMEOUT_S, Page, Request, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

YC = "www.ycombinator.com"
WAAS = "www.workatastartup.com"
YC_JOB = re.compile(r"^/companies/[^/]+/jobs/[^/?#]+")
WAAS_JOB = re.compile(r"^/jobs/(\d+)/?$")
ACCOUNT = "https://account.ycombinator.com/?continue=https%3A%2F%2Fwww.workatastartup.com%2F"
SIGN_UP = f"{ACCOUNT}&defaults%5BsignUpActive%5D=true"
CURRENT_USER = f"https://{WAAS}/user/current_user.json"
USERNAME = "#ycid-input"
PASSWORD = "#password-input"
DIALOG = "[role=dialog]"
MESSAGE = "message"
MIN_MESSAGE = 50  # Send stays disabled below it
MAX_LENGTH = {"text": 250, "url": 2000, "long_text": 5000}
ACCOUNT_TIMEOUT_MS = 20_000
ACCOUNT_TIMEOUT_S = 20
ACCOUNT_POLL_MS = 250
DIALOG_TIMEOUT_MS = 20_000
QUESTIONS_TIMEOUT_S = 10
QUESTIONS_POLL_MS = 250
SUBMIT_TIMEOUT_MS = 60_000

# Marks the dialog's message, each question's block and the relocation box, so each can be
# found again: the page names none of them. `questions` are [id, label] pairs.
_TAG = """(dialog, questions) => {
  const norm = s => s.replace(/\\s+/g, " ").trim();
  const blocks = [...dialog.querySelectorAll("p")];
  const found = [];
  for (const [id, label] of questions) {
    const p = blocks.find(p => [norm(label), norm(label) + " *"].includes(norm(p.textContent)));
    if (p) { p.parentElement.dataset.atsfillQuestion = id; found.push(id); }
  }
  const message = [...dialog.querySelectorAll("textarea")]
    .find(t => !t.closest("[data-atsfill-question]"));
  if (message) message.dataset.atsfillField = "message";
  const relocation = [...dialog.querySelectorAll("input[type=checkbox]")]
    .find(c => !c.closest("[data-atsfill-question]"));
  if (relocation) relocation.dataset.atsfillField = "open_to_relocation";
  return found;
}"""

_READ_BACK = """dialog => {
  const out = {};
  const message = dialog.querySelector("[data-atsfill-field=message]");
  if (message) out.message = message.value;
  for (const block of dialog.querySelectorAll("[data-atsfill-question]")) {
    const id = "question_" + block.dataset.atsfillQuestion;
    const text = block.querySelector("textarea");
    const label = e => e.closest("label").textContent.trim();
    if (text) out[id] = text.value;
    else if (block.querySelector("input[type=radio]")) {
      const picked = block.querySelector("input[type=radio]:checked");
      out[id] = picked ? label(picked) : "";
    } else out[id] = [...block.querySelectorAll("input[type=checkbox]:checked")].map(label);
  }
  return out;
}"""


class WorkAtAStartup:
    name = "workatastartup"
    per_employer = False
    asks = {"name"}  # split into the first and the last name
    known_gaps: dict[str, Gap] = {}
    side_effects = [
        SideEffect("sign_up", "hiring_system", "login --create creates a Y Combinator account"),
        SideEffect("submit", "employer", "the message and answers reach the employer's founders"),
    ]

    def __init__(self) -> None:
        self._questions: dict[str, dict[str, Any]] = {}

    def detect(self, url: str, page: Page | None) -> bool:
        parts = urlsplit(url)
        if parts.hostname == YC:
            return bool(YC_JOB.match(parts.path))
        return parts.hostname == WAAS and bool(WAAS_JOB.match(parts.path))

    def posting(self, url: str) -> str:
        """`https://www.workatastartup.com/jobs/<id>`; a ycombinator.com URL's id is in its
        page's props."""
        parts = urlsplit(url)
        if parts.hostname == WAAS and (match := WAAS_JOB.match(parts.path)):
            return f"https://{WAAS}/jobs/{match[1]}"
        props = _yc_props(f"https://{YC}{parts.path}")
        if not props:  # a page with no job: nothing to send
            return f"https://{YC}{parts.path}"
        return f"https://{WAAS}/jobs/{props['job']['id']}"

    def account_scope(self, url: str) -> None:
        return None

    def login_url(self, scope: str | None) -> str:
        return ACCOUNT

    def signed_in(self, request: Request, scope: str | None) -> bool:
        """`current_user.json` answers the user for an account, `null` for a visitor."""
        response = request.get(CURRENT_USER)
        return response.ok and response.json() is not None

    def sign_in(self, page: Page, email: str, password: str) -> None:
        """The email, Continue, then the password; an emailed OTP on some sign-ins."""
        page.locator(USERNAME).fill(email)
        page.locator("button[type=submit]").click()
        page.locator(PASSWORD).fill(password)
        status = _posted(page, "/sign_in", "Log in with password")
        if status == 422:
            raise Refused("wrong_password")
        if status != 200:
            raise RuntimeError(f"POST /sign_in answered {status}")
        if _landed(page) == "otp":
            otp.enter(page.locator("input:visible").first, email, "the sign-in")
            page.locator("button", has_text="Verify code").click()
            try:
                page.wait_for_url(
                    lambda url: urlsplit(url).hostname == WAAS, timeout=ACCOUNT_TIMEOUT_MS
                )
            except Timeout:
                raise Blocked("otp_rejected: the sign-in did not take the OTP") from None

    def sign_up(self, page: Page, email: str, password: str, details: SignUpDetails) -> None:
        """First and last name from `name`, a username made from them, signed in once it
        returns: no email verification."""
        first, _, last = (details.name or "").strip().partition(" ")
        if not last.strip():
            raise Refused("sign_up_details: name needs a first and a last name")
        page.goto(SIGN_UP)
        names = page.locator("input[type=text]:not([name])")
        names.nth(0).fill(first)
        names.nth(1).fill(last.strip())
        page.locator("input[type=email]").fill(email)
        handle = re.sub(r"[^a-z]", "", (first + last).lower())[:12] or "user"
        page.locator(USERNAME).fill(handle + secrets.token_hex(3))
        page.locator(PASSWORD).fill(password)
        status, errors = _posted(page, "/sign_up", "Sign up", errors=True)
        if status == 422:
            if "email" in errors:
                raise Refused("email_in_use")
            if "password" in errors:
                raise Refused(f"password_rule: {errors['password']}")
            raise Unsupported(f"unhandled_rendering: the sign-up refused {errors}")
        if status != 200 or _landed(page) != "signed_in":
            raise RuntimeError(f"POST /sign_up answered {status}, no session")

    def request_read(self, url: str) -> list[Field] | None:
        parts = urlsplit(url)
        if parts.hostname == WAAS and (match := WAAS_JOB.match(parts.path)):
            questions = _get_json(f"https://{WAAS}/jobs/{match[1]}/custom_questions.json")
            return _fields(questions["questions"]) if questions else None
        if parts.hostname == YC and YC_JOB.match(parts.path):
            props = _yc_props(f"https://{YC}{parts.path}")
            return _fields(props.get("customQuestions") or []) if props else None
        return None

    def browser_read(self, page: Page) -> list[Field]:
        """Opens the dialog on the job's Work at a Startup page, once the account profile lets
        the account apply."""
        if urlsplit(page.url).hostname == YC:
            props = _props(page, "WaasShowJobPage")
            if not props:
                raise Unsupported("posting_gone: no job on the page")
            page.goto(f"https://{WAAS}/jobs/{props['job']['id']}")
        props = _props(page, "JobDetailPage")
        if not props or not props.get("job"):
            raise Unsupported("posting_gone: no job on the page")
        profile = props.get("profile") or {}
        if not profile.get("minimally_complete"):
            raise ProfileIncomplete("incomplete: save every step with the profile's answers")
        if not profile.get("shared"):
            raise ProfileIncomplete("hidden: set share in the profile's answers")
        if props["job"]["id"] in (props.get("appliedJobIds") or []):
            raise Unsupported("posting_gone: the account already applied to this posting")
        questions = [q for q in props.get("customQuestions") or [] if q["archived_at"] is None]
        self._questions = {f"question_{q['id']}": q for q in questions}
        browser.click(page.locator("a, button").filter(has_text=re.compile(r"^\s*Apply\s*$")).first)
        dialog = page.locator(DIALOG).first
        try:
            dialog.locator("textarea").first.wait_for(timeout=DIALOG_TIMEOUT_MS)
        except Timeout:
            raise RuntimeError("Apply opened no dialog") from None
        deadline = time.monotonic() + QUESTIONS_TIMEOUT_S
        while len(self._tag(page)) < len(questions):  # the dialog may fetch them first
            if time.monotonic() > deadline:
                raise RuntimeError("the dialog does not show every question the job has")
            page.wait_for_timeout(QUESTIONS_POLL_MS)
        return _fields(questions)

    def _tag(self, page: Page) -> list[int]:
        pairs = [[q["id"], q["label"]] for q in self._questions.values()]
        return page.locator(DIALOG).first.evaluate(_TAG, pairs)

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        """Fills the answers and unticks the relocation box: relocation is the account
        profile's, set by `profile`, never by an application."""
        self._tag(page)
        dialog = page.locator(DIALOG).first
        for field_id, answer in answers.items():
            if field_id == MESSAGE:
                if len(str(answer).strip()) < MIN_MESSAGE:
                    raise NotTaken(f"message: under {MIN_MESSAGE} characters")
                dialog.locator("[data-atsfill-field=message]").fill(str(answer))
                continue
            question = self._questions[field_id]
            block = dialog.locator(f'[data-atsfill-question="{question["id"]}"]')
            match question["question_type"], answer:
                case "text" | "url" | "long_text", str():
                    block.locator("textarea").fill(answer)
                case "single_choice", str():
                    _option(block, answer).check()
                case "multiple_choice", list():
                    for option in question["options"]:
                        box = _option(block, option["label"])
                        box.check() if option["label"] in answer else box.uncheck()
                case kind, _:
                    reason = (
                        f"unhandled_rendering: cannot fill {field_id}, a {kind}, with {answer!r}"
                    )
                    raise Unsupported(reason)
        relocation = dialog.locator("[data-atsfill-field=open_to_relocation]")
        if relocation.count():
            relocation.uncheck()

    def read_back(self, page: Page) -> dict[str, Answer]:
        self._tag(page)
        values = page.locator(DIALOG).first.evaluate(_READ_BACK)
        return {k: v for k, v in values.items() if v not in ("", [])}

    def submit(self, page: Page) -> str | None:
        """Send posts `/apply`; its answer lists the jobs applied to at the employer."""
        dialog = page.locator(DIALOG).first
        if dialog.locator("[data-atsfill-field=open_to_relocation]:checked").count():
            raise RuntimeError("the relocation box is ticked")  # it would change the profile
        send = dialog.get_by_role("button", name="Send", exact=True)
        job = int(WAAS_JOB.match(urlsplit(page.url).path)[1])  # ty: ignore[not-subscriptable]
        with page.expect_response(
            lambda r: r.request.method == "POST" and urlsplit(r.url).path == "/apply",
            timeout=SUBMIT_TIMEOUT_MS,
        ) as sent:
            browser.click(send)
        response = sent.value
        answer = response.json() if response.status in (200, 422) else {}
        if answer.get("limit"):
            raise Blocked(f"rate_limited: {answer.get('reason') or 'the weekly application limit'}")
        if answer.get("success") is False:
            where = f"question_{answer['question_id']}: " if answer.get("question_id") else ""
            raise Blocked(f"answer_refused: {where}{answer.get('error')}")
        if job in (answer.get("job_ids") or []):
            return f"POST /apply {response.status}, job_ids {answer['job_ids']}"
        return None


def _fields(questions: list[dict[str, Any]]) -> list[Field]:
    """The message and the questions not archived, as both reads list them (ADR 0011)."""
    message = Field(
        MESSAGE,
        f"Message to the founders (at least {MIN_MESSAGE} characters)",
        FieldType.TEXT,
        True,
    )
    return [message] + [_field(q) for q in questions if q["archived_at"] is None]


def _field(question: dict[str, Any]) -> Field:
    kind = question["question_type"]
    field_id, label, required = (
        f"question_{question['id']}",
        question["label"].strip(),
        question["required"],
    )
    options = [o["label"] for o in question.get("options") or [] if not o.get("archived")]
    match kind:
        case "text" | "long_text":
            return Field(field_id, label, FieldType.TEXT, required, max_length=MAX_LENGTH[kind])
        case "url":
            return Field(field_id, label, FieldType.URL, required, max_length=MAX_LENGTH[kind])
        case "single_choice":
            return Field(field_id, label, FieldType.CHOICE, required, options)
        case "multiple_choice":
            return Field(field_id, label, FieldType.MULTI_CHOICE, required, options)
    reason = f"unhandled_rendering: {label!r} is a {kind} question"
    return Field(field_id, label, FieldType.TEXT, required, unfillable=reason)


def _option(block: browser.Locator, label: str) -> browser.Locator:
    return (
        block.locator("label")
        .filter(has_text=re.compile(rf"^\s*{re.escape(label)}\s*$"))
        .locator("input")
    )


def _props(page: Page, component: str) -> dict[str, Any] | None:
    """The Inertia page's props, when it is `component`."""
    page.locator("[data-page]").first.wait_for(state="attached", timeout=DIALOG_TIMEOUT_MS)
    data = json.loads(page.locator("[data-page]").first.get_attribute("data-page") or "{}")
    return data.get("props") if data.get("component", "").endswith(component) else None


@cache
def _yc_props(url: str) -> dict[str, Any] | None:
    """A ycombinator.com job page's props by request, None for a page with no job: one
    request per posting in a process, `posting` and the request read sharing it."""
    try:
        with urllib.request.urlopen(_request(url), timeout=HTTP_TIMEOUT_S) as response:
            text = response.read().decode()
    except OSError, ValueError:
        return None
    match = re.search(r"data-page=([\"'])(.*?)\1", text, re.S)
    data = json.loads(html.unescape(match[2])) if match else {}
    return data.get("props") if data.get("component", "").endswith("WaasShowJobPage") else None


def _get_json(url: str) -> Any:
    try:
        with urllib.request.urlopen(_request(url), timeout=HTTP_TIMEOUT_S) as response:
            return json.loads(response.read())
    except OSError, ValueError:
        return None


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (atsfill)"})


def _posted(page: Page, path: str, button: str, errors: bool = False) -> Any:
    """Clicks the account form's button and returns the status of the POST to `path`, with
    the `errors` it answered when asked."""
    with page.expect_response(
        lambda r: r.request.method == "POST" and urlsplit(r.url).path == path,
        timeout=ACCOUNT_TIMEOUT_MS,
    ) as sent:
        page.get_by_role("button", name=button, exact=True).click()
    response = sent.value
    if not errors:
        return response.status
    try:
        said = response.json().get("errors") or {}
    except ValueError:
        said = {}
    return response.status, said


def _landed(page: Page) -> str:
    """Where an account form left the page: `signed_in` on Work at a Startup, `otp` on the
    step that asks the emailed OTP."""
    deadline = time.monotonic() + ACCOUNT_TIMEOUT_S
    while time.monotonic() < deadline:
        if urlsplit(page.url).hostname == WAAS:
            return "signed_in"
        try:
            if "enter its code" in page.locator("body").inner_text():
                return "otp"
        except browser.BrowserError:
            pass  # the page is navigating
        page.wait_for_timeout(ACCOUNT_POLL_MS)
    raise Unsupported(f"unhandled_rendering: the account form stays on {page.url}")
