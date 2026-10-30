"""Wellfound, a startup job board (`wellfound.com/jobs/<id>-<slug>`), as docs/adapters/wellfound.md
observes.

Applying needs a Wellfound candidate account whose account profile has what the system requires:
Apply on the posting then opens a dialog, the employer's questions or, without any, a note. The
dialog's GraphQL read, `JobApplicationModal`, lists the questions with their kind and options, and
says whether the account may apply. Nothing is sent before Send, so the read-back is the dialog's
state. The account profile is two tabs of `/profile/edit` whose controls save as they change, so
`profile` fills it and reads back what the reloaded page says is saved (ADR 0017).
"""

import contextlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser, otp
from atsfill.core.adapter import (
    Blocked,
    Gap,
    NotTaken,
    ProfileIncomplete,
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
PROFILE = f"https://{HOST}/profile/edit"
ABOUT, PREFERENCES = "about", "preferences"  # the account profile's profile steps
STEPS = {ABOUT: PROFILE, PREFERENCES: f"{PROFILE}/preferences"}
GRAPHQL = "/graphql"
MODAL = "JobApplicationModal"
CREATE = "CreateJobApplication"
OFFER = "We want to feature you to recruiters"  # a dialog over a complete profile
DECLINE = "Skip for now"  # its other button, Start my application, enrols the account
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
YES_NO = ["Yes", "No"]
# yearsExperienceInPrimaryRole is the position in this list
YEARS = ["< 1 Year", *(f"{n} Year{'s' * (n > 1)}" for n in range(1, 10)), "10+ Years"]
STATUSES = {
    "interviewing": "Ready to interview",
    "open": "Open to offers",
    "closed": "Closed to offers",
}
JOB_TYPES = {
    "full_time": "Full-time Employee",
    "contract": "Contractor",
    "internships": "Intern",
    "cofounders": "Co-founder",
}
LINKS = {
    "website": "onlineBioUrl",
    "linkedin": "linkedinUrl",
    "github": "githubUrl",
    "twitter": "twitterUrl",
}
WORK = ("work_company", "work_title", "work_from", "work_to", "work_current", "work_description")
OPEN_TIMEOUT_S = 20
OPEN_POLL_MS = 250
ACCOUNT_TIMEOUT_MS = 20_000
CODE_TIMEOUT_MS = 20_000
SUBMIT_TIMEOUT_MS = 60_000
PROFILE_TIMEOUT_MS = 20_000
SAVE_TIMEOUT_MS = 8_000  # a Save whose value the page refuses posts nothing
SUGGEST_TIMEOUT_MS = 8_000
SUGGEST_POLL_MS = 250
TYPE_DELAY_MS = 40


class Wellfound:
    name = "wellfound"
    per_employer = False
    asks = {"name"}
    known_gaps = {
        "ai_assessment": Gap.FLOW,
        "question_sets": Gap.RENDERING,
    }
    side_effects = [
        SideEffect("sign_up", "hiring_system", "login --create creates a Wellfound account"),
        SideEffect("load", "hiring_system", "Apply records an apply attempt on the job"),
        SideEffect("profile", "hiring_system", "profile --answers saves each profile field"),
        SideEffect("profile", "hiring_system", "profile declines the recruiter offer it meets"),
        SideEffect("submit", "employer", "the answers reach the employer with the profile"),
    ]

    def __init__(self) -> None:
        self._job: dict[str, Any] = {}
        self._step = ABOUT
        self._filled = False  # the page holds what it had before a fill saved
        self._offer_handled = False

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
            raise ProfileIncomplete(f"the job needs the profile's {' and '.join(missing)}")
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

    def profile_url(self, scope: str | None) -> str:
        return PROFILE

    def profile_steps(self, page: Page) -> list[str]:
        return list(STEPS)

    def open_step(self, page: Page, step: str) -> bool:
        if step not in STEPS:
            raise Unsupported(f"unhandled_rendering: a profile step {step!r}")
        self._step, self._filled = step, False
        if not self._offer_handled:
            offer = page.locator(".ReactModal__Content", has_text=OFFER)
            page.add_locator_handler(
                offer, lambda: page.get_by_role("button", name=DECLINE).click()
            )
            self._offer_handled = True
        page.goto(STEPS[step])
        ready = "#form-input--bio" if step == ABOUT else "#form-input--usAuthorized--true"
        page.locator(ready).wait_for(state="attached", timeout=PROFILE_TIMEOUT_MS)
        return True

    def profile_read(self, page: Page) -> list[Field]:
        """The fields that decide whether Apply opens, and what employers read first. The name,
        the photo, the other job types, the salary, the locations, education, achievements,
        the identity questions and the Culture and Resume tabs are left as they are."""
        saved = self.profile_read_back(page)
        fields = _about(page) if self._step == ABOUT else _preferences()
        return [replace(f, prefilled=saved.get(f.id)) for f in fields]

    def profile_fill(self, page: Page, answers: dict[str, Answer]) -> None:
        """Saves each answer that differs from the saved value, as the page does: most controls
        save as they change, the bio, the links and the work experience on their Save."""
        saved = self.profile_read_back(page)
        new = {k: v for k, v in answers.items() if not _same(v, saved.get(k))}
        self._filled = bool(new)
        if self._step == ABOUT:
            _fill_about(page, new, saved)
        else:
            _fill_preferences(page, new)

    def profile_read_back(self, page: Page) -> dict[str, Answer]:
        """What the system holds: the page's Apollo state, reloaded after a fill."""
        if self._filled:
            page.reload()
            self._filled = False
        state = _apollo(page)
        candidate = next(v for k, v in state.items() if k.startswith("Candidate:"))
        values = (
            _saved_about(candidate, state) if self._step == ABOUT else _saved_preferences(candidate)
        )
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def save(self, page: Page) -> None:
        """Nothing is left to save: `profile_fill` saved each answer as it went."""


def _opened(page: Page, read: list[dict[str, Any]]) -> dict[str, Any]:
    """Waits for what Apply leads to: the dialog's read, the profile's edit page where the
    onboarding is unfinished, or the visitor's dialog where the session ended."""
    deadline = time.monotonic() + OPEN_TIMEOUT_S
    while time.monotonic() < deadline:
        if urlsplit(page.url).path == "/profile/edit":
            raise ProfileIncomplete("Apply sends the account to its unfinished profile")
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


def _apollo(page: Page) -> dict[str, Any]:
    """The objects the server rendered the page from, by their `Type:id`."""
    data = json.loads(page.locator("#__NEXT_DATA__").text_content() or "{}")
    return data["props"]["pageProps"]["apolloState"]["data"]


def _saved_about(candidate: dict[str, Any], state: dict[str, Any]) -> dict[str, Answer]:
    def found(ref: dict[str, str] | None) -> dict[str, Any]:
        return state[ref["__ref"]] if ref else {}

    place, years = (
        found(candidate.get("basedInLocation")),
        candidate.get("yearsExperienceInPrimaryRole"),
    )
    names = [place.get("displayName"), place.get("parentLocationDisplayName")]
    values: dict[str, Answer] = {
        "location": ", ".join(n for n in names if n),
        "primary_role": (candidate.get("primaryRole") or [""])[0],
        "years_experience": "" if years is None else YEARS[years],
        "skills": [found(t)["displayName"] for t in candidate.get("skillTags") or []],
        "bio": candidate.get("bio") or "",
        **{k: candidate.get(name) or "" for k, name in LINKS.items()},
    }
    if entries := candidate.get("startupRoleMetadata"):
        entry = found(entries[0])
        values |= {
            "work_company": found(entry.get("startup")).get("name", ""),
            "work_title": entry.get("title") or "",
            "work_from": _month(entry.get("startedAtMonth"), entry.get("startedAtYear")),
            "work_to": _month(entry.get("endedAtMonth"), entry.get("endedAtYear")),
            "work_current": "Yes" if entry.get("current") else "No",
            "work_description": entry.get("description") or "",
        }
    return values


def _saved_preferences(candidate: dict[str, Any]) -> dict[str, Answer]:
    def yes_no(key: str) -> str:
        value = candidate.get(key)
        return "" if value is None else YES_NO[0 if value else 1]

    return {
        "job_type": JOB_TYPES.get(candidate.get("primaryLookingFor") or "", ""),
        "us_authorized": yes_no("usAuthorized"),
        "require_sponsorship": yes_no("requireSponsorship"),
        "remote": "Yes" if candidate.get("distributedTeamOk") else "No",  # a checkbox
        "job_status": STATUSES.get(candidate.get("candidateState") or "", ""),
    }


def _month(month: int | None, year: int | None) -> str:
    return f"{month:02d}/{year}" if month and year else ""


def _same(answer: Answer, saved: Answer | None) -> bool:
    if isinstance(answer, list) and isinstance(saved, list):
        return sorted(answer) == sorted(saved)
    return answer == saved


def _about(page: Page) -> list[Field]:
    T, C = FieldType.TEXT, FieldType.CHOICE
    return [
        Field("location", "Where you are based, City, Country", FieldType.TYPEAHEAD, True),
        Field("primary_role", "Primary role", C, True, _menu(page, "primaryRole")),
        Field("years_experience", "Years of experience in that role", C, True, YEARS),
        Field("skills", "Skills, by their Wellfound names", FieldType.MULTI_CHOICE, False),
        Field("bio", "Bio", T, False, max_length=160),
        Field("website", "Website", FieldType.URL, False),
        Field("linkedin", "LinkedIn", FieldType.URL, False),
        Field("github", "GitHub", FieldType.URL, False),
        Field("twitter", "Twitter", FieldType.URL, False),
        Field("work_company", "Work experience: company, by its Wellfound name", T, False),
        Field("work_title", "Work experience: title", T, False),
        Field("work_from", "Work experience: start, MM/YYYY", T, False),
        Field("work_to", "Work experience: end, MM/YYYY", T, False),
        Field("work_current", "Work experience: I currently work here", C, False, YES_NO),
        Field("work_description", "Work experience: description", T, False),
    ]


def _preferences() -> list[Field]:
    C = FieldType.CHOICE
    return [
        Field("job_type", "Type of job", C, True, list(JOB_TYPES.values())),
        Field("us_authorized", "Legally authorized to work in the US", C, True, YES_NO),
        Field("require_sponsorship", "Requires US visa sponsorship", C, True, YES_NO),
        Field("remote", "Open to working remotely", C, False, YES_NO),
        Field("job_status", "Job search status", C, True, list(STATUSES.values())),
    ]


def _fill_about(page: Page, new: dict[str, Answer], saved: dict[str, Answer]) -> None:
    if isinstance(answer := new.get("location"), str):
        if shown := saved.get("location"):
            clear = page.get_by_text(str(shown), exact=True).locator("xpath=../*[@role='button']")
            clear.click()
        box = page.locator("input[placeholder='e.g. San Francisco']").first
        item = _suggestion(page, box, answer)
        _saved_by(page, "ProfileSavePrimaryLocation", item.click, "location")
    if isinstance(answer := new.get("primary_role"), str):
        _picked(page, "ProfileSaveRoles", "primaryRole", answer)
    if isinstance(answer := new.get("years_experience"), str):
        _picked(page, "ProfileSaveRoles", "yearsExperienceInPrimaryRole", answer)
    if isinstance(answer := new.get("skills"), list):
        _skills(page, answer, saved.get("skills"))
    if isinstance(answer := new.get("bio"), str):
        bio = page.locator("#form-input--bio")
        bio.fill(answer)
        _saved_by(page, "ProfileSaveBio", _save_button(bio).click, "bio")
    if links := {k: v for k, v in new.items() if k in LINKS}:
        for key, answer in links.items():
            page.locator(f"#form-input--{LINKS[key]}").fill(str(answer))
        box = page.locator(f"#form-input--{LINKS[next(iter(links))]}")
        _saved_by(page, "ProfileSaveSocialProfiles", _save_button(box).click, ", ".join(links))
    if any(k in new for k in WORK):
        _work(page, {k: v for k, v in new.items() if k in WORK}, saved)


def _skills(page: Page, answer: list[str], saved: Answer | None) -> None:
    """A chip per skill, each change saved. Where the list empties the saved chips go first."""
    box = page.locator("input[placeholder='e.g. Python, React']")
    held = saved if isinstance(saved, list) else []
    for skill in (s for s in held if s not in answer):
        chip = box.locator("xpath=ancestor::form").locator("span:has(> span[role=button])")
        remove = chip.filter(has_text=re.compile(f"^{re.escape(skill)}$")).locator("[role=button]")
        _saved_by(page, "ProfileSaveSkills", remove.click, "skills")
    for skill in (s for s in answer if s not in held):
        _saved_by(page, "ProfileSaveSkills", _suggestion(page, box, skill).click, "skills")


def _work(page: Page, new: dict[str, Answer], saved: dict[str, Answer]) -> None:
    """The first entry: a saved one opens on its Edit, which cannot change the company."""
    entry = {k: saved.get(k, "") for k in WORK} | new
    if "work_company" in saved:
        if "work_company" in new:
            said = f"work_company: the saved entry is at {saved['work_company']}"
            raise Refused(f"step_refused: {ABOUT}: {said}")
        page.get_by_text("Edit", exact=True).first.click()
    current = entry["work_current"] == "Yes"
    needed = ["work_company", "work_title", "work_from", *([] if current else ["work_to"])]
    if lacking := [k for k in needed if not entry[k]]:
        raise Refused(f"step_refused: {ABOUT}: the work experience needs {', '.join(lacking)}")
    if "work_company" in new:
        box = page.locator("input[placeholder='Type to search']").first
        name = str(new["work_company"])
        _suggestion(page, box, name).click()
    title = page.locator("#form-input--title")
    title.fill(str(entry["work_title"]))
    box = page.locator("#form-input--current--true")
    if box.is_checked() != current:
        page.locator("label[for='form-input--current--true']").click()
    _date(page, "Start date", str(entry["work_from"]))
    if not current:
        _date(page, "End date", str(entry["work_to"]))
    page.locator("#form-input--description").fill(str(entry["work_description"]))
    _saved_by(page, "ProfileSaveExperience", _save_button(title).click, "work experience")


def _date(page: Page, placeholder: str, month: str) -> None:
    box = page.locator(f"input[placeholder='{placeholder}']").first
    box.click()
    box.fill("")
    box.press_sequentially(month, delay=TYPE_DELAY_MS)
    box.press("Enter")


def _fill_preferences(page: Page, new: dict[str, Answer]) -> None:
    """The job search status goes last: the system ignores it, with a 200, while another
    required field of the profile is missing."""
    if isinstance(answer := new.get("job_type"), str):
        _picked(page, "ProfileSaveLookingFor", "primaryLookingFor", answer)
    for field_id, name, operation in (
        ("us_authorized", "usAuthorized", "ProfileSaveUsAuthorization"),
        ("require_sponsorship", "requireSponsorship", "ProfileSaveVisaSponsorship"),
    ):
        if field_id in new:
            value = str(new[field_id] == "Yes").lower()
            label = page.locator(f"label[for='form-input--{name}--{value}']")
            _saved_by(page, operation, label.click, field_id)
    if "remote" in new:
        label = page.locator("label[for='form-input--distributedTeamOk--true']")
        _saved_by(page, "ProfileSaveInterestedLocations", label.click, "remote")
    if isinstance(answer := new.get("job_status"), str):
        menu = page.locator("[data-test=CandidateStateDropdown]").last
        menu.locator("button").click()
        _saved_by(page, "SaveCandidateState", menu.get_by_text(answer).first.click, "job_status")


def _saved_by(page: Page, operation: str, act: Callable[[], None], what: str) -> None:
    """Runs what saves a field and waits for its mutation; the page posts none for a value it
    refuses itself."""
    try:
        with page.expect_response(
            lambda r: _operation(r) == operation, timeout=SAVE_TIMEOUT_MS
        ) as sent:
            act()
    except Timeout:
        raise Refused(f"step_refused: {what}: the page saved nothing") from None
    response = sent.value
    if response.headers.get("cf-mitigated") == "challenge":
        raise Blocked("captcha: Cloudflare Turnstile challenged a profile save")
    if errors := _json(response).get("errors"):
        said = "; ".join(e.get("message", "") for e in errors)
        raise Refused(f"step_refused: {what}: {said}")


def _save_button(control: Locator) -> Locator:
    return control.locator("xpath=ancestor::form").get_by_role("button", name="Save")


def _menu(page: Page, name: str) -> list[str]:
    """A react-select's options; a group's heading holds no span. Opened by the keyboard, as
    its input has no box to click once it holds a value."""
    box = page.locator(f"#react-select-form-input--{name}-input")
    box.focus()
    box.press("ArrowDown")
    options = page.locator(f'[id^="react-select-form-input--{name}-option-"] span')
    options.first.wait_for(timeout=PROFILE_TIMEOUT_MS)
    names = options.all_inner_texts()
    box.press("Escape")
    return names


def _picked(page: Page, operation: str, name: str, label: str) -> None:
    """Searches a react-select and takes the first option, which saves it; the read-back
    tells whether it was the answer."""
    box = page.locator(f"#react-select-form-input--{name}-input")
    box.focus()
    box.fill(label)
    _saved_by(page, operation, lambda: box.press("Enter"), name)


def _suggestion(page: Page, box: Locator, name: str) -> Locator:
    """Searches and returns the suggestion titled `name`, a suggestion's other lines being its
    site or followers. The search finds nothing for some whole names ("React.js") and lists
    them for their first word. A name it does not list is `answer_not_taken`."""
    items = page.locator("[id*=downshift][id*=item]")
    for query in dict.fromkeys((name, re.split(r"\W", name)[0])):
        box.click()
        box.fill("")
        box.press_sequentially(query, delay=TYPE_DELAY_MS)
        deadline = time.monotonic() + SUGGEST_TIMEOUT_MS / 1000
        while time.monotonic() < deadline:
            titles = [t.splitlines()[0].strip() for t in items.all_inner_texts() if t.strip()]
            if name in titles:
                return items.nth(titles.index(name))
            page.wait_for_timeout(SUGGEST_POLL_MS)
    box.fill("")
    raise NotTaken(f"{name!r} is no suggestion of Wellfound's search")


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
