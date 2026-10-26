"""isolved Hire (`<employer>.isolvedhire.com/jobs/<id>`), as docs/adapters/isolvedhire.md
observes.

Applying needs an account, one for the whole system, kept signed in per employer's site: a form
on a site not signed in yet signs in there again. The form is a wizard at `/apply/<id>-0.php`
whose steps the page loads from `/apply/view.php`; the read fetches each step from there, the
fill walks the steps to Submit Application, saving each with its Proceed to Next Step, and the
read-back fetches the steps again, as the system saved them.
"""

import re
import time
from contextlib import suppress
from typing import Any
from urllib.parse import parse_qs, urlsplit

from atsfill.core.adapter import (
    Blocked,
    Gap,
    NotTaken,
    Refused,
    SignUpDetails,
    Unsupported,
    known_gap,
)
from atsfill.core.browser import Locator, Page, Request, Timeout
from atsfill.core.results import (
    Answer,
    AnyOf,
    Condition,
    Field,
    FieldType,
    Is,
    OptionSet,
    SideEffect,
)

HOST = re.compile(r"^(?!(?:www|app|admin)\.)([a-z0-9-]+)\.isolvedhire\.com$")
JOB = re.compile(r"^/(?:jobs/(\d+)|apply/(\d+)-\d+\.php)/?$")
LOGIN, SIGN_UP, LOGOUT = "/account/login.php", "/account/", "/account/logout.php"
SIGNED_IN = f"a[href='{LOGOUT}']"
THANKS = "/apply/thanks.php"
ERRORS = "#landingStrip [id^=message_]:visible, #landingStrip .text-danger:visible"
PAGE_TIMEOUT_MS = 30_000
STEP_TIMEOUT_MS = 30_000
LIST_TIMEOUT_MS = 10_000  # a dependent list's options, a follow-up question
UPLOAD_TIMEOUT_MS = 60_000
SUBMIT_TIMEOUT_MS = 60_000
SETTLE_TIMEOUT_MS = 5_000  # an answer's save, sent as it is entered
ACCOUNT_TIMEOUT_S = 30
POLL_MS = 250
MAX_DEPTH = 3  # follow-up questions under follow-up questions

MOBILE = "2"  # the phone type's option for a cell phone
NO_TEXTS = "2"  # the text message consent's option that declines it
NANP = "+1"  # the calling code a US or Canadian number goes without
NANP_COUNTRIES = ("US", "CA")
YES_NO = ["Yes", "No"]
OTHER_SOURCE = "1"  # the source Other's value, added to every type's list
REFERRAL = "4"  # the source type whose source the applicant describes
FILES = [".pdf", ".doc", ".docx", ".txt"]
SKIPPED = ("instructions", "submit")  # steps without fields: text, then the send
ENTRY_LISTS = ("employment", "education")  # a list of entries, each added with its own form

