"""Welcome to the Jungle (`welcometothejungle.com` jobs and their `easyapply.jobs` links), as
docs/adapters/wttj.md observes.

Applying needs an account, which atsfill can sign in to and create: the form opens in a modal,
prefilled from the account profile. Nothing is sent before submit, so the read-back is the form's
inputs and the CV's shown name.
"""

import re
import time
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser, matching
from atsfill.core.adapter import Blocked, Gap, NotTaken, Refused, Unsupported
from atsfill.core.browser import Locator, Page, PageRequest, Request, Response, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOST = "www.welcometothejungle.com"
JOB = re.compile(r"^/(?:[a-z]{2}/)?companies/([^/]+)/jobs/([^/?#]+)")
FORM = "form:has([data-testid=apply-form-submit])"
ME = "https://api.welcometothejungle.com/api/v2/users/me"
SIGN_UP = f"https://{HOST}/en/authenticate/signup"
ACCOUNT_TIMEOUT_MS = 20_000
ACCOUNT_TIMEOUT_S = 20
ACCOUNT_POLL_MS = 250
FORM_TIMEOUT_MS = 20_000
SIGNED_IN_TIMEOUT_S = 10
SUBMIT_TIMEOUT_MS = 90_000
SUGGEST_TRIES = 3
LOCATION_TIMEOUT_S = 3
SUGGEST_TIMEOUT_S = 5
SIGNED_IN_POLL_MS = 250
LOCATION_POLL_MS = 200
SUGGEST_POLL_MS = 250
KEY_DELAY_MS = 30
YES_NO = ["Yes", "No"]

# The fillable fields: an input's name is its id. Email comes from the account, disabled.
_READ = """form => [...form.querySelectorAll("input[name], textarea[name], select[name]")]
  .filter(e => !e.disabled && e.name !== "avatar")
  .map(e => {
    const label = form.querySelector(`label[for="${CSS.escape(e.id)}"]`);
    return {id: e.name, tag: e.tagName.toLowerCase(), type: e.type,
            label: (label ?? e.closest("fieldset")?.querySelector("legend"))?.textContent
              .replace(/\\s+/g, " ").trim() ?? "",
            required: e.required || label?.hasAttribute("required"),
            accept: e.accept ? e.accept.split(",") : null,
            prefilled: (e.type === "checkbox" ? (e.checked ? "Yes" : null)
              : e.type === "file" ? e.parentElement.querySelector("[data-id]")?.dataset.id
              : e.value) || null};
  })"""

_READ_BACK = """(form, ids) => Object.fromEntries(ids.map(id => {
  const e = form.querySelector(`[name="${id}"]`);
  if (e?.type === "checkbox") return [id, e.checked ? "Yes" : "No"];
  if (e?.type === "file") {
    return [id, e.parentElement.querySelector("[data-id]")?.dataset.id ?? null];
  }
  return [id, e?.value ?? null];
}))"""


