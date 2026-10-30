"""Wellfound, a startup job board (`wellfound.com/jobs/<id>-<slug>`), as docs/adapters/wellfound.md
observes.

Applying needs a Wellfound candidate account whose account profile is onboarded: Apply on the
posting then opens a dialog, the employer's questions or, without any, a note. The dialog's
GraphQL read, `JobApplicationModal`, lists the questions with their kind and options, and says
whether the account may apply. Nothing is sent before Send, so the read-back is the dialog's
state.
"""

import contextlib
import json
import re
import time
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser, otp
from atsfill.core.adapter import (
    Blocked,
    Gap,
    NotTaken,
    Refused,
    SignedOut,
    SignUpDetails,
    Unsupported,
    known_gap,
)
from atsfill.core.browser import Locator, Page, Request, Response, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOST = "wellfound.com"
JOB = re.compile(r"^/(?:company/[^/]+/)?jobs/(\d+)(?:-[^/]*)?/?$")
LOGIN = f"https://{HOST}/login"
SIGN_UP = f"https://{HOST}/jobs/signup"
ONBOARDING = f"https://{HOST}/jobs/onboarding"  # a visitor is sent to /login
GRAPHQL = "/graphql"
MODAL = "JobApplicationModal"
CREATE = "CreateJobApplication"
FORM = 'form:has([data-test="JobApplicationModal--SubmitButton"])'
SEND = '[data-test="JobApplicationModal--SubmitButton"]'
NOTE = "note"
GONE = "no longer available"
WRONG_PASSWORD = "Email or password is incorrect"
PASSWORD_LENGTH = (8, 128)
NUMBER = re.compile(r"-?\d+(\.\d+)?")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# a qualification error the profile can fix; the others are the employer's rules
MISSING = {"MISSING_SKILLS": "skills", "MISSING_WORK_EXPERIENCE": "work experience"}
OPEN_TIMEOUT_S = 20
OPEN_POLL_MS = 250
ACCOUNT_TIMEOUT_MS = 20_000
CODE_TIMEOUT_MS = 20_000
SUBMIT_TIMEOUT_MS = 60_000