# A step's fields from its HTML, in page order: each control's key (a group's name), kind,
# label, whether required, options, value, and for a question that opens follow-ups, the
# option that opens them.
_READ = """html => {
  const doc = new DOMParser().parseFromString(`<div id="root">${html}</div>`, "text/html");
  const root = doc.getElementById("root");
  const clean = s => (s || "").replace(/\\s+/g, " ").trim();
  const bare = s => clean(s).replace(/[*:]+$/, "").trim();
  const heading = el => {
    const item = el.closest("li.list-group-item");
    let prev = item && item.previousElementSibling;
    return prev ? clean(prev.querySelector("h2, h4")?.textContent) : "";
  };
  const labelOf = el => {
    const byFor = el.id && root.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (byFor && bare(byFor.textContent)) return bare(byFor.textContent);
    if (el.dataset.questionTitle) return clean(el.dataset.questionTitle);
    const legend = el.closest("fieldset")?.querySelector("legend");
    if (legend) return clean(legend.textContent);
    return heading(el) || el.name || el.id;
  };
  const placeholder = o => !o.value || /^[.\\-\\s]*$/.test(o.textContent) ||
    /^--/.test(clean(o.textContent));
  const out = [], seen = new Set();
  if (root.querySelector("canvas.pad")) {
    out.push({key: "signature", kind: "signature", label: "Signature", required: true,
              options: [], value: "", disabled: false});
  }
  for (const el of root.querySelectorAll("input, select, textarea")) {
    const type = (el.type || el.tagName).toLowerCase();
    if (["hidden", "submit", "button"].includes(type)) continue;
    if (el.closest("#faq_bar_form, #text_resume, #verify_contact_info_popup")) continue;
    if (el.name === "email_pls") continue;
    const group = ["radio", "checkbox"].includes(type) &&
      root.querySelectorAll(`input[type=${type}][name="${CSS.escape(el.name)}"]`).length > 1;
    const key = group || type === "file" ? el.name : el.id || el.name;
    if (!key || seen.has(key)) continue;
    seen.add(key);
    const field = {key, kind: type, label: labelOf(el), required: el.classList.contains("required"),
                   options: [], value: el.value || "", disabled: el.disabled,
                   maxLength: Number(el.dataset.maxlength) || null};
    if (el.tagName === "SELECT") {
      field.kind = "select";
      const options = [...el.options];
      field.options = options.filter(o => !placeholder(o)).map(o => clean(o.textContent));
      field.values = options.filter(o => !placeholder(o)).map(o => o.value);
      const picked = options.find(o => o.hasAttribute("selected"));
      field.value = picked && !placeholder(picked) ? clean(picked.textContent) : "";
      field.picked = picked?.value || "";
      field.saved = root.querySelector(`input[type=hidden][name="${CSS.escape(el.name)}"]`)
        ?.value || "";
      if (el.classList.contains("conditional-answer-select")) {
        const at = Number(el.dataset.conditional_answer);
        field.opens = {option: clean(options[at]?.textContent), question: el.dataset.id,
                       group: el.dataset.group_id};
      }
    } else if (el.tagName === "TEXTAREA") {
      field.kind = "text";
      field.value = el.textContent;
    } else if (group) {
      const all = [...root.querySelectorAll(`input[type=${type}][name="${CSS.escape(el.name)}"]`)];
      field.kind = type === "radio" ? "radio" : "checkboxes";
      field.options = all.map(r => clean(r.closest("label")?.textContent || r.value));
      field.value = all.filter(r => r.hasAttribute("checked"))
        .map(r => clean(r.closest("label")?.textContent || r.value));
      field.required = all.some(r => r.classList.contains("required"));
      if (type === "radio") field.value = field.value[0] || "";
    } else if (type === "checkbox") {
      field.label = clean(el.closest("label")?.textContent) || field.label;
      field.value = el.hasAttribute("checked") ? "Yes" : "No";
    } else if (type === "file") {
      const files = [...root.querySelectorAll(".uploaded-resumes-container .file p")];
      field.label = "Resume";
      field.value = clean(files[files.length - 1]?.textContent);
    } else field.kind = "text";
    out.push(field);
  }
  const app = root.querySelector("input[name=applications_id]")?.value;
  return {fields: out, app};
}"""

# The page's own request for a step, a follow-up question or a source list.
_VIEW = """async ([f, params]) => {
  const token = document.querySelector("input[name=csrf_token]")?.value || "";
  const query = new URLSearchParams({n: "apply", f, csrf_token: token, ...params});
  const response = await fetch(`/apply/view.php?${query}`, {credentials: "include"});
  return response.ok ? await response.text() : null;
}"""

_OPTIONS = """html => {
  const doc = new DOMParser().parseFromString(`<select>${html}</select>`, "text/html");
  return [...doc.querySelectorAll("option")].filter(o => o.value)
    .map(o => [o.value, o.textContent.replace(/\\s+/g, " ").trim()]);
}"""


