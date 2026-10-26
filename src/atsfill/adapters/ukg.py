"""UKG Pro Recruiting (`recruiting[2].ultipro.com`, `recruiting.ultipro.ca`,
`<employer>.rec.pro.ukg.net`: `/<tenant>/JobBoard/<board>/OpportunityDetail?opportunityId=…`),
as docs/adapters/ukg.md observes.

Applying needs an account, one per tenant: the account scope. The form is one page, a
Knockout view of the posting, read from the page; the read-back is the page too.
"""

import re
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from atsfill.core.adapter import Blocked, Gap, NotTaken, Refused, SignUpDetails, Unsupported
from atsfill.core.browser import Locator, Page, Request, Timeout
from atsfill.core.results import Answer, Field, FieldType, Is, OptionSet, SideEffect

HOST = re.compile(r"^(recruiting\d*\.ultipro\.(com|ca)|[a-z0-9-]+\.rec\.pro\.ukg\.net)$")
PATH = re.compile(
    r"^/(?P<tenant>[A-Za-z0-9]+)/JobBoard/(?P<board>[0-9a-fA-F-]{36})/Opportunity(Detail|Apply)$"
)
SIGN_IN_HOST = re.compile(r"^signin-[a-z0-9]+\.ultipro\.com$")
SESSION_COOKIE = ".AspNetCore.Cookies"
FORM = "#OpportunityApply"
READY = f"{FORM} [data-automation=btn-submit]"
FORM_TIMEOUT_MS = 30_000
SIGN_IN_TIMEOUT_S = 30
SIGN_IN_POLL_MS = 500
STATES_TIMEOUT_MS = 10_000
UPLOAD_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
SETTLE_MS = 500
YES_NO = ["Yes", "No"]
DATE_SHOWN = re.compile(r"(\d\d)/(\d\d)/(\d{4})")
COUNTRY, STATE = "Country", "State"
HISPANIC = "HispanicOrigin"  # race (`EthnicOrigin`) is asked when not Hispanic or Latino
DECLINE_BOX = "input[type=checkbox][data-automation$='-decline-checkbox']"
DAYS, DOCUMENTS = "availability_days", "documents"

# Auth0's error codes on the sign-in and sign-up pages, by what atsfill raises
ERRORS = [
    (re.compile(r"wrong-(credentials|email-credentials)|invalid-(password|email-password)"),
     Refused, "wrong_password"),
    (re.compile(r"user-exists|email-in-use|already"), Refused, "email_in_use"),
    (re.compile(r"password"), Refused, "password_rule"),
    (re.compile(r"captcha"), Blocked, "captcha"),
    (re.compile(r"too-many|blocked|rate"), Blocked, "rate_limited"),
]  # fmt: skip

