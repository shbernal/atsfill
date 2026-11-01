"""Work at a Startup, Y Combinator's job board (`ycombinator.com/companies/<employer>/jobs/…`
and `workatastartup.com/jobs/<id>`), as docs/adapters/workatastartup.md observes.

Applying needs a Y Combinator account whose account profile is complete and visible: the form is
then a dialog on the job's Work at a Startup page, a message and the employer's questions.
Nothing is sent before Send, so the read-back is the dialog's state. The account profile is
a wizard of profile steps, each saved on its own button: `profile` fills it (ADR 0017).
"""

import contextlib
import html
import json
import re
import secrets
import time
import urllib.request
from dataclasses import dataclass
from functools import cache
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser, matching, otp
from atsfill.core.adapter import (
    Blocked,
    Gap,
    NotTaken,
    ProfileIncomplete,
    Refused,
    SignUpDetails,
    Unsupported,
)
from atsfill.core.browser import HTTP_TIMEOUT_S, Locator, Page, Request, Timeout
from atsfill.core.results import Answer, Condition, Field, FieldType, Is, SideEffect

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
PROFILE = f"https://{WAAS}/application"
WIZARD = "form:has(button[type=submit])"
PROFILE_TIMEOUT_MS = 20_000
MANUAL_TIMEOUT_MS = 2_000  # a profile with entries shows them, not the choice
SAVE_TIMEOUT_MS = 10_000
SUGGEST_TIMEOUT_S = 5
SUGGEST_POLL_MS = 250
KEY_DELAY_MS = 40
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

# The fields the dialog shows, once tagged; a control `_TAG` did not place, by its place in the
# dialog. The relocation box is the account profile's, not a field.
_SHOWN = """dialog => {
  const out = new Set();
  [...dialog.querySelectorAll("input, select, textarea")].forEach((el, n) => {
    if (el.type === "hidden" || !el.getClientRects().length) return;
    const block = el.closest("[data-atsfill-question]");
    if (block) out.add("question_" + block.dataset.atsfillQuestion);
    else if (el.dataset.atsfillField === "message") out.add("message");
    else if (!el.dataset.atsfillField) out.add(`control #${n + 1}`);
  });
  return [...out];
}"""