def _job(url: str) -> tuple[str, str] | None:
    """The employer's host and the job's id."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    match = JOB.match(parts.path)
    if not HOST.match(host) or not match:
        return None
    return host, match[1] or match[2]


class IsolvedHire:
    name = "isolvedhire"
    per_employer = False
    asks = {"name", "phone", "street", "city", "state", "zip", "country"}
    known_gaps = {
        "sign_up_otp": Gap.FLOW,  # why the sign-up goes through the sign-in, not Apply now
        "entry_list": Gap.RENDERING,
        "drawn_signature": Gap.RENDERING,
    }
    side_effects = [
        SideEffect("sign_up", "hiring_system", "login --create creates the job profile"),
        SideEffect("load", "employer", "opening the form starts an application, unfinished"),
        SideEffect("fill", "employer", "each step's answers are saved to the application"),
        SideEffect("upload", "employer", "a resume is attached to the application on upload"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._host: str | None = None  # the last posting's employer site
        self._job: str | None = None
        self._steps: list[str] = []
        self._fields: dict[str, Field] = {}
        self._raws: dict[str, dict[str, Any]] = {}  # by field id, as the read found it

    def detect(self, url: str, page: Page | None) -> bool:
        return _job(url) is not None

    def posting(self, url: str) -> str:
        found = _job(url)
        return f"https://{found[0]}/jobs/{found[1]}" if found else url

    def account_scope(self, url: str) -> None:
        if found := _job(url):
            self._host, self._job = found
        return None

    def login_url(self, scope: str | None) -> str:
        """The sign-in on the last posting's employer site: the system has none of its own."""
        if self._host is None:
            raise ValueError("isolvedhire signs in on an employer's site: pass --url <posting>")
        return f"https://{self._host}{LOGIN}"

    def signed_in(self, request: Request, scope: str | None) -> bool:
        """The last posting's employer site shows the account's Logout link."""
        if self._host is None:
            return False
        response = request.get(f"https://{self._host}{SIGN_UP}")
        return response.ok and f'href="{LOGOUT}"' in response.text()

    def sign_in(self, page: Page, email: str, password: str) -> None:
        page.locator("#login #email").wait_for(timeout=PAGE_TIMEOUT_MS)
        page.locator("#login #email").fill(email)
        page.locator("#login #password").fill(password)
        page.locator("#login [type=submit]").click()
        _signed(page)

    def sign_up(self, page: Page, email: str, password: str, details: SignUpDetails) -> None:
        """Creates the job profile from the sign-in's "Need an account?", signed in once
        submitted: the name, the phone as a cell phone, the address, the country by its English
        name and the state as the country's list names it; text messages declined, the privacy
        policy agreed to."""
        page.locator(f"a[href='{SIGN_UP}']").first.click()
        form = page.locator("form#apply")
        form.wait_for(timeout=PAGE_TIMEOUT_MS)
        first, _, last = (details.name or "").strip().rpartition(" ")
        country = page.evaluate(
            "code => new Intl.DisplayNames(['en'], {type: 'region'}).of(code)", details.country
        )
        if country not in _texts(form.locator("#country_id option")):
            raise Refused(f"sign_up_details: no country {country} in the sign-up's list")
        form.locator("#country_id").select_option(label=country)
        state = form.locator("#state_id option", has_text=re.compile(rf"^\s*{details.state}\s*$"))
        try:
            state.first.wait_for(state="attached", timeout=LIST_TIMEOUT_MS)
        except Timeout:
            detail = f"no state {details.state} in {country}'s list"
            raise Refused(f"sign_up_details: {detail}") from None
        phone = details.phone or ""
        if details.country in NANP_COUNTRIES:
            phone = phone.removeprefix(NANP)
        for field, value in [
            ("first_name", first or last), ("last_name", last), ("email", email),
            ("email_confirm", email), ("contact_number", phone),
            ("street1", details.street), ("city", details.city), ("zip_code", details.zip),
            ("password", password), ("password_confirm", password),
        ]:  # fmt: skip
            form.locator(f"#{field}").fill(value or "")
        form.locator("#state_id").select_option(state.first.get_attribute("value"))
        form.locator("#contact_number_type").select_option(MOBILE)
        form.locator("#applicant_consent_approval").select_option(NO_TEXTS)
        form.locator("#info_use_consent").check()
        form.locator("#submit_apply").click()
        _signed(page)

    def browser_read(self, page: Page) -> list[Field]:
        """Opens the wizard, then lists each step's fields as `/apply/view.php` serves it."""
        self._open(page)
        self._steps = [
            s for s in page.locator("#steps a.step").evaluate_all(
                "as => as.map(a => a.dataset.file)") if s not in SKIPPED
        ]  # fmt: skip
        fields: list[Field] = []
        for step in self._steps:
            fields += self._step_fields(page, step)
        self._fields = {f.id: f for f in fields}
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        """Confirms the account's contact details, then walks the steps in order: each answered
        and saved with Proceed to Next Step, which goes no further while one is invalid."""
        _confirm_contact(page)
        for step in self._steps:
            _go(page, step)
            for field_id, field in self._fields.items():
                if field_id.startswith(f"{step}.") and field_id in answers:
                    self._answer(page, field, answers[field_id])
            _settle(page)
            _next(page, step)

    def read_back(self, page: Page) -> dict[str, Answer]:
        """Each step as the system saved it, fetched again."""
        values: dict[str, Answer] = {}
        for step in self._steps:
            if step in ENTRY_LISTS:
                continue
            raws, app = _read_step(page, step)
            for raw in list(raws):
                raws += self._children(page, raw, app, 1, values_only=True)
            for raw in raws:
                field_id = f"{step}.{raw['key']}"
                if field_id in self._fields and raw["value"] not in ("", [], None):
                    values[field_id] = raw["value"]
        return values

    def submit(self, page: Page) -> str | None:
        """Submit Application, on the last step once every required one is complete; the page
        moves to the thanks page, the evidence."""
        _go(page, "submit")
        button = page.locator("#landingStrip #next")
        if not button.count():
            text = " ".join(page.locator("#landingStrip").inner_text().split())
            raise RuntimeError(f"the Submit Application step is not ready: {text}")
        button.click()
        page.wait_for_url(f"**{THANKS}*", timeout=SUBMIT_TIMEOUT_MS)
        return page.url

    def _open(self, page: Page) -> None:
        found = _job(page.url) or ((self._host, self._job) if self._host and self._job else None)
        if found is None:
            raise Unsupported("unhandled_rendering: not an isolved Hire posting")
        self._host, self._job = found
        page.goto(f"https://{self._host}/apply/{self._job}-0.php")
        if urlsplit(page.url).path == LOGIN:
            raise Blocked("login_wall: the application redirects to the sign-in")
        try:
            page.locator("#steps a.step").first.wait_for(state="attached", timeout=PAGE_TIMEOUT_MS)
        except Timeout:
            text = " ".join(page.locator("body").inner_text().split())[:300]
            raise Unsupported(f"posting_gone: no application wizard: {text}") from None

    def _step_fields(self, page: Page, step: str) -> list[Field]:
        if step in ENTRY_LISTS:
            reason = known_gap(self, "entry_list", f"the {step} step adds entries one by one")
            return [Field(f"{step}.entries", step.title(), FieldType.TEXT, True,
                          unfillable=reason)]  # fmt: skip
        raws, app = _read_step(page, step)
        fields = []
        for raw in raws:
            fields.append(self._field(page, step, raw))
            for child, condition in self._children(page, raw, app, 1):
                fields.append(self._field(page, step, child, condition))
        return fields

    def _children(
        self,
        page: Page,
        raw: dict[str, Any],
        app: str | None,
        depth: int,
        values_only: bool = False,
    ) -> list[Any]:
        """The follow-up questions a question opens, fetched as the page fetches them, each with
        the condition that shows it; only the raws, for the read-back."""
        opens = raw.get("opens")
        if not opens or depth > MAX_DEPTH or (values_only and raw["value"] != opens["option"]):
            return []
        params = {"parent_question_id": opens["question"], "group_id": opens["group"],
                  "app_id": app or ""}  # fmt: skip
        html = page.evaluate(_VIEW, ["buildAdditionalQuestions", params]) or ""
        found = []
        for child in page.evaluate(_READ, html)["fields"]:
            found.append(child if values_only else (child, Is(raw["key"], [opens["option"]])))
            found += self._children(page, child, app, depth + 1, values_only)
        return found

    def _field(self, page: Page, step: str, raw: dict[str, Any], shown: Is | None = None) -> Field:
        field_id, label, required = f"{step}.{raw['key']}", raw["label"], raw["required"]
        if shown is not None:
            shown = Is(f"{step}.{shown.field_id}", shown.values)
        self._raws[field_id] = raw
        locked = raw["value"] if raw["disabled"] and raw["value"] else None  # a saved source
        match raw["kind"]:
            case "signature":
                reason = known_gap(self, "drawn_signature", "the step asks a drawn signature")
                return Field(field_id, label, FieldType.TEXT, True, unfillable=reason)
            case "select" if raw["key"] == "source_id":
                return Field(field_id, label, FieldType.CHOICE, required, [],
                             option_sets=_sources(page, step), prefilled=locked)  # fmt: skip
            case "select" | "radio":
                return Field(field_id, label, FieldType.CHOICE, required, raw["options"],
                             prefilled=locked, default=raw["value"] or None,
                             shown_when=shown)  # fmt: skip
            case "checkboxes":
                return Field(field_id, label, FieldType.MULTI_CHOICE, required, raw["options"],
                             shown_when=shown)  # fmt: skip
            case "checkbox":
                return Field(field_id, label, FieldType.CHOICE, required, YES_NO, default="No",
                             shown_when=shown)  # fmt: skip
            case "file":
                return Field(field_id, label, FieldType.FILE, required, accept=FILES,
                             prefilled=raw["value"] or None)  # fmt: skip
            case "text" if raw["key"] == "source_other":
                return Field(field_id, label, FieldType.TEXT, True, None, raw["maxLength"],
                             prefilled=locked, shown_when=self._other_source(step))  # fmt: skip
            case "text":
                required = required and not raw["disabled"]
                return Field(field_id, label, FieldType.TEXT, required, None, raw["maxLength"],
                             shown_when=shown)  # fmt: skip
        reason = f"unhandled_rendering: {field_id} is a {raw['kind']} input"
        return Field(field_id, label, FieldType.TEXT, required, unfillable=reason)

    def _other_source(self, step: str) -> Condition:
        """Where the source asks to describe it: Other, or a referral (its type's value 4)."""
        other: Condition = Is(f"{step}.source_id", ["Other"])
        types = self._raws.get(f"{step}.source_type_id", {})
        referral = dict(zip(types.get("values", []), types.get("options", []), strict=False))
        if REFERRAL in referral:
            return AnyOf([other, Is(f"{step}.source_type_id", [referral[REFERRAL]])])
        return other

    def _answer(self, page: Page, field: Field, answer: Answer) -> None:
        raw = self._raws[field.id]
        key = raw["key"]
        strip = page.locator("#landingStrip")
        values = [answer] if isinstance(answer, str) else answer
        if raw["disabled"] and raw["value"]:
            if answer != raw["value"]:
                raise NotTaken(f"{field.id} keeps {raw['value']!r}, saved before")
            return
        match raw["kind"]:
            case "select":
                control = strip.locator(f'select[id="{key}"], select[name="{key}"]').first
                if key == "source_id":  # its list loads once a source type is picked
                    _wait_option(control, values[0])
                control.select_option(_option_value(control, values[0], field.id))
                if raw.get("opens"):
                    _follow_ups(page, raw["opens"]["question"], values[0] == raw["opens"]["option"])
            case "radio" | "checkboxes":
                boxes = f'input:is([type=radio], [type=checkbox])[name="{key}"]'
                for box in strip.locator(boxes).all():
                    text = " ".join((box.locator("xpath=ancestor::label[1]").text_content()
                                     or box.get_attribute("value") or "").split())  # fmt: skip
                    if raw["kind"] == "radio" and text == values[0]:
                        box.check()
                        break
                    if raw["kind"] == "checkboxes":
                        box.set_checked(text in values)
                else:
                    if raw["kind"] == "radio":
                        raise NotTaken(f"no option {values[0]!r} in {field.id}")
            case "checkbox":
                strip.locator(f'input[type=checkbox][id="{key}"]').set_checked(values[0] == "Yes")
            case "file":
                _upload(page, values[0])
            case _:
                control = strip.locator(f'[id="{key}"], [name="{key}"]').first
                if control.is_disabled():
                    raise NotTaken(f"{field.id} is disabled for the answers given")
                control.fill(values[0])
                control.blur()  # a question's answer is saved on blur


