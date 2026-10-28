"""Jobstreet and Jobsdb, SEEK's boards in Southeast Asia (`<country>.jobstreet.com/job/<id>`,
`<country>.jobsdb.com/job/<id>`), as docs/adapters/jobstreet.md observes.

Quick apply needs a SEEK account without a password, signed in by an emailed OTP on each site.
The form is a wizard: documents and personal details, the employer's questions, the SEEK
profile, then review. Each step is kept by the page until submit, so the read-back is each
step's inputs as they stood when it was left.
"""

import re
import time
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser, matching, otp
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

HOST = re.compile(r"^(?:[a-z]{2})\.(?:jobstreet|jobsdb)\.com$")
JOB = re.compile(r"^/(?:[a-z]{2}/)?job/(\d+)")
SIGN_IN_HOST = "login.seek.com"
APPLY = "[data-automation=job-detail-apply]"
CONTINUE = "[data-testid=continue-button]"
SUBMIT = "[data-testid=review-submit-application]"
SAVE_DETAILS = "[data-automation=personal-details-save-button]"
CHALLENGE = "Just a moment..."  # Cloudflare's interstitial: it passes on its own, or on a reload
CHALLENGE_TRIES = 2
SIGN_IN_TIMEOUT_MS = 90_000  # SEEK's sign-in takes up to ~50 s to show its form
TURNSTILE_TIMEOUT_S = 45  # Cloudflare's widget passes on its own headless within seconds
TURNSTILE_POLL_MS = 3_000
OTP_STEP_TIMEOUT_MS = 30_000
SIGNED_IN_TIMEOUT_MS = 60_000  # from the OTP typed to the site, through the OAuth redirects
APPLY_TIMEOUT_MS = 60_000
STEP_TIMEOUT_MS = 30_000
SAVE_TIMEOUT_MS = 15_000
SUBMIT_TIMEOUT_MS = 90_000
CLICK_TRIES = 5
STEP_MOVE_MS = 8_000  # from Continue to the next step's address, when the click was taken
RERENDER_MS = 1_000
STEP_ERRORS = "[role=alert]:has-text('please address')"
SUGGEST_TIMEOUT_S = 8
SUGGEST_POLL_MS = 250
KEY_DELAY_MS = 40
DETAILS = ("firstName", "lastName", "currentLocation", "phone")
VISIBILITY = ["Public", "Standard", "Limited", "Hidden"]

_STEP_ONE = """() => {
  const value = sel => document.querySelector(sel)?.value ?? null;
  const code = document.querySelector('#countryCallingCode');
  const picked = name => [...document.querySelectorAll(`input[type=radio][name^="${name}"]`)]
    .find(e => e.checked);
  const label = e => e && document.querySelector(`label[for="${e.id}"]`)?.innerText.trim();
  const resume = picked('document-select');
  const letter = document.querySelector('[data-testid=coverLetter-method-change]');
  const phone = value('#phoneNumber');
  return {
    firstName: value('#firstName'), lastName: value('#lastName'),
    currentLocation: value('#currentLocation2'),
    phone: phone && code?.value
      ? `${code.options[code.selectedIndex].text.match(/\\+\\d+/)?.[0] ?? ''}${phone}` : null,
    resume: resume && resume.value !== 'dont-include' ? label(resume) : null,
    coverLetter: letter?.checked ? value('[data-testid=coverLetterTextInput]') : null,
  };
}"""

_QUESTIONS = """() => {
  const out = {};
  for (const s of document.querySelectorAll('select[name^="questionnaire."]')) {
    out[s.name] = s.value ? s.options[s.selectedIndex].text.trim() : null;
  }
  for (const e of document.querySelectorAll('input[name^="questionnaire."]:checked')) {
    const text = document.querySelector(`label[for="${e.id}"]`)?.innerText.trim() ?? e.value;
    if (e.type === 'radio') out[e.name] = text;
    else (out[e.name] ??= []).push(text);
  }
  return out;
}"""