class WorkAtAStartup:
    name = "workatastartup"
    per_employer = False
    asks = {"name"}  # split into the first and the last name
    known_gaps: dict[str, Gap] = {}
    side_effects = [
        SideEffect("sign_up", "hiring_system", "login --create creates a Y Combinator account"),
        SideEffect("profile", "hiring_system", "profile --answers saves each profile step"),
        SideEffect("submit", "employer", "the message and answers reach the employer's founders"),
    ]

    def __init__(self) -> None:
        self._questions: dict[str, dict[str, Any]] = {}
        self._step = ""

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

    def shown_fields(self, page: Page) -> list[str]:
        self._tag(page)
        return page.locator(DIALOG).first.evaluate(_SHOWN)

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

    def profile_url(self, scope: str | None) -> str:
        return f"{PROFILE}/personal"

    def profile_steps(self, page: Page) -> list[str]:
        """The steps the profile has, from the wizard's props: skills only for Engineering."""
        props = _props(page, "ProfileWizardPage") or {}
        return [s["name"] for s in (props.get("sectionAppProps") or {}).get("sectionInfo", [])]

    def open_step(self, page: Page, step: str) -> bool:
        """False where the wizard sends the page to an earlier step, unsaved."""
        page.goto(f"{PROFILE}/{step}")
        props = _props(page, "ProfileWizardPage") or {}
        if (props.get("sectionAppProps") or {}).get("activeSection") != step:
            return False
        if step not in STEPS:
            raise Unsupported(f"unhandled_rendering: a profile step {step!r}")
        if step == "experience":  # an upload or an import cannot be read back field by field
            manually = page.get_by_role("button", name="fill in your experience manually")
            with contextlib.suppress(Timeout):
                manually.wait_for(timeout=MANUAL_TIMEOUT_MS)
                manually.click()
        page.locator(WIZARD).last.wait_for(timeout=PROFILE_TIMEOUT_MS)
        self._step = step
        return True

    def profile_read(self, page: Page) -> list[Field]:
        form = page.locator(WIZARD).last
        # a follow-up is listed before it shows: a fresh profile's role step shows its
        # questions only once a role is picked
        return [_profile_field(form, c) for c in STEPS[self._step] if c.shown_when or c.shown(form)]

    def profile_fill(self, page: Page, answers: dict[str, Answer]) -> None:
        form = page.locator(WIZARD).last
        for control in STEPS[self._step]:  # in the page's order: a follow-up after what shows it
            if control.id in answers:
                _profile_fill(page, form, control, answers[control.id])

    def profile_read_back(self, page: Page) -> dict[str, Answer]:
        form = page.locator(WIZARD).last
        values = {c.id: _value(form, c) for c in STEPS[self._step] if c.shown(form)}
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def save(self, page: Page) -> None:
        """Save & next posts the step to `/application/next`, then goes to the step it answers;
        a field the page refuses shows its error and posts nothing."""
        form = page.locator(WIZARD).last
        try:
            with page.expect_response(
                lambda r: (
                    r.request.method == "POST" and urlsplit(r.url).path == "/application/next"
                ),
                timeout=SAVE_TIMEOUT_MS,
            ) as sent:
                form.locator("button[type=submit]").last.click()
        except Timeout:
            said = form.locator("[aria-invalid=true]").count()
            text = " ".join(
                form.locator("p.text-red-600, .text-red-500, .text-red-600").all_inner_texts()
            )
            raise Refused(
                f"step_refused: {self._step}: {text.strip() or f'{said} fields invalid'}"
            ) from None
        response = sent.value
        if response.status == 422:
            try:
                said = "; ".join(response.json().get("errors") or [])
            except ValueError:
                said = ""
            raise Refused(f"step_refused: {self._step}: {said or response.text()[:200]}")
        if not response.ok:
            raise RuntimeError(f"POST /application/next answered {response.status}")
        try:
            to = response.json().get("redirect_to")
        except ValueError:
            to = None
        if to:  # the page goes there next: a navigation started meanwhile would be cut short
            page.wait_for_url(f"**{urlsplit(to).path}", timeout=SAVE_TIMEOUT_MS)


@dataclass(frozen=True)
class Control:
    """One profile field and how its control is found: `kind` names the rendering, `where`
    what finds it (an input's name, a combobox's aria-label, a date's name part), `entry` the
    list it is in, the first entry of Work History or Education."""

    id: str
    label: str
    kind: str
    type: FieldType
    required: bool
    where: str = ""
    entry: str | None = None
    shown_when: Condition | None = None
    unfillable: str | None = None

    def locate(self, form: Locator) -> Locator:
        match self.kind:
            case "phone":
                return form.locator("input[type=tel]")
            case "country" | "combo":
                return form.locator(f'input[aria-label="{self.where}"]')
            case "companies":
                return form.locator('input[aria-label="Company name"]')
            case "multi" | "skills":
                return form.get_by_label(self.where, exact=False)
            case "month":  # the selects' names carry the entry's id: <id>_start_date_month
                suffix = f"_{self.where}_month"
                ends = f"substring(@name, string-length(@name) - {len(suffix) - 1}) = '{suffix}'"
                return _in_entry(form, self.entry, f"select[{ends}]")
            case "current":
                return _in_entry(form, self.entry, "input[@name = 'is_current']")
            case _ if self.entry:
                tag = "textarea" if self.where == "summary" else "input"
                return _in_entry(form, self.entry, f"{tag}[@name = '{self.where}']")
        return form.locator(f'[name="{self.where or self.id}"]')

    def shown(self, form: Locator) -> bool:
        return self.locate(form).count() > 0


def _in_entry(form: Locator, entry: str | None, node: str) -> Locator:
    """The first entry's control in Work History, which comes before Education, or in
    Education."""
    axis = "preceding" if entry == "work" else "following"
    return form.locator(f"xpath=(.//h4[contains(., 'Education')]/{axis}::{node})[1]")