def _read_step(page: Page, step: str) -> tuple[list[dict[str, Any]], str | None]:
    """A step's raw fields as `/apply/view.php` serves it, and its application's id. A saved
    source is locked, its source kept in a hidden input: its name is looked up in its type's
    list."""
    html = page.evaluate(_VIEW, [step, {}])
    if html is None:
        raise RuntimeError(f"the {step} step did not load")
    read = page.evaluate(_READ, html)
    raws = read["fields"]
    by_key = {r["key"]: r for r in raws}
    source, kind = by_key.get("source_id"), by_key.get("source_type_id")
    if source and kind and source.get("saved"):
        if source["saved"] == OTHER_SOURCE:
            source["value"] = "Other"
        else:
            params = {"source_type_id": kind["picked"], "selected_id": "", "hide_internal": "1"}
            listed = page.evaluate(_OPTIONS, page.evaluate(_VIEW, ["getSources", params]) or "")
            source["value"] = dict(listed).get(source["saved"], "")
    return raws, read["app"]


def _texts(options: Locator) -> list[str]:
    return [" ".join(t.split()) for t in options.all_text_contents()]


def _option_value(select: Locator, text: str, field_id: str) -> str:
    for option in select.locator("option").all():
        if " ".join((option.text_content() or "").split()) == text:
            return option.get_attribute("value") or ""
    raise NotTaken(f"no option {text!r} in {field_id}")


