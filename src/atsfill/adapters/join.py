"""JOIN (`join.com/companies/<company>/<job id>-<slug>`), as docs/adapters/join.md observes.

Applying is a wizard of one step per page, ending on a review page whose Confirm & apply is the
send. Signed in, the wizard takes the name from the account and asks the CV, a cover letter and
the job's screening questions, which the page's own GraphQL query lists. The fill walks the
steps to the review page, and the read-back is what the review page lists.
"""

import json
import re
import time
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser
from atsfill.core.adapter import (
    Blocked,
    Gap,
    NotTaken,
    Refused,
    SignUpDetails,
    Unsupported,
    known_gap,
)
from atsfill.core.browser import Locator, Page, Request, Response, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOST = "join.com"
JOB = re.compile(r"^/(?:[a-z]{2}/)?companies/([^/]+)/(\d+)(?:-[^/]*)?(?:/apply(?:/.*)?)?/?$")
ME = f"https://{HOST}/api/me"
GRAPHQL = f"https://{HOST}/candidate-api/graphql"
LOGIN = f"https://{HOST}/auth/login/candidate?redirectUrl=%2Ftalent%2Fhome"
UNVERIFIED = "/auth/candidates/unverified-email"
STEP_TIMEOUT_MS = 20_000
UPLOAD_TIMEOUT_MS = 30_000
ACCOUNT_TIMEOUT_MS = 20_000
SUBMIT_TIMEOUT_MS = 60_000
BUTTON_TIMEOUT_MS = 2_000
OPENING_TIMEOUT_S = 20
CONTINUE_TIMEOUT_S = 15
POLL_MS = 250
MAX_STEPS = 60
MAX_MONTHS = 36  # how far the calendar is paged for a date

QUESTIONS = """query JobScreeningQuestions($input: JobScreeningQuestionsInput!) {
  jobScreeningQuestions(input: $input) {
    questions { id question options type isRequired position questionClass }
  }
}"""
YES_NO = ["Yes", "No"]  # a boolean's cards, in this order, in the job's language
FILES = {"cv": "cv"}  # a file field, by its step
# A question of a class has a step of its own; the others are `question-<n>`, in order.
CLASS_STEPS = {
    "workAuthorization": "WORK_AUTHORIZATION",
    "salaryExpectation": "SALARY_EXPECTATION",
    "startAvailability": "AVAILABILITY",
    "availability": "AVAILABILITY",
}
PROFILE_STEPS = ("personalInformation", "professionalLinks")  # the account profile's
PDF = [".pdf", "application/pdf"]

# The page's own GraphQL request, from the page.
_FETCH = """async ([url, body]) => (await fetch(url, {method: "POST", body: JSON.stringify(body),
  headers: {"content-type": "application/json"}})).json()"""

# What the review page lists: each entry's label, then its value.
_REVIEW = """() => [...document.querySelectorAll("a > div")].map(div => {
  const [label, value] = div.children;
  return div.children.length === 2 && label.tagName === "P"
    ? [label.textContent.trim(), value.textContent.trim()] : null;
}).filter(Boolean)"""