class Jobstreet:
    name = "jobstreet"
    per_employer = True  # one SEEK account, signed in on each site apart: the scope is the site
    asks: set[str] = set()
    known_gaps = {"profile_incomplete": Gap.FLOW}
    side_effects = [
        SideEffect("sign_up", "hiring_system", "login --create creates the SEEK account"),
        SideEffect("fill", "hiring_system", "the personal details are saved to the SEEK profile"),
        SideEffect("upload", "hiring_system", "a resume is stored as the SEEK profile's default"),
        SideEffect("submit", "employer", "the application and the SEEK profile reach the employer"),
    ]

    def __init__(self) -> None:
        self._fields: dict[str, Field] = {}
        self._read: dict[str, Answer] = {}

    def detect(self, url: str, page: Page | None) -> bool:
        parts = urlsplit(url)
        return bool(HOST.match(parts.hostname or "")) and bool(JOB.match(parts.path))

    def posting(self, url: str) -> str:
        parts = urlsplit(url)
        match = JOB.match(parts.path)
        return f"https://{parts.hostname}/job/{match[1]}" if match else url

    def account_scope(self, url: str) -> str:
        return urlsplit(url).hostname or ""

    def login_url(self, scope: str | None) -> str:
        return f"https://{scope}/oauth/login?returnUrl=%2F"

    def signed_in(self, request: Request, scope: str | None) -> bool:
        """The site's `/profile` redirects an account to `/profile/me` and shows a visitor its
        page. Cloudflare answers 403 outside the browser, so a session there reads signed out."""
        response = request.get(f"https://{scope}/profile", max_redirects=0)
        return response.status in (302, 307) and response.headers.get("location", "").endswith(
            "/profile/me"
        )

    def sign_in_by_otp(self, page: Page, email: str) -> None:
        _sign_in(page, email, create=False)

    def sign_up_by_otp(self, page: Page, email: str, details: SignUpDetails) -> None:
        _sign_in(page, email, create=True)

    def browser_read(self, page: Page) -> list[Field]:
        """Opens Quick apply; the questions and the account's details come from the GraphQL
        answers the page loads with. An Apply that links out has no form here."""
        job = JOB.match(urlsplit(page.url).path)
        if not job:
            raise RuntimeError(f"{page.url} is not a Jobstreet job")
        apply = page.locator(APPLY).first
        for attempt in range(CHALLENGE_TRIES):  # a check held once passed on the next load
            try:
                apply.wait_for(timeout=APPLY_TIMEOUT_MS)
                break
            except Timeout:
                if page.title() != CHALLENGE:
                    raise RuntimeError("the posting shows no Apply button") from None
                if attempt == CHALLENGE_TRIES - 1:
                    raise Blocked("captcha: Cloudflare's check kept the posting") from None
                page.reload()
        if apply.get_attribute("target") == "_blank":  # Apply, not Quick apply: `isLinkOut`
            raise Unsupported("external_step: the job applies on the employer's site")
        host = urlsplit(page.url).hostname
        answers: dict[str, Any] = {}

        def on_response(response: Response) -> None:
            if urlsplit(response.url).path == "/graphql":
                answers.update(_operations(response))

        page.on("response", on_response)
        try:
            page.goto(f"https://{host}/job/{job[1]}/apply")
            page.locator(CONTINUE).wait_for(timeout=APPLY_TIMEOUT_MS)
            deadline = time.monotonic() + STEP_TIMEOUT_MS / 1000
            needed = {"GetPersonalDetails", "GetJobApplicationProcess", "GetUser"}
            while not needed <= answers.keys() and time.monotonic() < deadline:
                page.wait_for_timeout(SUGGEST_POLL_MS)
        finally:
            page.remove_listener("response", on_response)
        if not needed <= answers.keys():
            raise RuntimeError(
                f"the apply page loaded without {', '.join(needed - answers.keys())}"
            )
        process = answers["GetJobApplicationProcess"]["jobApplicationProcess"]
        if process.get("linkOut"):
            raise Unsupported("external_step: the job applies on the employer's site")
        viewer = answers["GetPersonalDetails"]["viewer"]
        fields = _documents(page, viewer) + [
            f
            for q in (process.get("questionnaire") or {}).get("questions") or []
            if (f := _question(q))
        ]
        if answers["GetUser"]["viewer"].get("profileVisibility2") is None:
            fields.append(Field("profileVisibility", "Profile visibility", FieldType.CHOICE, True,
                                VISIBILITY))  # fmt: skip
        self._fields = {f.id: f for f in fields}
        self._read = {}
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        """Walks the steps in order, each answered and left with Continue, which stays put with
        its errors while one is missing; the review step takes the profile's visibility."""
        self._read = {k: f.prefilled for k in DETAILS if (f := self._fields[k]).prefilled}
        self._read |= _personal_details(page, self._fields, answers)
        _documents_fill(page, answers)
        documents = page.evaluate(_STEP_ONE)
        self._read |= {k: documents[k] for k in ("resume", "coverLetter") if documents[k]}
        _continue(page)
        if page.url.split("?")[0].endswith("/role-requirements"):
            for field_id, answer in answers.items():
                if field_id.startswith("question."):
                    _answer(page, self._fields[field_id], answer)
            asked = page.evaluate(_QUESTIONS)
            self._read |= {
                f"question.{name.removeprefix('questionnaire.')}": value
                for name, value in asked.items()
                if value
            }
            _continue(page)
        if page.url.split("?")[0].endswith("/profile"):
            try:
                _continue(page)
            except NotTaken as e:
                if "career history" not in str(e).lower():
                    raise
                reason = known_gap(self, "profile_incomplete", "the profile has no career history")
                raise Unsupported(reason) from None
        if "profileVisibility" in answers:
            radio = page.locator(
                f"[data-testid=profile-visibility-field2-radio-group-"
                f"{str(answers['profileVisibility']).lower()}]"
            )
            radio.check(force=True)
            if radio.is_checked():
                self._read["profileVisibility"] = radio.get_attribute("value") or ""

    def read_back(self, page: Page) -> dict[str, Answer]:
        return dict(self._read)

    def submit(self, page: Page) -> str | None:
        review = page.url
        browser.click(page.locator(SUBMIT))
        try:
            page.wait_for_url(lambda url: url != review, timeout=SUBMIT_TIMEOUT_MS)
        except Timeout:
            raise RuntimeError("Submit application left the review step in place") from None
        return f"Submit application moved to {urlsplit(page.url).path}"


