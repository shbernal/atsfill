"""JOIN (`join.com/companies/<company>/<job id>-<slug>`), as docs/adapters/join.md observes.

Applying is a wizard of one step per page, ending on a review page whose Confirm & apply is the
send. Signed in, the wizard takes the name from the account and asks the CV, a cover letter and
the job's screening questions, which the page's own GraphQL query lists. The fill walks the
steps to the review page, and the read-back is what the review page lists. What the wizard takes
from the account (name, phone, country, city, links) is the account profile's, a form of one
profile step that `profile` saves (ADR 0017).
"""

import json
import re
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser
from atsfill.core.adapter import (
    Blocked,
    Gap,
    NotTaken,
    ProfileIncomplete,
    Refused,
    SignUpDetails,
    Unsupported,
)
from atsfill.core.browser import Locator, Page, Request, Response, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOST = "join.com"
JOB = re.compile(r"^/(?:[a-z]{2}/)?companies/([^/]+)/(\d+)(?:-[^/]*)?(?:/apply(?:/.*)?)?/?$")
ME = f"https://{HOST}/api/me"
GRAPHQL = f"https://{HOST}/candidate-api/graphql"
LOGIN = f"https://{HOST}/auth/login/candidate?redirectUrl=%2Ftalent%2Fhome"
UNVERIFIED = "/auth/candidates/unverified-email"
PROFILE = f"https://{HOST}/talent/me?edit=profile"
GENERAL = "general"  # the account profile's one profile step
STEP_TIMEOUT_MS = 20_000
VIEW_TIMEOUT_MS = 5_000  # the step renders after the URL changes
PROFILE_TIMEOUT_MS = 20_000
SAVE_TIMEOUT_MS = 20_000
MENU_TIMEOUT_MS = 5_000
SUGGEST_TIMEOUT_MS = 10_000  # the city's suggestions come from a request per search
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
FILES = {"cv": "cv", "coverLetter": "coverLetter"}  # a file field, by its step
# The documents a job asks, by their type in the wizard's `documentsConfig`
DOCUMENTS = {"CV": ("cv", "CV"), "COVER_LETTER": ("coverLetter", "Cover letter")}
# A question of a class has a step of its own; the others are `question-<position>`.
CLASS_STEPS = {
    "workAuthorization": "WORK_AUTHORIZATION",
    "salaryExpectation": "SALARY_EXPECTATION",
    "startAvailability": "AVAILABILITY",
    "availability": "AVAILABILITY",
}
PROFILE_STEPS = ("personalInformation", "professionalLinks")  # the account profile's
# What a wizard's profile step can ask that the account profile lacks, by input id.
LACKS = {
    "candidate.firstName": "a first name",
    "candidate.lastName": "a last name",
    "candidate.phoneNumber": "a phone",
}
PDF = [".pdf", "application/pdf"]
REMOVE_FILE = "button:has(svg[name=TrashIcon])"  # a stored file's Remove file, in any language

# The profile form's controls, by its field ids.
TEXTS = {
    "first_name": "candidate.firstName",
    "last_name": "candidate.lastName",
    "linkedin": "professionalLinks.LINKEDIN",
}
COUNTRY = '[id="candidate.countryId"]'
CITY = "[data-testid=CityName]"
PHONE = '[id="candidate.phoneNumber"]'
DIAL = "[data-phone-input] button"  # the phone's country code, a popover of countries
POPOVER = "[data-scope=popover][data-part=content]"
DIAL_CODE = re.compile(r"\(\+(\d+)\)\s*$")  # an option of the popover: `France (+33)`

# The page's own GraphQL request, from the page.
_FETCH = """async ([url, body]) => (await fetch(url, {method: "POST", body: JSON.stringify(body),
  headers: {"content-type": "application/json"}})).json()"""

# What the review page lists: each entry's label, then its value.
_REVIEW = """() => [...document.querySelectorAll("a > div")].map(div => {
  const [label, value] = div.children;
  return div.children.length === 2 && label.tagName === "P"
    ? [label.textContent.trim(), value.textContent.trim()] : null;
}).filter(Boolean)"""

# A select's value: the paragraph it shows, not an option of its open menu.
_SHOWN = """root => {
  const p = [...root.querySelectorAll("p")].find(p => !p.closest("[role=option]"));
  return p ? p.textContent.trim() : "";
}"""

# The ids of the inputs that are empty, of those given.
_EMPTY = (
    """ids => ids.filter(id => { const e = document.getElementById(id); return e && !e.value; })"""
)


# Whether the outgoing step's heading is gone.
_LEFT = """shown => ![...document.querySelectorAll("h2")].some(
  h => h.checkVisibility() && h.textContent === shown)"""