ENGINEERING = Is("role", ["Engineering"])
A_ROLE = Is("role")  # shown once a role is picked
YES_NO = ["Yes", "No"]
T, C, M, TA = FieldType.TEXT, FieldType.CHOICE, FieldType.MULTI_CHOICE, FieldType.TYPEAHEAD
STEPS: dict[str, list[Control]] = {
    "personal": [
        Control("first_name", "First Name", "input", T, True),
        Control("last_name", "Last Name", "input", T, True),
        Control("email", "Email Address (Personal)", "input", T, True),
        Control("linkedin", "LinkedIn Profile URL", "input", T, False),
        Control("phone_country", "Phone country", "country", TA, False, "Phone country"),
        Control("phone_number", "Phone Number", "phone", FieldType.PHONE, False),
        Control("share", "What is your job search status?", "radio", C, True),
        Control("yc_affiliation", "Have you worked at a YC company?", "radio", C, True),
        Control("hidden_companies", "YC companies to be hidden from", "companies", M, False),
    ],
    "location": [
        Control("city_current", "What city do you live in?", "place", TA, True),
        Control(
            "us_authorized",
            "I am legally authorized to work in the United States.",
            "radio",
            C,
            True,
        ),
        Control(
            "us_visa_sponsorship",
            "I do not require visa sponsorship to work legally in the United States.",
            "radio",
            C,
            True,
        ),
        Control("remote", "Are you open to working remotely?", "radio", C, True),
        Control("relocate", "Are you willing to relocate?", "radio", C, True),
    ],
    "role": [
        Control("role", "What job function best fits what you're looking for?", "radio", C, True),
        Control(
            "eng_type",
            "Which engineering roles are you most interested in? (Choose up to four)",
            "multi",
            M,
            True,
            "Which engineering roles",
            shown_when=ENGINEERING,
        ),
        Control("github", "Github", "input", T, False, shown_when=ENGINEERING),
        Control(
            "in_school", "Are you a full-time student at a school or bootcamp?", "radio", C, True
        ),
        Control(
            "job_type",
            "What job type(s) are you most interested in?",
            "checks",
            M,
            True,
            shown_when=A_ROLE,
        ),
        Control(
            "experience", "Years of professional experience", "input", T, True, shown_when=A_ROLE
        ),
    ],
    "experience": [
        Control("work_employer", "Work History: Employer Name", "combo", T, False, "Employer Name"),
        Control("work_title", "Work History: Title", "combo", T, False, "Title"),
        Control("work_location", "Work History: Location", "input", T, False, "location", "work"),
        Control(
            "work_from", "Work History: From (MM/YYYY)", "month", T, False, "start_date", "work"
        ),
        Control(
            "work_current", "Work History: I currently work here", "current", C, False, entry="work"
        ),
        Control(
            "work_to",
            "Work History: To (MM/YYYY)",
            "month",
            T,
            False,
            "end_date",
            "work",
            Is("work_current", ["No"]),
        ),
        Control("work_summary", "Work History: Summary", "input", T, False, "summary", "work"),
        Control("education_school", "Education: School Name", "combo", T, False, "School Name"),
        Control(
            "education_field",
            "Education: Field of Study / Major",
            "combo",
            T,
            False,
            "Field of Study / Major",
        ),
        Control("education_degree", "Education: Degree type", "combo", T, False, "Degree type"),
        Control(
            "education_from",
            "Education: From (MM/YYYY)",
            "month",
            T,
            False,
            "start_date",
            "education",
        ),
        Control(
            "education_to", "Education: To (MM/YYYY)", "month", T, False, "end_date", "education"
        ),
        Control(
            "education_summary", "Education: Summary", "input", T, False, "summary", "education"
        ),
    ],
    "skills": [
        Control(
            "skills",
            "Technologies and skills, each as `<skill>: Beginner|Intermediate|Advanced` (up to 10)",
            "skills",
            M,
            True,
            "Which technologies",
        ),
    ],
    "career": [
        *(
            Control(f"company_sizes_{r}", label, "grid", C, True, f"company_sizes_{r}")
            for r, label in (
                ("1_10", "Seed: 1 - 10 people"),
                ("11_50", "Small: 11 - 50 people"),
                ("51_300", "Medium: 51 - 300 people"),
                ("300_+", "Large: 301+ people"),
            )
        ),
        Control("equity_importance", "How much do you value equity?", "radio", C, True),
        Control(
            "salary_range_importance", "Do you have a minimum salary requirement?", "radio", C, True
        ),
    ],
    "share": [
        Control("short_phrase", "Describe yourself in a short phrase", "input", T, True),
        Control("looking_for", "What are you looking for in your next role?", "input", T, True),
        Control("proud_project", "A project you are proud of", "input", T, False),
    ],
}