class Wellfound:
    name = "wellfound"
    per_employer = False
    asks = {"name"}
    known_gaps = {
        "profile_incomplete": Gap.FLOW,
        "ai_assessment": Gap.FLOW,
        "question_sets": Gap.RENDERING,
    }
    side_effects = [
        SideEffect("sign_up", "hiring_system", "login --create creates a Wellfound account"),
        SideEffect("load", "hiring_system", "Apply records an apply attempt on the job"),
        SideEffect("submit", "employer", "the answers reach the employer with the profile"),
    ]

    def __init__(self) -> None:
        self._job: dict[str, Any] = {}

    def detect(self, url: str, page: Page | None) -> bool:
        parts = urlsplit(url)
        return parts.hostname in (HOST, f"www.{HOST}") and bool(JOB.match(parts.path))

    def posting(self, url: str) -> str:
        match = JOB.match(urlsplit(url).path)
        return f"https://{HOST}/jobs/{match[1]}" if match else url

    def account_scope(self, url: str) -> None:
        return None

    def login_url(self, scope: str | None) -> str:
        return LOGIN

    def signed_in(self, request: Request, scope: str | None) -> bool | None:
        """The onboarding sends a visitor to the sign-in, an account to one of its steps."""
        response = request.get(ONBOARDING, max_redirects=0)
        if response.headers.get("cf-mitigated"):
            return None
        return urlsplit(response.headers.get("location") or "").path != "/login"

    def sign_in(self, page: Page, email: str, password: str) -> None:
        """`POST /login` redirects an account, and shows the form again with an error
        otherwise."""
        page.locator("#user_email").fill(email)
        page.locator("#user_password").fill(password)
        page.locator("input[type=submit][value='Log in']").click()
        deadline = time.monotonic() + ACCOUNT_TIMEOUT_MS / 1000
        while time.monotonic() < deadline:
            if urlsplit(page.url).path != "/login":
                return
            with contextlib.suppress(browser.BrowserError):  # the page is navigating
                if WRONG_PASSWORD in page.locator("body").inner_text():
                    raise Refused("wrong_password")
            page.wait_for_timeout(OPEN_POLL_MS)
        raise Unsupported("unhandled_rendering: the sign-in stays on /login without an error")

    def sign_up(self, page: Page, email: str, password: str, details: SignUpDetails) -> None:
        """The job seeker sign-up: name, email and password, then the six-digit OTP it emails;
        Create account signs in and starts the onboarding."""
        low, high = PASSWORD_LENGTH
        if not low <= len(password) <= high:
            raise Refused(f"password_rule: {low} to {high} characters")
        page.goto(SIGN_UP)
        page.locator("[data-signup-field=name]").fill(details.name or "")
        page.locator("[data-signup-field=email]").fill(email)
        page.locator("[data-signup-field=password]").fill(password)
        response = _posted(
            page, "/jobs/signup/email-verification-code", page.locator("input[value=Continue]")
        )
        if response.status == 429:
            raise Blocked("rate_limited: the sign-up's OTP was asked too often")
        if response.status == 422:
            errors = _json(response).get("field_errors") or {}
            if "email" in errors:
                raise Refused("email_in_use")
            if "password" in errors:
                raise Refused(f"password_rule: {'; '.join(errors['password'])}")
            raise Unsupported(f"unhandled_rendering: the sign-up refused {errors}")
        if not response.ok:
            raise RuntimeError(f"the sign-up's OTP request answered {response.status}")
        code = page.locator("#email_verification_code")
        with page.expect_response(
            lambda r: urlsplit(r.url).path == "/jobs/signup/email-verification-code/verify",
            timeout=CODE_TIMEOUT_MS,
        ) as verified:
            otp.enter(code, email, "the sign-up")
        if not verified.value.ok:
            raise Blocked("otp_rejected: the sign-up did not take the OTP")
        create = page.locator("input[type=submit][value='Create account']")
        create.wait_for(timeout=ACCOUNT_TIMEOUT_MS)
        page.wait_for_function("e => !e.disabled", arg=create.element_handle())
        response = _posted(page, "/jobs/create-user", create)
        if not response.ok:
            raise RuntimeError(f"POST /jobs/create-user answered {response.status}")
        page.wait_for_url(lambda url: urlsplit(url).path != "/jobs/signup")

    def browser_read(self, page: Page) -> list[Field]:
        """Clicks Apply and lists the dialog's questions from its GraphQL read."""
        if GONE in page.locator("body").inner_text():
            raise Unsupported("posting_gone: the job listing is no longer available")
        apply = page.get_by_role("button", name="Apply", exact=True)
        if not apply.count():
            raise Unsupported("posting_gone: the posting shows no Apply")
        read: list[dict[str, Any]] = []

        def seen(response: Response) -> None:
            if _operation(response) == MODAL:
                read.append(_json(response))

        page.on("response", seen)
        try:
            browser.click(apply.first)
            self._job = _opened(page, read)
        finally:
            page.remove_listener("response", seen)
        job = self._job
        if job.get("currentUserApplied"):
            raise Unsupported("already_sent: the account applied to this posting")
        if job.get("offsiteListingUrl"):
            raise Unsupported(f"external_step: Apply opens {job['offsiteListingUrl']}")
        errors = (job.get("currentUserQualificationReport") or {}).get("errors") or []
        if missing := [MISSING[e] for e in errors if e in MISSING]:
            reason = f"the job needs the profile's {' and '.join(missing)}"
            raise Unsupported(known_gap(self, "profile_incomplete", reason))
        if errors:
            raise Unsupported(f"not_qualified: {', '.join(errors)}")
        if job.get("aiAssessmentRequired"):
            reason = "the job asks for an AI interview after the send"
            raise Unsupported(known_gap(self, "ai_assessment", reason))
        if job.get("applicationQuestionSets"):
            reason = "the dialog shows question sets"
            raise Unsupported(known_gap(self, "question_sets", reason))
        form = page.locator(FORM)
        fields = [_field(q) for q in job.get("questions") or []]
        if form.locator("textarea[name=userNote]").count():
            label = form.locator("textarea[name=userNote]").get_attribute("placeholder") or "Note"
            fields.append(Field(NOTE, label, FieldType.TEXT, bool(job["candidateNoteRequired"])))
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        form = page.locator(FORM)
        questions = {f"question_{q['id']}": q for q in self._job.get("questions") or []}
        for field_id, answer in answers.items():
            if field_id == NOTE:
                form.locator("textarea[name=userNote]").fill(str(answer))
                continue
            question = questions[field_id]
            name = f"customQuestionAnswers[{question['id']}]"
            match question["kind"], answer:
                case "SHORT" | "LONG", str():
                    form.locator(f'[name="{name}[answer]"]').fill(answer)
                case "NUMBER", str() if NUMBER.fullmatch(answer.strip()):
                    form.locator(f'[name="{name}[answer]"]').fill(answer.strip())
                case "DATE", str() if DATE.fullmatch(answer):
                    form.locator(f'[name="{name}[answer]"]').fill(answer)
                case "NUMBER" | "DATE", str():
                    raise NotTaken(f"{answer!r} is no {question['kind'].lower()} for {field_id}")
                case "SINGLE", str():
                    _tick(form, _option(form, question, answer), True)
                case "MULTIPLE", list():
                    for option in question["options"]:
                        box = _option(form, question, option["option"])
                        _tick(form, box, option["option"] in answer)
                case kind, _:
                    said = f"cannot fill {field_id}, a {kind}, with {answer!r}"
                    raise Unsupported(f"unhandled_rendering: {said}")

    def read_back(self, page: Page) -> dict[str, Answer]:
        form = page.locator(FORM)
        values: dict[str, Answer] = {}
        note = form.locator("textarea[name=userNote]")
        if note.count():
            values[NOTE] = note.input_value()
        for q in self._job.get("questions") or []:
            name, field_id = f"customQuestionAnswers[{q['id']}]", f"question_{q['id']}"
            if q["kind"] not in ("SINGLE", "MULTIPLE"):
                values[field_id] = form.locator(f'[name="{name}[answer]"]').input_value()
                continue
            labels = {o["id"]: o["option"] for o in q["options"]}
            picked = form.locator(f'input[name="{name}[jobListingQuestionOptionId]"]:checked')
            chosen = [labels.get(v, v) for v in picked.evaluate_all("es => es.map(e => e.value)")]
            single = chosen[0] if chosen else ""
            values[field_id] = chosen if q["kind"] == "MULTIPLE" else single
        return {k: v for k, v in values.items() if v not in ("", [])}

    def submit(self, page: Page) -> str | None:
        """Send posts the `CreateJobApplication` mutation, answered with the job and whether
        the account applied to it. A request Cloudflare challenges shows Turnstile, whose pass
        reloads the page."""
        try:
            with page.expect_response(
                lambda r: _operation(r) == CREATE, timeout=SUBMIT_TIMEOUT_MS
            ) as sent:
                browser.click(page.locator(FORM).locator(SEND))
        except Timeout:
            raise RuntimeError(f"Send posted no {CREATE}") from None
        response = sent.value
        if response.headers.get("cf-mitigated") == "challenge":
            raise Blocked("captcha: Cloudflare Turnstile challenged the send")
        answer = _json(response)
        if errors := answer.get("errors"):
            said = "; ".join(e.get("message", "") for e in errors)
            raise Blocked(f"answer_refused: {said}")
        job = (answer.get("data") or {}).get("jobListing") or {}
        if job.get("currentUserApplied") and str(job.get("id")) == str(self._job.get("id")):
            return f"{CREATE} {response.status}: job {job['id']} currentUserApplied"
        return None