def _sign_in(page: Page, email: str, create: bool) -> None:
    """SEEK's sign-in: the email, Cloudflare's widget, then the OTP it emails, typed in six
    digits that send themselves. An email without an account lands on "Register a new account?",
    where only a sign-up goes on."""
    box = page.get_by_label("Email address")
    try:
        box.wait_for(timeout=SIGN_IN_TIMEOUT_MS)
    except Timeout:
        raise Unsupported(f"unhandled_rendering: no SEEK sign-in on {page.url}") from None
    box.fill(email)
    code = page.get_by_label("verification input")
    deadline = time.monotonic() + TURNSTILE_TIMEOUT_S
    while not code.count():
        if time.monotonic() > deadline:
            raise Blocked("captcha: SEEK's sign-in kept asking to complete the recaptcha")
        page.get_by_role("button", name="Email me a sign in code").click()
        try:  # clicked before the widget passed, the page says so and sends nothing
            code.wait_for(timeout=TURNSTILE_POLL_MS)
        except Timeout:
            if not page.get_by_text("complete the recaptcha").count():
                code.wait_for(timeout=OTP_STEP_TIMEOUT_MS)
    otp.enter(code, email, "SEEK's sign-in")
    try:
        page.wait_for_url(
            lambda url: (
                "signin-confirmation" in url
                or (urlsplit(url).hostname != SIGN_IN_HOST and "/oauth" not in urlsplit(url).path)
            ),
            timeout=SIGNED_IN_TIMEOUT_MS,
        )
    except Timeout:
        raise Blocked(f"otp_rejected: SEEK's sign-in sent an OTP to {email}") from None
    if "signin-confirmation" not in page.url:
        return
    if not create:
        raise Refused("no_account")
    page.locator("#privacy-chkbox").check(force=True)  # the marketing box stays unticked
    page.get_by_role("button", name="Register new account").click()
    page.wait_for_url(
        lambda url: "/oauth" not in urlsplit(url).path and "/sign-in/" not in urlsplit(url).path,
        timeout=SIGNED_IN_TIMEOUT_MS,
    )