def _profile_field(form: Locator, c: Control) -> Field:
    shown = c.shown(form)
    options = _options(form, c) if shown else None
    return Field(
        c.id,
        c.label,
        c.type,
        c.required,
        options,
        prefilled=(_value(form, c) or None) if shown and not c.unfillable else None,
        unfillable=c.unfillable,
        shown_when=c.shown_when,
    )


def _options(form: Locator, c: Control) -> list[str] | None:
    match c.kind:
        case "radio" | "checks":
            return form.locator(f'input[name="{c.id}"]').evaluate_all(_LABELS)
        case "grid":
            return [
                a.rsplit(": ", 1)[-1]
                for a in form.locator(f'input[name="{c.where}"]').evaluate_all(
                    "es => es.map(e => e.ariaLabel)"
                )
            ]
        case "current":
            return YES_NO
    return None


_LABELS = """es => es.map(e => {
  const l = e.closest("label");
  return (l.querySelector("b") ?? l).textContent.replace(/\\s+/g, " ").trim();
})"""


def _value(form: Locator, c: Control) -> Answer | None:
    target = c.locate(form)
    match c.kind:
        case "radio" | "checks":
            picked = form.locator(f'input[name="{c.id}"]:checked').evaluate_all(_LABELS)
            return picked if c.kind == "checks" else (picked[0] if picked else None)
        case "grid":
            picked = form.locator(f'input[name="{c.where}"]:checked')
            return (
                (picked.get_attribute("aria-label") or "").rsplit(": ", 1)[-1]
                if picked.count()
                else None
            )
        case "current":
            return "Yes" if target.is_checked() else "No"
        case "month":
            year = target.locator("xpath=following::select[1]")
            month, year = target.input_value(), year.input_value()
            return f"{month}/{year}" if month and year else None
        case "country":
            return target.evaluate(_SINGLE_VALUE) or None
        case "multi":
            return target.evaluate(_CHIPS)
        case "companies":
            return target.evaluate(_COMPANIES)
        case "skills":
            return form.evaluate(_SKILLS)
    return target.input_value()


# A react-select's shown value, its chips, and the skills each with its level.
_SINGLE_VALUE = """e => {
  let box = e;
  while (box && !/\\(\\+\\d+\\)/.test(box.innerText ?? "")) box = box.parentElement;
  return box ? box.innerText.match(/[^\\n]*\\(\\+\\d+\\)/)[0].trim() : null;
}"""
_CHIPS = """e => {
  const box = e.closest("[id$=__select]") ?? e.parentElement.parentElement.parentElement;
  return [...box.querySelectorAll("[aria-label^='Remove ']")].map(c => c.ariaLabel.slice(7));
}"""
_COMPANIES = """e => [...e.parentElement.previousElementSibling.querySelectorAll(
  "span[role=button] + span")].map(s => s.textContent.trim())"""
_SKILLS = """form => {
  const groups = {};
  for (const r of form.querySelectorAll("input[type=radio][name^=top_skills_]")) {
    const cut = r.ariaLabel.lastIndexOf(": ");
    const [skill, level] = [r.ariaLabel.slice(0, cut), r.ariaLabel.slice(cut + 2)];
    groups[r.name] ??= skill;
    if (r.checked) groups[r.name] = `${skill}: ${level}`;
  }
  return Object.values(groups);
}"""