class Join:
    name = "join"
    per_employer = False
    asks = {"name"}  # the account's first and last name
    known_gaps: dict[str, Gap] = {}
    side_effects = [
        SideEffect("sign_up", "hiring_system", "login --create creates the candidate account"),
        SideEffect("profile", "hiring_system", "profile --answers saves the account profile"),
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
        listed = (answered.get("data") or {}).get("jobScreeningQuestions")
        if not listed:
            raise RuntimeError(f"JobScreeningQuestions answered {answered.get('errors')}")
        questions = listed["questions"]
        self._questions = sorted(questions, key=lambda q: q.get("position") or 0)
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
        # the wizard's page names the documents the job asks, each required or optional
        raw = page.locator("script#__NEXT_DATA__").text_content() or "{}"
        state = json.loads(raw)["props"]["pageProps"].get("initialState") or {}
        documents = (state.get("job") or {}).get("documentsConfig") or [{"type": "CV"}]
        fields = [
            *(Field(*DOCUMENTS[d["type"]], FieldType.FILE, d.get("constraint") != "OPTIONAL",
                    accept=PDF) for d in documents if d["type"] in DOCUMENTS),
            *(_question(q) for q in self._questions),
        ]  # fmt: skip
        self._fields = {f.id: f for f in fields if not f.unfillable}
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
            elif step in PROFILE_STEPS:
                try:  # shown when the job requires what the account profile lacks
                    _next(page, step)
                except NotTaken, RuntimeError:  # Continue disabled, or refused on click
                    lacks = [LACKS[i] for i in page.evaluate(_EMPTY, list(LACKS))]
                    raise ProfileIncomplete(
                        f"the job asks for {' and '.join(lacks) or f'what {step} asks'}"
                    ) from None
                continue
            else:
                raise Unsupported(f"unhandled_rendering: a wizard step {step!r}")
            _next(page, step)
        raise RuntimeError(f"the wizard did not reach its review page in {MAX_STEPS} steps")

    def _step_question(self, step: str) -> dict[str, Any] | None:
        if kind := CLASS_STEPS.get(step):
            return next((q for q in self._questions if q.get("questionClass") == kind), None)
        if match := re.fullmatch(r"question-(\d+)", step):
            position = int(match[1])
            plain = (q for q in self._questions if not q.get("questionClass"))
            if question := next((q for q in plain if q.get("position") == position), None):
                return question
            raise Unsupported(f"unhandled_rendering: a question step {step} not listed")
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

    def profile_url(self, scope: str | None) -> str:
        return PROFILE

    def profile_steps(self, page: Page) -> list[str]:
        return [GENERAL]

    def open_step(self, page: Page, step: str) -> bool:
        if step != GENERAL:
            raise Unsupported(f"unhandled_rendering: a profile step {step!r}")
        if page.url != PROFILE:
            page.goto(PROFILE)
        page.locator(f'[id="{TEXTS["first_name"]}"]').wait_for(timeout=PROFILE_TIMEOUT_MS)
        return True

    def profile_read(self, page: Page) -> list[Field]:
        """The general information form: the name, the phone, the country and city of residence
        and the LinkedIn URL. The photo, the bio, the work authorizations and the other links are
        left as they are, as are the profile's other sections (experience, education, …)."""
        saved = self.profile_read_back(page)
        limit = {
            k: int(page.locator(f'[id="{i}"]').get_attribute("maxlength") or 0) or None
            for k, i in TEXTS.items()
        }
        fields = [
            Field("first_name", "First name", FieldType.TEXT, True, max_length=limit["first_name"]),
            Field("last_name", "Last name", FieldType.TEXT, True, max_length=limit["last_name"]),
            Field("phone", "Phone number, with its country code", FieldType.PHONE, False),
            Field("country", "Country of residence", FieldType.CHOICE, True, _menu(page, COUNTRY)),
            Field("city", "City, in the country of residence", FieldType.TYPEAHEAD, False),
            Field("linkedin", "LinkedIn", FieldType.URL, False),
        ]  # fmt: skip
        return [replace(f, prefilled=saved.get(f.id)) for f in fields]

    def profile_fill(self, page: Page, answers: dict[str, Answer]) -> None:
        """In the form's order: the city is searched in the country of residence."""
        for field_id, input_id in TEXTS.items():
            if isinstance(answer := answers.get(field_id), str):
                page.locator(f'[id="{input_id}"]').fill(answer)
        if isinstance(answer := answers.get("phone"), str):
            _phone(page, answer)
        if isinstance(answer := answers.get("country"), str):
            _pick(page, COUNTRY, answer, lambda text: text == answer, MENU_TIMEOUT_MS)
        if isinstance(answer := answers.get("city"), str):
            _pick(page, CITY, answer, lambda text: True, SUGGEST_TIMEOUT_MS)

    def profile_read_back(self, page: Page) -> dict[str, Answer]:
        values = {k: page.locator(f'[id="{i}"]').input_value() for k, i in TEXTS.items()}
        phone = page.locator(PHONE)
        if phone.input_value():  # `data-value` holds the country code and the number
            values["phone"] = phone.get_attribute("data-value") or ""
        values["country"] = _shown(page, COUNTRY)
        values["city"] = _shown(page, CITY)
        return {k: v for k, v in values.items() if v}

    def save(self, page: Page) -> None:
        """Save changes posts `UpdateTalentPersonalInformation`; the button stays disabled while
        a required field is empty or nothing changed."""
        button = page.locator("form button[type=submit]").last
        try:  # it is enabled a moment after the last change
            page.wait_for_function(
                "e => !e.disabled", arg=button.element_handle(), timeout=BUTTON_TIMEOUT_MS
            )
        except Timeout:
            raise Refused(
                f"step_refused: {GENERAL}: the form keeps its Save button disabled"
            ) from None
        with page.expect_response(
            lambda r: "operationName=UpdateTalentPersonalInformation" in r.url,
            timeout=SAVE_TIMEOUT_MS,
        ) as sent:
            button.click()
        response = sent.value
        if not response.ok:
            raise RuntimeError(f"UpdateTalentPersonalInformation answered {response.status}")
        if errors := response.json().get("errors"):
            raise Refused(f"step_refused: {GENERAL}: {errors[0].get('message')}")

    def _file(self, page: Page, field_id: str, answer: Answer | None) -> None:
        if not isinstance(answer, str):
            return
        upload, stored = page.locator("input[type=file]"), page.locator(REMOVE_FILE)
        upload.or_(stored).first.wait_for(state="attached", timeout=STEP_TIMEOUT_MS)
        if stored.count():  # a file an earlier run left on the account: the answer replaces it
            stored.click()
        upload.first.set_input_files(answer)
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
    on, heading = page.url, page.locator("h2:visible")
    shown = heading.first.text_content() if heading.count() else None
    try:
        button.click(timeout=STEP_TIMEOUT_MS)
        page.wait_for_url(lambda url: url != on, timeout=STEP_TIMEOUT_MS)
    except Timeout:
        raise RuntimeError(f"Continue on the {step} step went nowhere") from None
    # the URL changes before the step renders; two steps can share a heading, so it is no error
    with suppress(Timeout):
        page.wait_for_function(_LEFT, arg=shown, timeout=VIEW_TIMEOUT_MS)


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


def _select(page: Page, container: str) -> tuple[Locator, Locator]:
    """A select of the profile form: its combobox, and the options its menu shows, whose ids
    carry the combobox's."""
    box = page.locator(container).locator("input[role=combobox]")
    suffix = (box.get_attribute("id") or "").removeprefix("select-input-")
    return box, page.locator(f'[id^="react-select-{suffix}-option-"]')


def _menu(page: Page, container: str) -> list[str]:
    box, options = _select(page, container)
    box.click()
    options.first.wait_for(timeout=MENU_TIMEOUT_MS)
    texts = [t.strip() for t in options.all_inner_texts()]
    box.press("Escape")
    return texts


def _pick(page: Page, container: str, answer: str, take: Callable[[str], bool], ms: int) -> None:
    """Searches the select for the answer and picks the first option `take` accepts."""
    box, options = _select(page, container)
    box.click()
    box.fill(answer)
    try:
        options.first.wait_for(timeout=ms)
    except Timeout:
        raise NotTaken(f"no option for {answer!r}") from None
    texts = [t.strip() for t in options.all_inner_texts()]
    picked = next((i for i, text in enumerate(texts) if take(text)), None)
    if picked is None:
        raise NotTaken(f"no option {answer!r} in {texts}")
    options.nth(picked).click()


def _shown(page: Page, container: str) -> str:
    """A select's value as it shows, empty under its placeholder."""
    return page.locator(container).first.evaluate(_SHOWN)


def _phone(page: Page, answer: str) -> None:
    """Picks the country whose code starts the answer, the longest of them, then types the rest:
    the number's input takes digits only."""
    digits = re.sub(r"\D", "", answer)
    if not answer.strip().startswith("+"):
        raise NotTaken(f"the phone {answer!r} has no country code, `+<code> <number>`")
    page.locator(DIAL).click()
    options = page.locator(POPOVER).locator("[role=option]")
    options.first.wait_for(timeout=MENU_TIMEOUT_MS)
    codes = [m[1] if (m := DIAL_CODE.search(t)) else "" for t in options.all_inner_texts()]
    starting = [i for i, code in enumerate(codes) if code and digits.startswith(code)]
    if not starting:
        raise NotTaken(f"no country code starts the phone {answer!r}")
    picked = max(starting, key=lambda i: len(codes[i]))
    options.nth(picked).click()
    page.locator(PHONE).fill(digits[len(codes[picked]) :])


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