def _sources(page: Page, step: str) -> list[OptionSet]:
    """The sources of each source type, fetched as the page fetches them once one is picked;
    Other ends every list."""
    html = page.evaluate(_VIEW, [step, {}]) or ""
    types = page.evaluate(
        """html => [...new DOMParser().parseFromString(html, "text/html")
            .querySelectorAll("#source_type_id option")].filter(o => o.value)
            .map(o => [o.value, o.textContent.replace(/\\s+/g, " ").trim()])""",
        html,
    )
    sets = []
    for value, text in types:
        params = {"source_type_id": value, "selected_id": "", "hide_internal": "1"}
        listed = page.evaluate(_OPTIONS, page.evaluate(_VIEW, ["getSources", params]) or "")
        options = [name for _, name in listed]
        sets.append(OptionSet(Is(f"{step}.source_type_id", [text]), [*options, "Other"]))
    return sets


def _wait_option(select: Locator, text: str) -> None:
    with suppress(Timeout):
        select.locator("option", has_text=text).first.wait_for(
            state="attached", timeout=LIST_TIMEOUT_MS
        )


def _follow_ups(page: Page, question: str, opened: bool) -> None:
    """Waits for the follow-up questions an answer opens to load, or for them to go."""
    div = page.locator(f"#conditional_questions_div_{question}")
    with suppress(Timeout):
        if opened:
            div.locator("input, select, textarea").first.wait_for(timeout=LIST_TIMEOUT_MS)
        else:
            div.locator("input, select, textarea").first.wait_for(
                state="detached", timeout=LIST_TIMEOUT_MS
            )