class Join:
    name = "join"
    per_employer = False
    asks = {"name"}  # the account's first and last name
    known_gaps = {"profile_incomplete": Gap.FLOW}
    side_effects = [
        SideEffect("sign_up", "hiring_system", "login --create creates the candidate account"),
        SideEffect("upload", "hiring_system", "a file is stored as soon as it is chosen"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._fields: dict[str, Field] = {}
        self._questions: list[dict[str, Any]] = []
        self._seen: dict[str, Answer] = {}  # each question as its step was left
        self._files: list[str] = []  # the file fields answered, in the steps' order

    def detect(self, url: str, page: Page | None) -> bool:
        parts = urlsplit(url)
        return (parts.hostname or "").removeprefix("www.") == HOST and bool(JOB.match(parts.path))

    def posting(self, url: str) -> str:
        match = JOB.match(urlsplit(url).path)
        return f"https://{HOST}/companies/{match[1]}/{match[2]}" if match else url

    def account_scope(self, url: str) -> None:
        return None

    def login_url(self, scope: str | None) -> str:
        return LOGIN

    def signed_in(self, request: Request, scope: str | None) -> bool:
        """`api/me` answers the candidate for an account."""
        response = request.get(ME)
        return response.ok and response.json().get("type") == "candidate"

    def sign_in(self, page: Page, email: str, password: str) -> None:
        if not _email_step(page, email):
            raise Refused("wrong_password: no account has this email")
        page.locator("#password").fill(password)
        login = _account_post(page, "LoginWithCredentials")
        if login.json().get("errors"):
            raise Refused("wrong_password")
        page.wait_for_url(lambda url: "/auth/login" not in url, timeout=ACCOUNT_TIMEOUT_MS)
        if urlsplit(page.url).path == UNVERIFIED:
            raise Blocked("email_link: the account is not confirmed")

    def sign_up(self, page: Page, email: str, password: str, details: SignUpDetails) -> None:
        """Creates the account, signed in, then waits for its confirmation link: until it is
        clicked the wizard does not open."""
        first, _, last = (details.name or "").strip().rpartition(" ")
        if not first or not last:
            raise Unsupported("unhandled_rendering: the sign-up asks a first and a last name")
        if _email_step(page, email):
            raise Refused("email_in_use")
        page.locator("#firstName").fill(first)
        page.locator("#lastName").fill(last)
        page.locator("#password").fill(password)
        submit = page.locator("[data-testid=SubmitButton]")
        try:
            page.wait_for_function(
                "e => !e.disabled", arg=submit.element_handle(), timeout=BUTTON_TIMEOUT_MS
            )
        except Timeout:
            raise Refused("password_rule: the sign-up keeps its button disabled") from None
        registered = _account_post(page, "RegisterCandidate")
        if errors := registered.json().get("errors"):
            raise Refused(f"password_rule: {errors[0].get('message')}")
        raise Blocked("email_link: the sign-up sent a confirmation link")

    def browser_read(self, page: Page) -> list[Field]:
        """Lists the fields, then opens the wizard on its first step."""
        if not JOB.match(urlsplit(page.url).path):
            raise RuntimeError(f"{page.url} is not a JOIN job")
        raw = page.locator("script#__NEXT_DATA__").text_content() or "{}"
        job = json.loads(raw)["props"]["pageProps"].get("pageData", {}).get("job")
        if not job or job.get("status") != "ONLINE":
            raise Unsupported(f"posting_gone: the job is {job and job.get('status')}")
        variables = {"input": {"companyId": job["companyId"], "jobId": job["id"],
                               "paginationPage": {"page": 1, "pageSize": 100}}}  # fmt: skip
        body = {
            "operationName": "JobScreeningQuestions",
            "variables": variables,
            "query": QUESTIONS,
        }
        answered = page.evaluate(_FETCH, [f"{GRAPHQL}?operationName=JobScreeningQuestions", body])
        questions = answered["data"]["jobScreeningQuestions"]["questions"]
        self._questions = sorted(questions, key=lambda q: q.get("position") or 0)
        fields = [
            Field("cv", "CV", FieldType.FILE, True, accept=PDF),
            *(_question(q) for q in self._questions),
        ]
        self._fields = {f.id: f for f in fields if not f.unfillable}
        browser.click(page.locator("[data-testid=ApplyButton]").locator("visible=true").first)
        page.wait_for_url("**/apply/**", timeout=STEP_TIMEOUT_MS)
        # the wizard opens on `authentication`, and leaves it once it knows the account
        signed_out = page.locator("#email:visible")
        deadline = time.monotonic() + OPENING_TIMEOUT_S
        while _step(page) == "authentication" and time.monotonic() < deadline:
            if signed_out.count():
                break
            page.wait_for_timeout(POLL_MS)
        if _step(page) == "authentication":
            raise Blocked("login_required: run `atsfill login join`")
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        """Walks the wizard from the step it is on to the review page, answering each step; a
        step without a listed field (the account's name, its links) is left as it is."""
        for _ in range(MAX_STEPS):
            step = _step(page)
            if step == "review":
                page.locator("a > div > p + p").first.wait_for(timeout=STEP_TIMEOUT_MS)
                return
            if step in FILES:
                self._file(page, FILES[step], answers.get(FILES[step]))
            elif question := self._step_question(step):
                field_id = f"question.{question['id']}"
                if field_id in self._fields:
                    self._question(page, question, answers.get(field_id))
            elif step == "coverLetter":  # asked by some jobs only: left without one
                pass
            elif step in PROFILE_STEPS:
                try:  # shown when the job requires what the account profile lacks
                    _next(page, step)
                except NotTaken, RuntimeError:
                    detail = f"the {step} step asks what the account profile lacks"
                    raise Unsupported(known_gap(self, "profile_incomplete", detail)) from None
                continue
            else:
                raise Unsupported(f"unhandled_rendering: a wizard step {step!r}")
            _next(page, step)
        raise RuntimeError(f"the wizard did not reach its review page in {MAX_STEPS} steps")

    def _step_question(self, step: str) -> dict[str, Any] | None:
        if kind := CLASS_STEPS.get(step):
            return next((q for q in self._questions if q.get("questionClass") == kind), None)
        if match := re.fullmatch(r"question-(\d+)", step):
            plain = [q for q in self._questions if not q.get("questionClass")]
            if int(match[1]) > len(plain):
                raise Unsupported(f"unhandled_rendering: a question step {step} not listed")
            return plain[int(match[1]) - 1]
        return None

    def read_back(self, page: Page) -> dict[str, Answer]:
        """The files as the review page lists them; each question as its step was left, since
        the review shows a classed question's answer reworded (`€50,000`)."""
        values: dict[str, Answer] = dict(self._seen)
        files = iter(self._files)  # the review lists the files by name, in the steps' order
        for _, value in page.evaluate(_REVIEW):
            if value.lower().endswith(".pdf") and (field_id := next(files, None)):
                values[field_id] = value
        return {k: v for k, v in values.items() if v}

    def submit(self, page: Page) -> str | None:
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.startswith(GRAPHQL)
            and "apply" in r.url.lower(),
            timeout=SUBMIT_TIMEOUT_MS,
        ) as sent:  # fmt: skip
            browser.click(page.locator("button[type=submit]:visible").last)
        response = sent.value
        body = response.json()
        if response.ok and not body.get("errors"):
            parts = urlsplit(response.url)
            return f"POST {parts.path}?{parts.query} {response.status}"
        raise RuntimeError(f"POST {response.url} answered {response.status}: {body.get('errors')}")

    def _file(self, page: Page, field_id: str, answer: Answer | None) -> None:
        if not isinstance(answer, str):
            return
        page.locator("input[type=file]").first.set_input_files(answer)
        # once stored, the step shows the file in place of its input
        page.wait_for_function(
            "() => !document.querySelector('input[type=file]')", timeout=UPLOAD_TIMEOUT_MS
        )
        self._files.append(field_id)

    def _question(self, page: Page, question: dict[str, Any], answer: Answer | None) -> None:
        field_id = f"question.{question['id']}"
        cards = page.locator("[role=radio]")
        match question["type"]:
            case "BOOLEAN" | "SINGLE_CHOICE":
                cards.first.wait_for(timeout=STEP_TIMEOUT_MS)
                # the cards are the options in order: a letter, the option, a description
                texts = [_card(t) for t in cards.all_inner_texts()]
                options = YES_NO if question["type"] == "BOOLEAN" else question["options"]
                if isinstance(answer, str):
                    if answer not in options or options.index(answer) >= len(texts):
                        raise NotTaken(f"no option {answer!r} in {field_id}: {texts}")
                    card = cards.nth(options.index(answer))
                    if card.get_attribute("aria-checked") != "true":
                        card.click()
                checked = [c.get_attribute("aria-checked") == "true" for c in cards.all()]
                if True in checked and checked.index(True) < len(options):
                    self._seen[field_id] = options[checked.index(True)]
            case "DATE":
                if isinstance(answer, str):
                    _date(page, answer)
                picked = page.locator("td[data-part=table-cell][aria-selected=true]")
                if picked.count():
                    self._seen[field_id] = picked.first.get_attribute("data-value") or ""
            case _ if isinstance(answer, str):
                name = f"screeningQuestions.answer-{question['id']}"
                target = page.locator(f'[name="{name}"], [id="{name}"], #salaryExpectationAmount')
                if not target.count():
                    target = page.locator(
                        "textarea:visible, input:visible:not([type=file]):not([role=combobox])"
                    )
                target.first.fill(answer)
                self._seen[field_id] = target.first.input_value()


def _step(page: Page) -> str:
    return urlsplit(page.url).path.rstrip("/").rsplit("/", 1)[-1]


def _continue(page: Page) -> Locator:
    """The footer's last button, after Back: its label is in the job's language."""
    return page.locator("button:visible:has(> [data-button-label])").last


def _next(page: Page, step: str) -> None:
    button = _continue(page)
    deadline = time.monotonic() + CONTINUE_TIMEOUT_S  # an upload enables it once stored
    while button.is_disabled() and time.monotonic() < deadline:
        page.wait_for_timeout(POLL_MS)
    if button.is_disabled():
        raise NotTaken(f"the {step} step keeps Continue disabled: a required answer is missing")
    on = page.url
    try:
        button.click(timeout=STEP_TIMEOUT_MS)
        page.wait_for_url(lambda url: url != on, timeout=STEP_TIMEOUT_MS)
    except Timeout:
        raise RuntimeError(f"Continue on the {step} step went nowhere") from None


def _question(q: dict[str, Any]) -> Field:
    field_id, label, required = f"question.{q['id']}", q["question"], bool(q["isRequired"])
    match q["type"]:
        case "SINGLE_CHOICE":
            return Field(field_id, label, FieldType.CHOICE, required, list(q["options"] or []))
        case "BOOLEAN":
            return Field(field_id, label, FieldType.CHOICE, required, YES_NO)
        case "DATE":
            return Field(field_id, label, FieldType.DATE, required)
        case "INTEGER":
            return Field(field_id, f"{label} (a number)", FieldType.TEXT, required)
        case "TEXT":
            return Field(field_id, label, FieldType.TEXT, required)
    reason = f"unhandled_rendering: {label!r} is a {q['type']} question"
    return Field(field_id, label, FieldType.TEXT, required, unfillable=reason)


def _card(text: str) -> str:
    """A choice card's option: the line after its letter."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[1] if len(lines) > 1 else (lines[0] if lines else "")


def _date(page: Page, answer: str) -> None:
    """Pages the calendar to the answer's month, then picks its day."""
    day = page.locator(f'td[data-part=table-cell][data-value="{answer}"]')
    for _ in range(MAX_MONTHS):
        if day.count() and day.first.is_visible():
            break
        shown = page.locator("td[data-part=table-cell][data-value]").evaluate_all(
            "es => es.map(e => e.dataset.value)"
        )
        side = "next" if shown and answer > max(shown) else "prev"
        page.locator(f"button[data-part={side}-trigger]").first.click()
    else:
        raise NotTaken(f"the calendar shows no {answer}")
    trigger = day.first.locator("[data-part=table-cell-trigger]")
    (trigger if trigger.count() else day.first).click()


def _email_step(page: Page, email: str) -> bool:
    """The account page's first step: the email. Whether an account has it, from what the page
    asks next: a password alone for one, a name and a password for none."""
    try:
        page.locator("#email").wait_for(timeout=ACCOUNT_TIMEOUT_MS)
    except Timeout:
        raise Unsupported(f"unhandled_rendering: no email step on {page.url}") from None
    page.locator("#email").fill(email)
    with page.expect_response(
        lambda r: "operationName=CheckCandidate" in r.url, timeout=ACCOUNT_TIMEOUT_MS
    ) as checked:
        page.locator("button[type=submit]").first.click()
    page.locator("#password").wait_for(timeout=ACCOUNT_TIMEOUT_MS)
    return bool(checked.value.json()["data"]["checkCandidate"]["exists"])


def _account_post(page: Page, operation: str) -> Response:
    with page.expect_response(
        lambda r: f"operationName={operation}" in r.url, timeout=ACCOUNT_TIMEOUT_MS
    ) as answered:
        page.locator("[data-testid=SubmitButton]").click()
    response = answered.value
    if response.status == 429:
        raise Blocked(f"rate_limited: {operation} answered 429")
    return response