def _operations(response: Response) -> dict[str, Any]:
    """The `data` of each operation a GraphQL response answers, by operation name."""
    try:
        sent, answered = response.request.post_data_json, response.json()
    except Exception:
        return {}
    pairs = zip(*(x if isinstance(x, list) else [x] for x in (sent, answered)), strict=False)
    return {
        s["operationName"]: a["data"]
        for s, a in pairs
        if isinstance(s, dict) and isinstance(a, dict) and a.get("data")
    }


def _documents(page: Page, viewer: dict[str, Any]) -> list[Field]:
    """The first step: the profile's personal details, then the resume and the cover letter,
    optional where the page offers to leave them out."""
    details = viewer["personalDetails"]
    code = (details.get("countryCallingCode") or {}).get("countryCallingCode", "")
    location = (viewer.get("currentLocation2") or {}).get("description")
    resume = page.locator("input[type=file][id^=resume]")
    accept: list[str] | None = None
    if resume.count():
        accept = [str(a) for a in (resume.get_attribute("accept") or "").split(",")]
    return [
        Field("firstName", "First name", FieldType.TEXT, True,
              prefilled=details.get("firstName") or None),
        Field("lastName", "Last name", FieldType.TEXT, True,
              prefilled=details.get("lastName") or None),
        Field("currentLocation", "Home location", FieldType.TYPEAHEAD, True, prefilled=location),
        Field("phone", "Phone number", FieldType.PHONE, False,
              prefilled=f"{code}{details['phoneNumber']}" if details.get("phoneNumber") else None),
        Field("resume", "Resumé", FieldType.FILE,
              not page.locator("input[type=radio][value=dont-include]").count(), accept=accept),
        Field("coverLetter", "Cover letter", FieldType.TEXT,
              not page.locator("[data-testid=coverLetter-method-none]").count()),
    ]  # fmt: skip


def _question(raw: dict[str, Any]) -> Field | None:
    field_id, label = f"question.{raw['id']}", raw["text"]
    options = [o["text"] for o in raw.get("options") or []]
    match raw["__typename"]:
        case "SingleChoiceQuestion":
            return Field(field_id, label, FieldType.CHOICE, True, options)
        case "MultipleChoiceQuestion":
            return Field(field_id, label, FieldType.MULTI_CHOICE, True, options)
        case "PrivacyPolicyQuestion":
            return Field(field_id, label, FieldType.CHOICE, True, options)
    reason = f"unhandled_rendering: {label!r} is a {raw['__typename']}"
    return Field(field_id, label, FieldType.TEXT, True, unfillable=reason)


def _personal_details(
    page: Page, fields: dict[str, Field], answers: dict[str, Answer]
) -> dict[str, Answer]:
    """Types the details that differ from the profile's, then saves them to it: the step goes
    no further while the details are open, whether changed or not. Returns what was saved."""
    changed = {
        k: v for k in DETAILS
        if (v := answers.get(k)) is not None and v != fields[k].prefilled
    }  # fmt: skip
    if changed and not page.locator("#firstName").is_visible():
        raise Unsupported("unhandled_rendering: the personal details show no inputs")
    for key, selector in (("firstName", "#firstName"), ("lastName", "#lastName")):
        if key in changed:
            page.locator(selector).fill(str(changed[key]))
    if "currentLocation" in changed:
        _suggest(page, fields["currentLocation"], page.locator("#currentLocation2"),
                 str(changed["currentLocation"]))  # fmt: skip
    if "phone" in changed:
        _phone(page, str(changed["phone"]))
    save = page.locator(SAVE_DETAILS)
    if not save.is_visible():
        return {}
    saved = {k: v for k, v in page.evaluate(_STEP_ONE).items() if k in DETAILS and v}
    save.click()
    try:
        save.wait_for(state="hidden", timeout=SAVE_TIMEOUT_MS)
    except Timeout:
        said = page.locator("[id$=-message]").all_inner_texts()
        raise NotTaken(f"the personal details were not saved: {'; '.join(said)}") from None
    return saved