# Each visible field of the form, in page order: a control's id (a radio group's name), its
# label, how it renders, whether it is required, its options and value.
_READ = """root => {
  const clean = s => (s || "").replace(/\\s+/g, " ").trim();
  const shown = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) &&
    !el.closest(".modal");
  const labelOf = el => {
    if (el.id) {
      const l = root.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return l;
    }
    const group = el.closest("[aria-labelledby]");
    if (group) {
      const l = document.getElementById(group.getAttribute("aria-labelledby").split(" ")[0]);
      if (l) return l;
    }
    return el.closest(".form-group")?.querySelector("label");
  };
  const required = (el, label) => el.required || !!label?.classList.contains("required");
  const select = (el, field) => {
    field.kind = "select";
    field.options = [...el.options].filter(o => o.value).map(o => clean(o.textContent));
    field.value = el.value ? clean(el.selectedOptions[0]?.textContent) : "";
    const box = el.closest(".form-group")?.querySelector(
      "input[type=checkbox][data-automation$='-decline-checkbox']");
    if (box) {
      field.decline = clean(box.closest("label")?.querySelector("span:not(.sr-only)")
        ?.textContent);
      field.options.push(field.decline);
      if (box.checked) field.value = field.decline;
    }
    return field;
  };
  const out = [], seen = new Set();
  for (const el of root.querySelectorAll("input, select, textarea")) {
    const type = (el.type || "").toLowerCase();
    if (["hidden", "file", "submit", "button"].includes(type) || !shown(el)) continue;
    if (el.matches("[data-automation$='-decline-checkbox']")) continue;  // its select's option
    if (el.closest("ukg-input, ukg-input-container, [data-automation=upload-resume]")) continue;
    const id = type === "radio" ? el.name : el.id || el.name || el.dataset.automation;
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const label = labelOf(el);
    const field = {id, label: clean(label?.textContent), required: required(el, label),
                   kind: type, options: [], value: el.value,
                   readonly: el.readOnly || el.disabled, maxLength: el.maxLength > 0 ?
                   el.maxLength : null};
    if (el.tagName === "SELECT") select(el, field);
    else if (el.tagName === "TEXTAREA") field.kind = "text";
    else if (type === "radio") {
      const all = [...root.querySelectorAll(`input[type=radio][name="${CSS.escape(id)}"]`)];
      field.options = all.map(r => clean(r.closest("label")?.textContent));
      field.required = all.some(r => r.required) || field.required;
      field.value = clean(all.find(r => r.checked)?.closest("label")?.textContent);
    } else if (type === "checkbox") {
      field.label = clean(el.closest("label")?.textContent) || field.label;
      field.value = el.checked ? "Yes" : "No";
    } else if (type === "tel") field.kind = "phone";
    else if (["text", "email", "number", ""].includes(type)) field.kind = "text";
    out.push(field);
  }
  for (const box of root.querySelectorAll("ukg-input[type=date]")) {
    if (!shown(box)) continue;
    const bridge = box.closest("react-ko-es6-bridge") || box.parentElement;
    const named = bridge.querySelector("[id]");
    const id = named?.id;
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const label = bridge.closest(".form-group")?.querySelector("label") || named;
    out.push({id, label: clean(label.textContent), kind: "date",
              required: box.getAttribute("required") === "true" ||
                !!label.classList?.contains("required"),
              options: [], value: box.getAttribute("value") || ""});
  }
  const race = root.querySelector("select#EthnicOrigin");
  const hispanic = root.querySelector("select#HispanicOrigin");
  if (race && hispanic && out.some(f => f.id === "HispanicOrigin")) {
    const kept = out.findIndex(f => f.id === "EthnicOrigin");
    if (kept >= 0) out.splice(kept, 1);
    const label = labelOf(race);
    out.splice(out.findIndex(f => f.id === "HispanicOrigin") + 1, 0, select(race, {
      id: race.id, label: clean(label?.textContent), required: required(race, label),
      readonly: false, maxLength: null,
      shownWhen: clean([...hispanic.options].find(o => o.value === "No")?.textContent)}));
  }
  const days = [...root.querySelectorAll("[data-automation=day-btn]")].filter(shown);
  if (days.length) {
    const label = document.getElementById("AvailabilityDaysQuestion");
    out.push({id: "availability_days", kind: "days", required: false,
              label: clean(label?.textContent) || "Which days are you available to work?",
              options: days.map(d => clean(d.textContent)),
              value: days.filter(d => d.classList.contains("selected"))
                .map(d => clean(d.textContent))});
  }
  const upload = [...root.querySelectorAll("form[action*='/Documents/UploadFile']")]
    .find(f => shown(f.closest(".panel, section, div") || f));
  if (upload) {
    const input = upload.querySelector("input[type=file]");
    out.push({id: "documents", kind: "file", label: "Documents", required: false,
              options: [], accept: (input?.accept || "").split(",").filter(Boolean),
              value: ""});
  }
  return out;
}"""