def _confirm_contact(page: Page) -> None:
    """The wizard opens on the account's contact details, asking on each employer's site to
    agree to the privacy policy again: saved as they are, text messages declined where no
    choice is saved yet."""
    popup = page.locator("#verify_contact_info_popup")
    with suppress(Timeout):  # shown once faded in
        page.locator("#verify_contact_info_popup.in").wait_for(timeout=SETTLE_TIMEOUT_MS)
    if not popup.is_visible():
        return
    consent = popup.locator("#applicant_consent_approval")
    if consent.count() and not consent.input_value():
        consent.select_option(NO_TEXTS)
    popup.locator("#info_use_consent").check()
    popup.locator("#verify_contact_info_button").click()
    try:
        popup.wait_for(state="hidden", timeout=STEP_TIMEOUT_MS)
    except Timeout:
        said = " ".join(" ".join(popup.locator("[id^=message_]:visible").all_inner_texts()).split())
        raise NotTaken(f"the contact details were not saved: {said or 'no error shown'}") from None


def _current(page: Page) -> str | None:
    return page.locator("#steps a.step.current").first.get_attribute("data-file")


def _go(page: Page, step: str) -> None:
    """Opens a step from the steps list, once it has loaded."""
    if _current(page) != step:
        page.locator(f"#steps a.step[data-file='{step}']").click()
    page.locator(f"#steps a.step.current[data-file='{step}']").wait_for(
        state="attached", timeout=STEP_TIMEOUT_MS
    )
    page.locator("#landingStrip #next, #landingStrip #previous").first.wait_for(
        timeout=STEP_TIMEOUT_MS
    )