class WelcomeToTheJungle:
    name = "wttj"
    per_employer = False
    asks_name = True  # the profile's first name
    known_gaps: dict[str, Gap] = {}
    side_effects = [
        SideEffect("sign_up", "hiring_system", "login --create creates the account"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._fields: dict[str, Field] = {}

    def detect(self, url: str, page: Page | None) -> bool:
        parts = urlsplit(url)
        if parts.hostname == "easyapply.jobs":
            return parts.path.startswith("/r/")
        return parts.hostname == HOST and bool(JOB.match(parts.path))

    def posting(self, url: str) -> str:
        """`easyapply.jobs` links resolve to the job, so the key comes from the loaded page."""
        match = JOB.match(urlsplit(url).path)
        return f"https://{HOST}/companies/{match[1]}/jobs/{match[2]}" if match else url

    def account_scope(self, url: str) -> None:
        return None

    def login_url(self, scope: str | None) -> str:
        return f"https://{HOST}/en/signin"

    def signed_in(self, request: Request, scope: str | None) -> bool:
        """`users/me` answers 200 for an account, 204 for a visitor."""
        return request.get(ME).status == 200

    def sign_in(self, page: Page, email: str, password: str) -> None:
        _account_form(page, "sign-in-form", {"email": email, "password": password}, "/sessions")

    def sign_up(self, page: Page, email: str, password: str, name: str | None) -> None:
        """Creates the account, signed in, then waits for its confirmation link: clicking it
        signs every session out."""
        page.goto(SIGN_UP)
        values = {"firstname": name or "", "email": email, "password": password}
        _account_form(page, "sign-up-form", values, "/registrations")
        raise Blocked("email_link: the sign-up sent a confirmation link")

    def browser_read(self, page: Page) -> list[Field]:
        """Opens the apply modal; a job that applies on another site has no form here."""
        if not JOB.match(urlsplit(page.url).path):
            raise RuntimeError(f"{page.url} is not a Welcome to the Jungle job")
        apply = page.locator('[data-testid="job_header-button-apply"]').first
        apply.wait_for()
        href = apply.get_attribute("href")
        deadline = time.monotonic() + SIGNED_IN_TIMEOUT_S
        while href and "/signin" in href and time.monotonic() < deadline:
            page.wait_for_timeout(
                SIGNED_IN_POLL_MS
            )  # rendered for a visitor until the page knows the account
            href = apply.get_attribute("href")
        if href and "/signin" in href:
            raise Blocked("login_required: run `atsfill login wttj`")
        if href:
            raise Unsupported(f"external_step: the job applies on another site ({href})")
        form = page.locator(FORM).first
        if not form.is_visible():  # an easyapply link opens the form by itself
            try:
                browser.click(apply)
            except browser.Overlaid:
                if not form.is_visible():
                    raise
        try:
            form.wait_for(timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise RuntimeError("the apply button opened no form") from None
        location = form.locator("[name=location]")
        deadline = time.monotonic() + LOCATION_TIMEOUT_S
        while location.count() and not location.input_value() and time.monotonic() < deadline:
            page.wait_for_timeout(
                LOCATION_POLL_MS
            )  # the account profile's location comes last, a second later
        fields: dict[str, Field] = {}
        for raw in form.evaluate(_READ):
            if raw["id"] not in fields and (field := _field(raw)) is not None:
                fields[field.id] = field  # a radio group once
        self._fields = {i: f for i, f in fields.items() if not f.unfillable}
        return list(fields.values())

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        form = page.locator(FORM).first
        for field_id, answer in answers.items():
            target = form.locator(f'[name="{field_id}"]')
            match self._fields[field_id].type, answer:
                case FieldType.FILE, str():
                    box = target.locator("xpath=..")
                    remove = box.locator("button[title=Remove]")
                    if remove.count():
                        remove.click()  # the account profile's CV; never made the default
                    target.set_input_files(answer)
                    box.locator(f'[data-id="{answer.rsplit("/", 1)[-1]}"]').wait_for()
                case FieldType.CHOICE, str():
                    if target.is_checked() != (answer == "Yes"):
                        browser.click(form.locator(f'label[for="{target.get_attribute("id")}"]'))
                case FieldType.TYPEAHEAD, str():
                    _suggest(page, self._fields[field_id], target, answer)
                case FieldType.TEXT | FieldType.PHONE, str():
                    target.fill(answer)
                case kind, _:
                    reason = (
                        f"unhandled_rendering: cannot fill {field_id}, a {kind}, with {answer!r}"
                    )
                    raise Unsupported(reason)

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = page.locator(FORM).first.evaluate(_READ_BACK, list(self._fields))
        return {k: v for k, v in values.items() if v not in (None, "")}

    def submit(self, page: Page) -> str | None:
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.split("?")[0].endswith("/applications"),
            timeout=SUBMIT_TIMEOUT_MS,
        ) as sent:
            browser.click(page.locator('[data-testid="apply-form-submit"]'))
        response = sent.value
        if response.status == 201:
            reference = response.json().get("application", {}).get("reference")
            return f"POST {urlsplit(response.url).path} {response.status}, reference {reference}"
        raise RuntimeError(f"POST {response.url} answered {response.status}")


def _account_form(page: Page, form: str, values: dict[str, str], posts: str) -> None:
    """Fills the sign-in or sign-up form, submits it and returns once the system took it; raises
    what the system answered, or what the form says under a field it refused without posting."""
    try:
        page.locator(f"[data-testid={form}-submit-button]").wait_for(timeout=ACCOUNT_TIMEOUT_MS)
    except Timeout:
        if "too many requests" in page.locator("body").inner_text().lower():
            reason = f"rate_limited: {urlsplit(page.url).path} answers too many requests"
            raise Blocked(reason) from None
        raise Unsupported(f"unhandled_rendering: no {form} on {page.url}") from None
    for field, value in values.items():
        page.locator(f"[data-testid={form}-{field}-input]").fill(value)
    # posted before the page has its CSRF token, the form is answered 403 and says nothing
    page.wait_for_function(
        "() => document.cookie.includes('csrf-token=')", timeout=ACCOUNT_TIMEOUT_MS
    )
    sent: list[PageRequest] = []
    answered: list[Response] = []

    def posted(request: PageRequest) -> bool:
        return request.method == "POST" and urlsplit(request.url).path.endswith(posts)

    def on_request(request: PageRequest) -> None:
        if posted(request):
            sent.append(request)

    def on_response(response: Response) -> None:
        if posted(response.request):
            answered.append(response)

    page.on("request", on_request)
    page.on("response", on_response)
    on_form = page.url
    page.locator(f"[data-testid={form}-submit-button]").click()
    said = page.locator(f"[data-testid^={form}-][data-testid$=-error]")
    deadline = time.monotonic() + ACCOUNT_TIMEOUT_S
    try:
        while time.monotonic() < deadline:
            if answered:
                _account_answer(answered[0])
                # the page leaves the form on its own: a navigation before then is aborted
                page.wait_for_url(lambda url: url != on_form, timeout=ACCOUNT_TIMEOUT_MS)
                return
            # the page can show the answer before its response event arrives here
            if said.count() and not sent:
                field = said.first.get_attribute("data-testid") or ""
                text = said.first.inner_text().strip()
                if field.endswith("-password-error"):
                    raise Refused(f"password_rule: {text}")
                raise Unsupported(f"unhandled_rendering: the form says {text!r}")
            page.wait_for_timeout(ACCOUNT_POLL_MS)
    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)
    raise Unsupported("unhandled_rendering: the form answered nothing")


def _account_answer(response: Response) -> None:
    match response.status:
        case 200 | 201:
            return
        case 401:
            raise Refused("wrong_password")
        case 422 if "email" in response.json().get("errors", {}):
            raise Refused("email_in_use")
    raise RuntimeError(f"POST {urlsplit(response.url).path} answered {response.status}")


def _suggest(page: Page, field: Field, box: Locator, answer: str) -> None:
    """Retypes the answer key by key, which loads the suggestions, and picks the first that
    matches it, never "current position" (the item with an icon, which geolocates). Typed too
    early, the answer loads no suggestions at all: it is typed again."""
    options = page.locator("[role=option]:visible").filter(has_not=page.locator("svg"))
    for _ in range(SUGGEST_TRIES):
        box.click()
        box.press("Control+a")  # `fill("")` (select-all, Delete) leaves the account profile's value
        box.press("Backspace")
        box.press_sequentially(answer, delay=KEY_DELAY_MS)
        deadline = time.monotonic() + SUGGEST_TIMEOUT_S
        while time.monotonic() < deadline:
            for option in options.all():
                if matching.matches(field, answer, option.inner_text().strip()):
                    option.click()
                    return
            page.wait_for_timeout(SUGGEST_POLL_MS)
    raise NotTaken(f"no suggestion for {answer!r} in {field.id}")


def _field(raw: dict[str, Any]) -> Field | None:
    """The field an input is, listed unfillable when atsfill cannot fill it; None for a hidden
    input, no field."""
    name, kind, options = raw["id"], None, None
    match raw["type"]:
        case "hidden":
            return None
        case "file":
            kind = FieldType.FILE
        case "checkbox" if name == "consent":
            kind, options = FieldType.CHOICE, YES_NO
        case "text" | "textarea" | "email":
            kind = {"phone": FieldType.PHONE, "location": FieldType.TYPEAHEAD}.get(
                name, FieldType.TEXT
            )
    if kind is None:
        reason = f"unhandled_rendering: {raw['label'] or name!r} is a {raw['type']} input"
        return Field(name, raw["label"], FieldType.TEXT, bool(raw["required"]), unfillable=reason)
    return Field(
        name, raw["label"], kind, bool(raw["required"]), options, None, raw["accept"],
        raw["prefilled"],
    )  # fmt: skip