# What each listed field shows now, by its kind.
_READ_BACK = """(root, kinds) => {
  const clean = s => (s || "").replace(/\\s+/g, " ").trim();
  const shown = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const out = {};
  for (const [id, kind] of Object.entries(kinds)) {
    if (kind === "days") {
      out[id] = [...root.querySelectorAll("[data-automation=day-btn]")].filter(shown)
        .filter(d => d.classList.contains("selected")).map(d => clean(d.textContent));
    } else if (kind === "file") {
      out[id] = [...root.querySelectorAll("[data-automation=file-name]")].filter(shown)
        .map(f => clean([...f.querySelectorAll("[id^=FileName], [data-automation=file-link]")]
          .filter(shown).map(e => e.textContent).join(""))).filter(Boolean).pop() || null;
    } else if (kind === "date") {
      const named = root.querySelector(`[id="${CSS.escape(id)}"]`);
      const box = named?.closest("react-ko-es6-bridge, div")?.querySelector("ukg-input");
      out[id] = box?.getAttribute("value") || null;
    } else if (kind === "radio") {
      const checked = [...root.querySelectorAll(`input[type=radio][name="${CSS.escape(id)}"]`)]
        .find(r => r.checked);
      out[id] = checked ? clean(checked.closest("label")?.textContent) : null;
    } else {
      const named = CSS.escape(id);
      const el = [...root.querySelectorAll(
        `[id="${named}"], [name="${named}"], [data-automation="${named}"]`)].find(shown);
      if (!el) continue;
      const box = el.closest(".form-group")?.querySelector(
        "input[type=checkbox][data-automation$='-decline-checkbox']");
      if (kind === "select" && box?.checked) {
        out[id] = clean(box.closest("label")?.querySelector("span:not(.sr-only)")?.textContent);
      } else if (kind === "select") {
        out[id] = el.value ? clean(el.selectedOptions[0]?.textContent) : null;
      } else if (kind === "checkbox") out[id] = el.checked ? "Yes" : "No";
      // a Numeric question's autoNumeric input groups its number: 50,000
      else if (el.dataset.automation === "numeric-response") out[id] = el.value.replace(/,/g, "");
      else out[id] = el.value;
    }
  }
  return out;
}"""

# Each country's states from the board's states URL, by the country's name.
_STATES = """async (root, url) => {
  const select = root.querySelector("select#Country");
  const countries = [...(select?.options || [])].filter(o => o.value);
  const lists = await Promise.all(countries.map(async o => {
    const response = await fetch(`${url}?countryId=${o.value}`, {credentials: "include"});
    return response.ok ? await response.json() : [];
  }));
  return Object.fromEntries(countries.map((o, i) => [
    o.textContent.replace(/\\s+/g, " ").trim(), (lists[i] || []).map(s => s.Name)]));
}"""

KINDS = {
    "text": FieldType.TEXT,
    "phone": FieldType.PHONE,
    "select": FieldType.CHOICE,
    "radio": FieldType.CHOICE,
    "checkbox": FieldType.CHOICE,
    "date": FieldType.DATE,
    "days": FieldType.MULTI_CHOICE,
    "file": FieldType.FILE,
}


@dataclass(frozen=True)
class Board:
    """A tenant's job board, where its postings and its sign-in are served."""

    host: str
    tenant: str  # lowercase
    board: str
    opportunity: str

    @property
    def base(self) -> str:
        return f"https://{self.host}/{self.tenant}/JobBoard/{self.board}"

    @property
    def apply(self) -> str:
        return f"{self.base}/OpportunityApply?opportunityId={self.opportunity}"


def board(url: str) -> Board | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    match = PATH.match(parts.path)
    opportunity = parse_qs(parts.query).get("opportunityId", [""])[0].lower()
    if not HOST.match(host) or not match or not opportunity:
        return None
    return Board(host, match["tenant"].lower(), match["board"].lower(), opportunity)