def _phone(page: Page, answer: str) -> None:
    """Picks the country whose calling code starts the E.164 answer, the longest one, then
    types the rest."""
    codes = page.locator("#countryCallingCode").evaluate(
        "s => [...s.options].map(o => [o.value, o.text.match(/\\+\\d+/)?.[0]])"
    )
    picked = max(
        ((value, code) for value, code in codes if code and answer.startswith(code)),
        key=lambda c: len(c[1]),
        default=None,
    )
    if picked is None:
        raise NotTaken(f"no country calling code starts {answer!r}")
    page.locator("#countryCallingCode").select_option(picked[0])
    page.locator("#phoneNumber").fill(answer.removeprefix(picked[1]))


def _documents_fill(page: Page, answers: dict[str, Answer]) -> None:
    """Uploads the resume, which the page then picks, or leaves it out; the same for the cover
    letter, written in its box."""
    if (resume := answers.get("resume")) is not None:
        page.locator("input[type=file][id^=resume]").set_input_files(str(resume))
        name = str(resume).rsplit("/", 1)[-1]
        page.locator("label", has_text=name).first.wait_for(timeout=SAVE_TIMEOUT_MS)
    elif (none := page.locator("input[type=radio][value=dont-include]")).count():
        none.check(force=True)
    if (letter := answers.get("coverLetter")) is not None:
        page.locator("[data-testid=coverLetter-method-change]").check(force=True)
        page.locator("[data-testid=coverLetterTextInput]").fill(str(letter))
    elif (none := page.locator("[data-testid=coverLetter-method-none]")).count():
        none.check(force=True)


def _answer(page: Page, field: Field, answer: Answer) -> None:
    name = f"questionnaire.{field.id.removeprefix('question.')}"
    select = page.locator(f'select[name="{name}"]')
    if select.count():
        select.select_option(label=str(answer))
        return
    boxes = page.locator(f'input[name="{name}"]')
    if not boxes.count():
        raise Unsupported(f"unhandled_rendering: {field.id} shows no select or boxes")
    wanted = answer if isinstance(answer, list) else [answer]
    for box in boxes.all():
        label = page.locator(f'label[for="{box.get_attribute("id")}"]')
        text = label.inner_text().strip()
        wanted_here = any(matching.matches(field, str(w), text) for w in wanted)
        if box.get_attribute("type") == "radio" and not wanted_here:
            continue
        if box.is_checked() != wanted_here:  # the input is styled away: its label takes the click
            browser.click(label)
        if box.is_checked() != wanted_here:
            raise NotTaken(f"{field.id} did not take {text!r}")


def _continue(page: Page) -> None:
    """Continue, once the next step's address shows; the step's errors otherwise. The profile
    step re-renders as its queries come back, losing a click: one that moves nothing and says
    nothing is clicked again."""
    step = page.url
    page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)
    for _ in range(CLICK_TRIES):
        try:
            browser.click(page.locator(CONTINUE))
            page.wait_for_url(lambda url: url != step, timeout=STEP_MOVE_MS)
            break
        except browser.BrowserError as e:  # a Timeout too
            if not isinstance(e, Timeout) and "not attached" not in str(e):
                raise
            if page.locator(STEP_ERRORS).count():
                break
            page.wait_for_timeout(RERENDER_MS)
    if page.url == step:
        said = " ".join(page.locator("[role=alert]").all_inner_texts()).strip()
        raise NotTaken(f"Continue stayed on {urlsplit(step).path}: {said}")
    page.locator(f"{CONTINUE}, {SUBMIT}").first.wait_for(timeout=STEP_TIMEOUT_MS)
    # a step renders its inputs once its queries answer, and takes no Continue before
    page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)
    if page.url.split("?")[0].endswith("/role-requirements"):
        page.locator('[name^="questionnaire."]').first.wait_for(timeout=STEP_TIMEOUT_MS)


def _suggest(page: Page, field: Field, box: Locator, answer: str) -> None:
    """Types the answer key by key, which loads the suggestions, and picks the one that
    matches it: a typed location the page does not take."""
    box.fill("")
    box.press_sequentially(answer, delay=KEY_DELAY_MS)
    options = page.locator("[role=option]:visible")
    deadline = time.monotonic() + SUGGEST_TIMEOUT_S
    while time.monotonic() < deadline:
        for option in options.all():
            if matching.matches(field, answer, option.inner_text().strip()):
                option.click()
                return
        page.wait_for_timeout(SUGGEST_POLL_MS)
    raise NotTaken(f"no suggestion for {answer!r} in {field.id}")