def _profile_fill(page: Page, form: Locator, c: Control, answer: Answer) -> None:
    target = c.locate(form)
    match c.kind, answer:
        case "input" | "phone", str():
            target.fill(answer)
        case "radio", str():
            labels = form.locator(f'input[name="{c.id}"]').evaluate_all(_LABELS)
            form.locator(f'input[name="{c.id}"]').nth(labels.index(answer)).check(force=True)
        case "checks", list():
            boxes = form.locator(f'input[name="{c.id}"]')
            for i, label in enumerate(boxes.evaluate_all(_LABELS)):
                boxes.nth(i).set_checked(label in answer, force=True)
        case "grid", str():
            form.locator(f'input[name="{c.where}"][aria-label="{c.label}: {answer}"]').check(
                force=True
            )
        case "current", str():
            target.set_checked(answer == "Yes", force=True)
        case "month", str():
            month, _, year = answer.partition("/")
            target.select_option(month)
            target.locator("xpath=following::select[1]").select_option(year)
        case "country" | "place", str():
            _pick(page, c, target, answer, strict=True)
        case "combo", str():
            _pick(page, c, target, answer, strict=False, exact=True)
        case "multi", list():
            have = _value(form, c) or []
            for chip in [h for h in have if h not in answer]:
                form.locator(f'[aria-label="Remove {chip}"]').click()
            for item in [a for a in answer if a not in have]:
                _pick(page, c, target, item, strict=True, exact=True)
        case "companies", list():
            _companies(page, target, c, answer)
        case "skills", list():
            _skills(page, form, target, c, answer)
        case kind, _:
            raise Unsupported(f"unhandled_rendering: cannot fill {c.id}, a {kind}, with {answer!r}")


def _pick(
    page: Page, c: Control, box: Locator, answer: str, strict: bool, exact: bool = False
) -> None:
    """Types the answer key by key and picks the first suggestion that matches it; a combobox
    that takes free text keeps the typed text when none does."""
    box.click()
    if box.input_value():  # Backspace in an empty multi-select removes its last pick
        box.press("Control+a")
        box.press("Backspace")
    box.press_sequentially(answer, delay=KEY_DELAY_MS)
    options = page.locator("[role=option]:visible, li[id^=downshift-][id*=-item-]:visible")
    field = Field(c.id, c.label, FieldType.TYPEAHEAD, False)
    deadline = time.monotonic() + SUGGEST_TIMEOUT_S
    while time.monotonic() < deadline:
        for option in options.all():
            text = option.inner_text().strip()
            if text == answer if exact else matching.matches(field, answer, text):
                option.click()
                return
        page.wait_for_timeout(SUGGEST_POLL_MS)
    if strict:
        raise NotTaken(f"no suggestion for {answer!r} in {c.id}")
    box.press("Escape")


def _companies(page: Page, box: Locator, c: Control, answer: list[str]) -> None:
    """Removes the companies the answer leaves out, then searches each other one and picks the
    result named exactly as it: a nearest name would hide the profile from another company."""
    chips = box.locator("xpath=../preceding-sibling::div[1]//div[span[@role='button']]")
    while extra := [i for i, h in enumerate(box.evaluate(_COMPANIES)) if h not in answer]:
        chips.nth(extra[0]).locator("span[role=button]").click()
    have = box.evaluate(_COMPANIES)
    for company in [a for a in answer if a not in have]:
        _pick(page, c, box, company, strict=True, exact=True)


def _skills(page: Page, form: Locator, box: Locator, c: Control, answer: list[str]) -> None:
    """Removes the skills the answer leaves out, adds the others, then picks each level."""
    wanted: dict[str, str] = {}
    for item in answer:
        skill, sep, level = item.rpartition(": ")
        wanted[skill if sep else level] = level if sep else ""
    have = [v.rsplit(": ", 1)[0] for v in form.evaluate(_SKILLS)]
    for skill in [h for h in have if h not in wanted]:
        form.locator("[aria-label=remove]").nth(have.index(skill)).click()
        have.remove(skill)
    for skill in [w for w in wanted if w not in have]:
        _pick(page, c, box, skill, strict=True, exact=True)
    for skill, level in wanted.items():
        if level:
            form.locator(f'input[type=radio][aria-label="{skill}: {level}"]').check(force=True)


def _fields(questions: list[dict[str, Any]]) -> list[Field]:
    """The message and the questions not archived, as both reads list them (ADR 0011)."""
    message = Field(
        MESSAGE, "Message to the founders", FieldType.TEXT, True, min_length=MIN_MESSAGE
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