class UKG:
    name = "ukg"
    per_employer = True
    asks = {"name", "phone"}  # the phone only some tenants require, asked before the sign-up
    known_gaps: dict[str, Gap] = {}
    side_effects = [
        SideEffect("sign_up", "employer", "the account is created in the employer's tenant"),
        SideEffect("upload", "hiring_system", "a document is stored in the account's documents"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._boards: dict[str, Board] = {}  # by tenant, the last posting's
        self._kinds: dict[str, str] = {}
        self._declines: dict[str, str] = {}  # a select's decline box's label, by the select

    def detect(self, url: str, page: Page | None) -> bool:
        return board(url) is not None

    def posting(self, url: str) -> str:
        found = board(url)
        return f"{found.base}/OpportunityDetail?opportunityId={found.opportunity}" if found else url

    def account_scope(self, url: str) -> str | None:
        found = board(url)
        if found is None:
            return None
        self._boards[found.tenant] = found
        return found.tenant

    def login_url(self, scope: str | None) -> str:
        """The board's sign-in, back to the last posting's form once signed in."""
        if scope not in self._boards:
            raise ValueError(f"no UKG posting seen for the tenant {scope!r}")
        found = self._boards[scope]
        back = urlsplit(found.apply)
        return f"{found.base}/Account/Login?redirectUrl={quote(f'{back.path}?{back.query}')}"

    def signed_in(self, request: Request, scope: str | None) -> bool:
        """The posting's form loads, instead of redirecting to the sign-in or to the tenant's
        name step; without a posting
        seen, the tenant's session cookie is there and unexpired."""
        if scope in self._boards:
            response = request.get(self._boards[scope].apply)
            return response.ok and urlsplit(response.url).path.endswith("/OpportunityApply")
        cookies = request.storage_state()["cookies"]
        return any(
            c["name"] == SESSION_COOKIE and HOST.match(c["domain"].lstrip("."))
            and (c["expires"] < 0 or c["expires"] > time.time())
            for c in cookies
        )  # fmt: skip

    def sign_in(self, page: Page, email: str, password: str) -> None:
        _sign_in(page, email, password)
        if "/AuthCode/Register" in page.url:
            raise Refused(
                "sign_up_details: the account was left at the tenant's details step: "
                "`login --create --details` finishes it"
            )

    def finish_sign_up(self, page: Page, email: str, password: str, details: SignUpDetails) -> None:
        """Signs in and, where the account was left without its tenant details, gives them."""
        _sign_in(page, email, password)
        if "/AuthCode/Register" in page.url:
            _register(page, details)

    def sign_up(self, page: Page, email: str, password: str, details: SignUpDetails) -> None:
        page.locator("a[href*='/u/signup']").first.click()
        page.locator("input#email").wait_for(timeout=FORM_TIMEOUT_MS)
        page.locator("input#email").fill(email)
        page.locator("input#password").fill(password)
        page.locator("button[type=submit][name=action][data-action-button-primary]").click()
        _left_sign_in(page)
        if "/AuthCode/Register" not in page.url:
            return
        _register(page, details)

    def browser_read(self, page: Page) -> list[Field]:
        found = board(page.url)
        if found is None or not urlsplit(page.url).path.endswith("/OpportunityApply"):
            found = found or next(iter(self._boards.values()), None)
            if found is None:
                raise Unsupported("unhandled_rendering: not a UKG posting page")
            page.goto(found.apply)
        if SIGN_IN_HOST.match(urlsplit(page.url).hostname or ""):
            raise Blocked("login_wall: the form redirects to the sign-in")
        try:
            page.locator(READY).wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        page.wait_for_timeout(SETTLE_MS)
        raws = page.locator(FORM).evaluate(_READ)
        required_resume = '"IsResumeRequired":true' in page.content()
        states = None
        # with the address optional, the contact information is the account's, a read-only
        # panel whose Country and State are in a closed edit dialog
        if page.locator(f"{FORM} select#{STATE}").count() and any(r["id"] == COUNTRY for r in raws):
            states = page.locator(FORM).evaluate(_STATES, f"{found.base}/Candidate/GetStates")
            if not any(r["id"] == STATE for r in raws):  # shown once a country is picked
                label = page.locator(f"{FORM} label[for={STATE}]").first
                at = next((i + 1 for i, r in enumerate(raws) if r["id"] == "City"), len(raws))
                raws.insert(at, {
                    "id": STATE, "label": " ".join((label.text_content() or "").split()),
                    "kind": "select", "options": [], "value": "",
                    "required": "required" in (label.get_attribute("class") or ""),
                })  # fmt: skip
        self._kinds = {raw["id"]: raw["kind"] for raw in raws}
        self._declines = {raw["id"]: raw["decline"] for raw in raws if raw.get("decline")}
        fields = []
        for raw in raws:
            if raw["id"] == DOCUMENTS:
                raw["required"] = required_resume
            fields.append(_field(raw, states))
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        for field_id, kind in self._kinds.items():
            if field_id not in answers:
                continue
            answer = answers[field_id]
            values = [answer] if isinstance(answer, str) else answer
            root = page.locator(FORM)
            match kind:
                case "select" if field_id in self._declines:
                    declined = values[0] == self._declines[field_id]
                    _decline_box(root, field_id).set_checked(declined)
                    if not declined:
                        _control(root, field_id).select_option(label=values[0])
                case "select":
                    control = _control(root, field_id)
                    control.select_option(label=values[0])
                    if field_id == COUNTRY:
                        _wait_for_states(page)
                case "radio":
                    _option(root, field_id, values[0]).check()
                case "checkbox":
                    _control(root, field_id).set_checked(values[0] == "Yes")
                case "date":
                    _date(root, field_id, values[0])
                case "days":
                    for day in root.locator("[data-automation=day-btn]:visible").all():
                        text = (day.text_content() or "").strip()
                        if ("selected" in (day.get_attribute("class") or "")) != (text in values):
                            day.click()
                case "file":
                    _upload(page, values[0])
                case _:
                    _control(root, field_id).fill(values[0])

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = page.locator(FORM).evaluate(_READ_BACK, self._kinds)
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        """Clicks Submit; the application posts and the page moves to ApplicationSubmitted,
        the evidence. A tenant may have Cloudflare Turnstile check the visitor first."""
        page.locator(READY).click()
        try:
            page.wait_for_url(re.compile(r"/ApplicationSubmitted"), timeout=SUBMIT_TIMEOUT_MS)
        except Timeout:
            if page.locator("iframe[src*='challenges.cloudflare.com']").is_visible():
                raise Blocked("captcha: Cloudflare Turnstile asked for a challenge") from None
            raise
        return page.url


def _sign_in(page: Page, email: str, password: str) -> None:
    page.locator("input#username").wait_for(timeout=FORM_TIMEOUT_MS)
    page.locator("input#username").fill(email)
    page.locator("input#password").fill(password)
    page.locator("button[type=submit][name=action][data-action-button-primary]").click()
    _left_sign_in(page)


def _left_sign_in(page: Page) -> None:
    """Waits to leave the sign-in host, or for the error the page shows."""
    deadline = time.monotonic() + SIGN_IN_TIMEOUT_S
    said = page.locator(".ulp-input-error-message:visible, #prompt-alert:visible, "
                        "[id^=error-element-]:visible")  # fmt: skip
    while time.monotonic() < deadline:
        host = urlsplit(page.url).hostname or ""
        path = urlsplit(page.url).path
        if not SIGN_IN_HOST.match(host):
            page.wait_for_load_state()
            return
        if re.search(r"/u/(mfa|email-verification|login-email-verification)", path):
            raise Blocked("email_otp: the sign-in asks for a code sent by email")
        if page.locator("[data-captcha-provider]:visible, .ulp-captcha:visible").count():
            raise Blocked("captcha: the sign-in page shows a captcha")
        if said.count():
            text = " ".join(said.all_inner_texts()).strip()
            codes = (
                " ".join(filter(None, [e.get_attribute("data-error-code") for e in said.all()]))
                + " "
                + " ".join(filter(None, [e.get_attribute("id") for e in said.all()]))
            )
            for pattern, error, code in ERRORS:
                if pattern.search(codes.lower()) or pattern.search(text.lower()):
                    raise error(f"{code}: {text}")
            raise Unsupported(f"unhandled_rendering: the sign-in page says {text!r}")
        page.wait_for_timeout(SIGN_IN_POLL_MS)
    raise Unsupported("unhandled_rendering: the sign-in page answered nothing")


def _register(page: Page, details: SignUpDetails) -> None:
    """The tenant's own step after the sign-up: the name, the phone where the tenant requires
    it, and the privacy policy's box on some tenants; the optional consents (text messages, AI
    scoring, ticked by default) are declined. Its inputs take typed keys only: a filled value
    leaves Create account disabled."""
    page.locator("input[name=firstName]").wait_for(timeout=FORM_TIMEOUT_MS)
    page.wait_for_load_state("networkidle")
    first, _, last = (details.name or "").strip().partition(" ")
    values = [("firstName", first), ("lastName", last or first)]
    if page.locator("ukg-input[name=phoneNumber][required=true]").count():
        values.append(("phoneNumber", details.phone or ""))
    for field, value in values:
        page.locator(f"input[name={field}]").click()
        page.locator(f"input[name={field}]").press_sequentially(value)
    for box in page.locator("ukg-checkbox[data-automation*=-checkbox]:visible").all():
        wanted = "privacy" in (box.get_attribute("data-automation") or "")
        if _ticked(box) != wanted:
            box.click()
    page.get_by_role("button", name="Create account").click()
    page.wait_for_url(lambda u: "/AuthCode/Register" not in u, timeout=FORM_TIMEOUT_MS)


def _ticked(box: Locator) -> bool:
    """A `ukg-checkbox`'s state: `aria-checked` once clicked, `checked` as the page set it."""
    return "true" in (box.get_attribute("aria-checked"), box.get_attribute("checked"))


def _field(raw: dict[str, Any], states: dict[str, list[str]] | None) -> Field:
    kind, field_id = raw["kind"], raw["id"]
    label = raw["label"] or field_id
    if kind not in KINDS:
        reason = f"unhandled_rendering: {field_id} is a {kind} input"
        return Field(field_id, label, FieldType.TEXT, raw["required"], unfillable=reason)
    value = raw.get("value") or None
    if kind == "checkbox":
        return Field(field_id, label, FieldType.CHOICE, raw["required"], YES_NO, default="No")
    if kind == "days":
        return Field(field_id, label, FieldType.MULTI_CHOICE, False, raw["options"])
    if kind == "file":
        return Field(field_id, label, FieldType.FILE, raw["required"], accept=raw["accept"])
    if raw.get("readonly"):  # the account's name, changed on its profile only
        reason = f"unsupported_rendering: {field_id} is read-only" if not value else None
        return Field(field_id, label, KINDS[kind], raw["required"], prefilled=value,
                     unfillable=reason)  # fmt: skip
    options = raw["options"] if kind in ("select", "radio") else None
    shown_when = Is(HISPANIC, [raw["shownWhen"]]) if raw.get("shownWhen") else None
    if field_id == STATE and states is not None:
        sets = [OptionSet(Is(COUNTRY, [country]), names) for country, names in states.items()]
        return Field(field_id, label, FieldType.CHOICE, raw["required"], [], option_sets=sets)
    return Field(
        field_id, label, KINDS[kind], raw["required"], options, raw.get("maxLength"),
        default=value if kind in ("date", "select") else None, shown_when=shown_when,
    )  # fmt: skip


def _wait_for_states(page: Page) -> None:
    with suppress(Timeout):  # a country without states
        page.wait_for_function(
            "() => document.querySelectorAll('#OpportunityApply select#State option').length > 1",
            timeout=STATES_TIMEOUT_MS,
        )


def _control(root: Locator, field_id: str) -> Locator:
    named = f'[id="{field_id}"]:visible, [name="{field_id}"]:visible'
    return root.locator(f'{named}, [data-automation="{field_id}"]:visible').first


def _decline_box(root: Locator, field_id: str) -> Locator:
    """The "I decline to say" box under an equal-opportunity question's select: ticked, it
    clears the select."""
    group = "xpath=ancestor::div[contains(@class,'form-group')][1]"
    return _control(root, field_id).locator(group).locator(DECLINE_BOX)


def _option(root: Locator, name: str, text: str) -> Locator:
    for radio in root.locator(f'input[type=radio][name="{name}"]').all():
        label = radio.locator("xpath=ancestor::label[1]").text_content() or ""
        if " ".join(label.split()) == text:
            return radio
    raise NotTaken(f"no option {text!r} for {name}")


def _date(root: Locator, field_id: str, value: str) -> None:
    """Types the date into the picker's month, day and year inputs."""
    year, month, day = value.split("-")
    bridge = root.locator(f'[id="{field_id}"]').locator("xpath=ancestor::react-ko-es6-bridge[1]")
    for part, typed in (("month", month), ("day", day), ("year", year)):
        segment = bridge.locator(f"[data-automation=ukg-date-input-text-{part}]").first
        segment.click()
        segment.press("Control+a")
        segment.press_sequentially(typed)
    bridge.locator("[data-automation=ukg-date-input-text-year]").first.press("Tab")


def _upload(page: Page, path: str) -> None:
    form = page.locator(f"{FORM} form[action*='/Documents/UploadFile']").first
    name = path.rsplit("/", 1)[-1]
    form.locator("input[type=file]").set_input_files(path)
    page.locator(f"{FORM} [data-automation=file-name]:visible", has_text=name).first.wait_for(
        timeout=UPLOAD_TIMEOUT_MS
    )