def _next(page: Page, step: str) -> None:
    """Proceed to Next Step: the step is saved once the next one is current, and stays with its
    errors shown while an answer is missing or invalid."""
    page.locator("#landingStrip #next").click()
    deadline = time.monotonic() + STEP_TIMEOUT_MS / 1000
    errors = page.locator(ERRORS)
    while time.monotonic() < deadline:
        if _current(page) != step:
            page.locator("#landingStrip #previous").first.wait_for(timeout=STEP_TIMEOUT_MS)
            return
        if errors.count():
            said = " ".join(" ".join(errors.all_inner_texts()).split())
            raise NotTaken(f"the {step} step was not saved: {said}")
        page.wait_for_timeout(POLL_MS)
    raise RuntimeError(f"Proceed to Next Step on the {step} step went nowhere")


def _settle(page: Page) -> None:
    """Lets the answers saved as they were entered reach the system."""
    with suppress(Timeout):
        page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)


def _upload(page: Page, path: str) -> None:
    """Chooses the file, then Upload Files, which the step shows once a file is chosen; the step
    loads again listing the file."""
    name = path.rsplit("/", 1)[-1]
    listed = page.locator(".uploaded-resumes-container .file p", has_text=name)
    before = listed.count()
    page.locator("#default_resume").set_input_files(path)
    page.locator("#new_upload_button").click(timeout=STEP_TIMEOUT_MS)
    deadline = time.monotonic() + UPLOAD_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        with suppress(Exception):
            if listed.count() > before:
                return
        page.wait_for_timeout(POLL_MS)
    raise RuntimeError(f"the upload of {name} was not listed")


def _signed(page: Page) -> None:
    """Waits for the page to show the account signed in, or for the error the sign-in shows."""
    deadline = time.monotonic() + ACCOUNT_TIMEOUT_S
    while time.monotonic() < deadline:
        if parse_qs(urlsplit(page.url).query).get("error"):
            raise Refused(f"wrong_password: {_said(page)}")
        if page.locator(SIGNED_IN).count():
            return
        page.wait_for_timeout(POLL_MS)
    raise Unsupported(f"unhandled_rendering: the account page says {_said(page)!r}")


def _said(page: Page) -> str:
    return " ".join(page.locator("body").inner_text().split())[:500]