def _opened(page: Page, read: list[dict[str, Any]]) -> dict[str, Any]:
    """Waits for what Apply leads to: the dialog's read, the profile's edit page where the
    onboarding is unfinished, or the visitor's dialog where the session ended."""
    deadline = time.monotonic() + OPEN_TIMEOUT_S
    while time.monotonic() < deadline:
        if urlsplit(page.url).path == "/profile/edit":
            reason = "Apply sends the account to its profile: the onboarding is unfinished"
            raise Unsupported(known_gap(Wellfound(), "profile_incomplete", reason))
        if page.locator("input[name=passwordConfirmation]").count():
            raise SignedOut("login_required: Apply shows the visitor's sign-up dialog")
        if read and page.locator(FORM).count():
            job = ((read[-1].get("data") or {}).get("jobListing")) or {}
            if not job:
                raise Unsupported("posting_gone: the dialog's read has no job")
            return job
        page.wait_for_timeout(OPEN_POLL_MS)
    raise RuntimeError("Apply opened no dialog")


def _field(question: dict[str, Any]) -> Field:
    field_id, label = f"question_{question['id']}", question["question"].strip()
    required, options = question["required"], [o["option"] for o in question["options"]]
    match question["kind"]:
        case "SHORT" | "LONG" | "NUMBER":
            return Field(field_id, label, FieldType.TEXT, required)
        case "DATE":
            return Field(field_id, label, FieldType.DATE, required)
        case "SINGLE":
            return Field(field_id, label, FieldType.CHOICE, required, options)
        case "MULTIPLE":
            return Field(field_id, label, FieldType.MULTI_CHOICE, required, options)
    reason = f"unhandled_rendering: {label!r} is a {question['kind']} question"
    return Field(field_id, label, FieldType.TEXT, required, unfillable=reason)


def _option(form: Locator, question: dict[str, Any], label: str) -> Locator:
    ids = [o["id"] for o in question["options"] if o["option"] == label]
    if not ids:
        raise NotTaken(f"{label!r} is no option of question_{question['id']}")
    name = f"customQuestionAnswers[{question['id']}][jobListingQuestionOptionId]"
    return form.locator(f'input[name="{name}"][value="{ids[0]}"]')


def _tick(form: Locator, box: Locator, checked: bool) -> None:
    """Clicks the option's label: the input itself is moved out of view."""
    if box.is_checked() != checked:
        form.locator(f'label[for="{box.get_attribute("id")}"]').click()


def _operation(response: Response) -> str | None:
    """The GraphQL operation a response answers, None for another request."""
    request = response.request
    if request.method != "POST" or urlsplit(response.url).path != GRAPHQL:
        return None
    try:
        return json.loads(request.post_data or "{}").get("operationName")
    except ValueError:
        return None


def _json(response: Response) -> dict[str, Any]:
    try:
        answer = response.json()
    except ValueError:
        return {}
    return answer if isinstance(answer, dict) else {}


def _posted(page: Page, path: str, button: Locator) -> Response:
    with page.expect_response(
        lambda r: r.request.method == "POST" and urlsplit(r.url).path == path,
        timeout=ACCOUNT_TIMEOUT_MS,
    ) as sent:
        button.click()
    return sent.value
