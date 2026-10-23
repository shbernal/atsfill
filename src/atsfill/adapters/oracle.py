"""Oracle Recruiting Cloud's Candidate Experience (`*.oraclecloud.com/hcmUI/CandidateExperience`),
as docs/adapters/oracle.md observes.

The form opens only past an email step, and an email the employer's Oracle already knows gets an
emailed OTP instead. Reading passes the step with a throwaway address; filling starts over with
the answer's. Oracle keeps a draft of the application from then on, autosaved as fields change.
"""

import re
import uuid
from contextlib import suppress
from dataclasses import replace
from datetime import date
from pathlib import PurePath
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser, matching
from atsfill.core.adapter import Blocked, Gap, NotTaken, Unsupported, known_gap
from atsfill.core.browser import Locator, Page
from atsfill.core.results import Answer, Field, FieldType, SideEffect

JOB = re.compile(r"/hcmUI/CandidateExperience/[\w-]+/sites/[^/]+/job/(\d+)")
EMAIL = "email"
LEGAL = "legalDisclaimer"
COUNTRY = "country"
EMAIL_INPUT = "input[type=email]"
LEGAL_BOX = "legal-disclaimer .apply-flow-input-checkbox__button"
NEXT = "button[type=submit].apply-flow-pagination__button"
SUBMIT = ".apply-flow-pagination__submit-button"
PAGINATION = ".apply-flow-pagination__button"
PAGE_NUMBERS = ".apply-flow-navigation-pages__link:visible"
LOADING = ".app-loading-spinner, .cx-select--loading, .apply-flow-block .skeleton"
OTP_PAGE = ".apply-flow-pin-code-required"
CAPTCHA = "iframe[src*='hcaptcha.com']"
MANUALLY = ".quick-apply-flow__manually--link:visible"  # past an offer to import a profile
ATTACHED = ".attachment-upload-button__download, .misc-attachment-list__item-name"
STEP_TIMEOUT_MS = 30_000
LIST_TIMEOUT_MS = 3_000
SEARCH_TIMEOUT_MS = 6_000  # for a typed search's results
PAGE_TIMEOUT_MS = 15_000  # per click on a page number
SUBMIT_TIMEOUT_MS = 90_000
KEY_DELAY_MS = 20
SETTLE_MS = 1_500
UPLOAD_TIMEOUT_MS = 30_000
CHECKBOX = ["Yes", "No"]
KINDS = {
    "text": FieldType.TEXT,
    "phone": FieldType.PHONE,
    "date": FieldType.DATE,
    "pills": FieldType.CHOICE,
    "radios": FieldType.CHOICE,
    "select": FieldType.CHOICE,
    "check": FieldType.CHOICE,
    "multipills": FieldType.MULTI_CHOICE,
    "checks": FieldType.MULTI_CHOICE,
    "file": FieldType.FILE,
}

# Every field of the form, by block. A row's id is its label's DOM id without the counter Oracle
# appends on each load (`labelText-lastName-14` is `lastName`); a block of tiles to add
# (education, experience, skills) is one `tiles` entry.
_READ = """() => {
  const text = e => (e?.textContent || "").replace(/\\s+/g, " ").trim().replace(/\\s*\\*$/, "");
  const stable = s => s?.replace(/^labelText-/, "").replace(/-\\d+(-label)?$/, "");
  const shown = e => !!e.getClientRects().length;
  const CONTROL = "input:not([type=hidden]), textarea, select, button, [role], [tabindex]";
  const fields = [];
  const add = f => { if (f.id && !fields.some(g => g.id === f.id)) fields.push(f); };
  for (const block of document.querySelectorAll("apply-flow-block")) {
    const kind = [...(block.firstElementChild?.classList || [])]
      .find(c => c.startsWith("apply-flow-block--"))?.slice(18);
    const title = block.querySelector(".apply-flow-block__title");
    const blockRequired = !!title?.classList.contains("apply-flow-block__title--required");
    const blockId = `block:${block.dataset.scrollSpyId}`;
    if (!shown(block) || kind === "profile-import") continue;
    if (kind === "agreements") {
      for (const box of block.querySelectorAll("input[type=checkbox]")) {
        const label = box.closest("label") || block.querySelector(`label[for="${box.id}"]`);
        const id = (box.id || label?.id || "").replace(/-checkbox(-label)?$/, "");
        add({id, kind: "check", label: text(label), required: false});
      }
      continue;
    }
    const files = [...block.querySelectorAll("input[type=file]")];
    for (const input of files) {
      const button = input.closest("resume-upload-button, cover-letter-upload-button");
      const id = button?.tagName === "RESUME-UPLOAD-BUTTON" ? "resume"
        : button ? "coverLetter" : blockId;
      const required = button
        ? !!button.querySelector(".attachment-upload-button__drag-and-drop-label--required")
        : blockRequired;
      const filled = button?.querySelector(".attachment-upload-button__download");
      add({id, kind: "file", label: button ? text(button.querySelector(
             ".attachment-upload-button__label")) : text(title), required,
           accept: input.accept ? input.accept.split(",").map(s => s.trim()) : null,
           value: filled ? text(filled) : null});
    }
    const rows = [...block.querySelectorAll(".input-row")]
      .filter(r => !r.querySelector(".input-row") && shown(r));
    if (!rows.length && !files.length && block.querySelector(
        ".apply-flow-profile-item-tile__new-tile, .apply-flow-profile-item-button--add")) {
      add({id: blockId, kind: "tiles", label: text(title), required: blockRequired});
      continue;
    }
    for (const row of rows) {
      const label = row.querySelector(".input-row__label");
      const named = row.querySelector("[id^=labelText-]");
      const input = row.querySelector("input:not([type=hidden]), textarea");
      let id = stable(named?.id || label?.getAttribute("for") || label?.id || input?.name);
      const field = {id, label: text(row.querySelector(
          ".input-row__linebreak, .input-row__label-text") || label),
        required: !!label?.classList.contains("input-row__label--required")
          || !!row.querySelector("[aria-required=true], [required]")};
      const pills = sel => [...row.querySelectorAll(sel)].map(text);
      if (row.querySelector(".phone-row")) {
        field.kind = "phone";
        field.value = row.querySelector("input[type=tel]").value;
      } else if (row.matches(".datepicker-row")) {
        field.kind = "date";
      } else if (row.querySelector("ul[role=radiogroup]")) {
        field.kind = "pills";
        field.options = pills(".cx-select-pill-name");
      } else if (row.querySelector(".cx-multi-select-pills")) {
        field.kind = "multipills";
        field.options = pills(".cx-select-pill-name");
      } else if (row.querySelector("input[role=combobox]")) {
        field.kind = "select";
        field.value = row.querySelector("input[role=combobox]").value;
        field.geo = row.matches(".geo-hierarchy-form-element");
        field.search = !!row.closest(".address-form") && !field.geo;
      } else if (row.querySelector("input.apply-flow-input-radio-control")) {
        field.kind = "radios";
        field.options = pills(".apply-flow-input-radio__label");
      } else if (row.querySelector(".input-row__control-item input[type=checkbox]")) {
        field.kind = "checks";
        field.options = pills(".apply-flow-input-checkbox__label");
      } else if (input?.matches("textarea, input.input-row__control") && !input.disabled) {
        field.kind = input.type === "email" ? "email" : "text";
        field.value = input.value;
        field.maxLength = input.maxLength > 0 ? input.maxLength : null;
      } else if (input?.disabled || !row.querySelector(CONTROL)) {
        continue;  // text, or a value the page sets
      } else {
        field.kind = "unhandled";
        field.id ||= `${blockId}:${field.label}`;
      }
      add(field);
    }
  }
  return fields;
}"""

# Whether the form is loaded; a dropdown loads only once scrolled into view, so the first one
# still loading is brought there.
_SETTLED = """(loading) => {
  const pending = [...document.querySelectorAll(loading)]
    .find(e => e.getClientRects().length && getComputedStyle(e).visibility !== "hidden");
  pending?.scrollIntoView({block: "center"});
  return !pending
  && [...document.querySelectorAll(".apply-flow-block--personal-information-address-v2")]
    .every(block => {
    const country = block.querySelector("address-form-v2 input[role=combobox]");
    return country && country.value && block.querySelector(".address-form .input-row");
  });
}"""

# The selector of the element naming a field's row, found by the read's id rule.
_ROW = """(id) => {
  const stable = s => s?.replace(/^labelText-/, "").replace(/-\\d+(-label)?$/, "");
  for (const row of document.querySelectorAll(".input-row")) {
    if (row.querySelector(".input-row")) continue;
    const label = row.querySelector(".input-row__label");
    const named = row.querySelector("[id^=labelText-]");
    const input = row.querySelector("input:not([type=hidden]), textarea");
    const rowId = stable(named?.id || label?.getAttribute("for") || label?.id || input?.name);
    if (rowId !== id) continue;
    if (named) return `[id='${named.id}']`;
    if (label?.getAttribute("for")) return `label[for='${label.getAttribute("for")}']`;
    if (label?.id) return `[id='${label.id}']`;
    return `[name='${input.name}']`;
  }
  return null;
}"""

# What the form holds for each field id, in the same terms the read gives its options.
_READ_BACK = """(ids) => {
  const text = e => (e?.textContent || "").replace(/\\s+/g, " ").trim();
  const stable = s => s?.replace(/^labelText-/, "").replace(/-\\d+(-label)?$/, "");
  const out = {};
  for (const row of document.querySelectorAll(".input-row")) {
    if (row.querySelector(".input-row")) continue;
    const label = row.querySelector(".input-row__label");
    const named = row.querySelector("[id^=labelText-]");
    const input = row.querySelector("input:not([type=hidden]), textarea");
    const id = stable(named?.id || label?.getAttribute("for") || label?.id || input?.name);
    const kind = ids[id];
    if (id in out) continue;  // a field shown twice is read where it is filled, the first
    const values = sel => [...row.querySelectorAll(sel)].map(text);
    if (kind === "phone") {
      out[id] = row.querySelector("input[role=combobox]").value
        + row.querySelector("input[type=tel]").value;
    } else if (kind === "date") {
      out[id] = [...row.querySelectorAll("input[role=combobox]")].map(i => i.value);
    } else if (kind === "pills") {
      out[id] = values(".cx-select-pill-section--selected .cx-select-pill-name")[0] ?? null;
    } else if (kind === "multipills") {
      out[id] = values(".cx-select-pill-section--selected .cx-select-pill-name");
    } else if (kind === "select" || kind === "typeahead") {
      out[id] = row.querySelector("input[role=combobox]").value;
    } else if (kind === "radios" || kind === "checks") {
      const picked = [...row.querySelectorAll("input:checked")]
        .map(i => text(row.querySelector(`label[for="${i.id}"]`)));
      out[id] = kind === "radios" ? picked[0] ?? null : picked;
    } else if (kind === "text" || kind === "email") {
      out[id] = input.value;
    }
  }
  for (const box of document.querySelectorAll("apply-flow-block input[type=checkbox]")) {
    const label = box.closest("label") || document.querySelector(`label[for="${box.id}"]`);
    const id = (box.id || label?.id || "").replace(/-checkbox(-label)?$/, "");
    if (ids[id] === "check") out[id] = box.checked ? "Yes" : "No";
  }
  for (const [id, kind] of Object.entries(ids)) {
    if (kind !== "file") continue;
    const host = id === "resume" ? document.querySelector("resume-upload-button")
      : id === "coverLetter" ? document.querySelector("cover-letter-upload-button")
      : document.querySelector(`apply-flow-block[data-scroll-spy-id="${id.slice(6)}"]`);
    const attached = ".attachment-upload-button__download, .misc-attachment-list__item-name";
    out[id] = text(host?.querySelector(attached)) || null;
  }
  return out;
}"""


class Oracle:
    name = "oracle"
    known_gaps = {"entries": Gap.RENDERING}
    filters = ["##.cookie-consent-modal"]  # some tenants' banner, over the email step and the form
    side_effects = [
        SideEffect(
            "load",
            "hiring_system",
            "the email step starts a draft application in the employer's Oracle, under a"
            " throwaway address to read the form and under the answer's to fill it",
        ),
        SideEffect("fill", "hiring_system", "Oracle autosaves the draft, files included"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._fields: dict[str, Field] = {}
        self._kinds: dict[str, str] = {}
        self._pages: dict[str, int] = {}  # field id: the page of the form it is on
        self._picked: dict[str, str] = {}  # a geography field's id: the item picked in it
        self._months: list[str] = []
        self._job = ""

    def detect(self, url: str, page: Page | None) -> bool:
        return JOB.search(urlsplit(url).path) is not None

    def posting(self, url: str) -> str:
        parts = urlsplit(url)
        match = JOB.search(parts.path)
        assert match, url
        return f"https://{parts.hostname}/hcmUI/CandidateExperience/job/{match[1]}"

    def read_form(self, page: Page) -> list[Field]:
        parts = urlsplit(page.url)
        match = JOB.search(parts.path)
        assert match, page.url  # detected on a job URL
        self._job = f"{parts.scheme}://{parts.netloc}{parts.path[: match.end()]}"
        legal = self._email_step(page)
        fields = [Field(EMAIL, "Email Address", FieldType.TEXT, True)]
        if legal:
            fields.append(Field(LEGAL, legal, FieldType.CHOICE, True, CHECKBOX))
        self._pass_email_step(
            page, f"atsfill.read.{uuid.uuid4().hex[:12]}@example.com", bool(legal)
        )
        self._kinds, self._pages = {}, {}
        # The page numbers move between pages without checking the page left.
        for number in range(1, max(len(_page_numbers(page)), 1) + 1):
            _go_to(page, number)
            described = [
                (raw, field)
                for raw in page.evaluate(_READ)
                if raw["id"] not in self._kinds and (field := self._described(page, raw))
            ]
            for raw, field in _geography(described):
                self._kinds[field.id], self._pages[field.id] = raw["kind"], number
                fields.append(field)
        self._fields = {f.id: f for f in fields}
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        if LEGAL in self._fields and answers.get(LEGAL) != "Yes":
            raise Unsupported("not_qualified: the form opens only once the terms are accepted")
        page.context.clear_cookies()
        page.goto(self._job + "/apply/email")
        page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
        self._email_step(page)
        email = answers.get(EMAIL)
        assert isinstance(email, str)  # required, so answered
        self._pass_email_step(page, email, LEGAL in self._fields)
        for number in sorted(set(self._pages.values())):
            on_page = [i for i in answers if self._pages.get(i) == number]
            if not on_page:
                continue
            _go_to(page, number)
            if COUNTRY in on_page:  # the address below it follows the country's format
                self._fill(page, COUNTRY, answers[COUNTRY])
                _settle(page)
            # Files go last: a click while one attaches can be lost, and once attached its
            # label takes the focus back from a field typed after it.
            files = [i for i in on_page if self._kinds[i] == "file"]
            for field_id in [i for i in on_page if i not in (COUNTRY, *files)] + files:
                self._fill(page, field_id, answers[field_id])
            self._wait_for_uploads(
                page, {i: PurePath(a).name for i in files if isinstance(a := answers[i], str)}
            )

    def read_back(self, page: Page) -> dict[str, Answer]:
        values: dict[str, Any] = page.evaluate(_READ_BACK, self._kinds)
        values[LEGAL] = "Yes"  # the form shows only once the terms were accepted
        for field_id, kind in self._kinds.items():
            value = values.get(field_id)
            if kind == "date" and isinstance(value, list):
                values[field_id] = self._date_value(value)
            picked = self._picked.get(field_id)
            if picked and value and picked != value and picked.split(",")[0].strip() == value:
                values[field_id] = picked  # an item that shows only its first part once picked
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        _go_to(page, len(_page_numbers(page)) or 1)
        _click(page, page.locator(SUBMIT))
        page.wait_for_url(lambda url: "/apply/" not in url, timeout=SUBMIT_TIMEOUT_MS)
        return page.url

    def _email_step(self, page: Page) -> str | None:
        """Opens the email step; the terms' label when it asks for them to be accepted."""
        page.goto(self._job + "/apply/email")
        try:
            page.locator(EMAIL_INPUT).wait_for(timeout=STEP_TIMEOUT_MS)
        except browser.Timeout:
            raise Unsupported("posting_gone: no email step on the apply page") from None
        _settle(page)
        terms = page.locator("legal-disclaimer .apply-flow-input-checkbox__label")
        return _text(terms.first.inner_text()) if terms.count() else None

    def _pass_email_step(self, page: Page, email: str, agree: bool) -> None:
        page.locator(EMAIL_INPUT).fill(email)
        if agree:
            box = page.locator(LEGAL_BOX).first
            if "--checked" not in (box.get_attribute("class") or ""):
                _click(page, box)
        _click(page, page.locator(NEXT).first)
        opened = page.locator(f"apply-flow-block, {OTP_PAGE}, {MANUALLY}")
        try:
            opened.first.wait_for(timeout=STEP_TIMEOUT_MS)
            if page.locator(MANUALLY).count():
                _click(page, page.locator(MANUALLY).first)
                page.locator("apply-flow-block").first.wait_for(timeout=STEP_TIMEOUT_MS)
        except browser.Timeout:
            if page.locator(CAPTCHA).count():
                raise Blocked("captcha: the email step asks for an hCaptcha") from None
            raise RuntimeError("the email step did not open the form") from None
        if page.locator(OTP_PAGE).count():
            raise Blocked(
                f"email_otp: {email} is known to this employer's Oracle, which emails it an"
                " OTP before it shows the form"
            )
        page.locator(f"{PAGINATION}:visible").first.wait_for(timeout=STEP_TIMEOUT_MS)
        _settle(page)

    def _described(self, page: Page, raw: dict[str, Any]) -> Field | None:
        """The field a raw description is, None for one atsfill leaves out."""
        if raw["kind"] == "tiles":
            gap = known_gap(self, "entries", f"{raw['label']!r} needs entries added")
            return Field(raw["id"], raw["label"], FieldType.TEXT, raw["required"], unfillable=gap)
        if raw["kind"] == "unhandled":
            unhandled = f"unhandled_rendering: {raw['label']!r} has no control atsfill knows"
            return Field(
                raw["id"], raw["label"], FieldType.TEXT, raw["required"], unfillable=unhandled
            )
        if raw["kind"] == "email":
            self._kinds[EMAIL] = "email"
            return None
        if raw["kind"] == "select":
            options = self._options(page, raw["id"])
            raw["kind"] = "select" if options else "text" if raw["search"] else "typeahead"
            raw["options"] = options or None
        if raw["kind"] == "date":
            self._months = self._date_options(page, raw["id"])
        return _field(raw)

    def _row(self, page: Page, field_id: str) -> Locator:
        """The innermost row holding the field."""
        selector = page.evaluate(_ROW, field_id)
        if selector is None:
            raise RuntimeError(f"{field_id} left the form while it was filled")
        return page.locator(".input-row").filter(has=page.locator(selector)).last

    def _options(self, page: Page, field_id: str) -> list[str]:
        """A dropdown's options, from its list once opened; none when it lists only matches
        for what is typed."""
        box = self._row(page, field_id).locator("input[role=combobox]")
        return _open_list(page, box)

    def _date_options(self, page: Page, field_id: str) -> list[str]:
        return _open_list(page, self._row(page, field_id).locator("input[role=combobox]").first)

    def _date_value(self, parts: list[str]) -> str | None:
        month, day, year = parts
        if not (month in self._months and day.isdigit() and year.isdigit()):
            return None
        return date(int(year), self._months.index(month) + 1, int(day)).isoformat()

    def _fill(self, page: Page, field_id: str, answer: Answer) -> None:
        field, kind = self._fields[field_id], self._kinds[field_id]
        if kind == "check":
            box = page.locator(f"[id='{field_id}-checkbox'], [id='{field_id}-checkbox-label']")
            host = box.first.locator("xpath=ancestor-or-self::*[.//input][1]")
            checked = host.locator("input[type=checkbox]").first.is_checked()
            if checked != (answer == "Yes"):
                _click(page, host.locator(".apply-flow-input-checkbox__button").first)
            return
        if kind == "file":
            assert isinstance(answer, str)
            self._file_input(page, field_id).set_input_files(answer)
            return
        row = self._row(page, field_id)
        match kind, answer:
            case "text", str():
                box = row.locator("input, textarea").first
                box.fill(answer)
                if box.get_attribute("role") == "combobox":  # an address search, left unpicked
                    box.press("Escape")
                box.blur()
            case "pills" | "multipills", _:
                for value in [answer] if isinstance(answer, str) else answer:
                    pill = row.locator(".cx-select-pill-section").filter(
                        has=page.locator(".cx-select-pill-name", has_text=_exact(value))
                    )
                    if "--selected" not in (pill.first.get_attribute("class") or ""):
                        _click(page, pill.first)
            case "radios" | "checks", _:
                for value in [answer] if isinstance(answer, str) else answer:
                    label = row.locator("label").filter(has_text=_exact(value)).first
                    target = page.locator(f"[id='{label.get_attribute('for')}']")
                    if not target.is_checked():
                        _click(page, label)
            case "select", str():
                _choose(page, row.locator("input[role=combobox]"), answer, exact=True)
                self._picked[field_id] = answer
            case "typeahead", str():
                _choose(page, row.locator("input[role=combobox]"), answer, field=field)
            case "phone", str():
                self._phone(page, row, answer)
            case "date", str():
                year, month, day = answer.split("-")
                boxes = row.locator("input[role=combobox]")
                _choose(page, boxes.nth(0), self._months[int(month) - 1], exact=True)
                _choose(page, boxes.nth(1), str(int(day)), exact=True)
                _choose(page, boxes.nth(2), year, exact=True)
            case _:
                raise Unsupported(
                    f"unhandled_rendering: cannot fill {field_id}, a {kind}, with {answer!r}"
                )

    def _phone(self, page: Page, row: Locator, answer: str) -> None:
        """Picks the country code the number starts with, the longest that fits, then types
        the rest of the number."""
        digits = re.sub(r"\D", "", answer)
        code = row.locator("input[role=combobox]")
        codes = sorted(
            {m[1] for o in _open_list(page, code) if (m := re.search(r"\+(\d+)", o))},
            key=len,
            reverse=True,
        )
        prefix = next((c for c in codes if digits.startswith(c)), None)
        if prefix is None:
            raise NotTaken(f"no country code for {answer!r}")
        _choose(page, code, f"+{prefix}", code=True)
        number = row.locator("input[type=tel]")
        number.fill(digits[len(prefix) :])
        number.blur()

    def _file_input(self, page: Page, field_id: str) -> Locator:
        return page.locator(f"{_upload(field_id)} input[type=file]").first

    def _wait_for_uploads(self, page: Page, uploads: dict[str, str]) -> None:
        """Waits for each file (by field id: its name) to show attached."""
        for field_id, name in uploads.items():
            selector = f"{_upload(field_id)} :is({ATTACHED})"
            done = page.locator(selector, has_text=name)
            try:
                done.first.wait_for(timeout=UPLOAD_TIMEOUT_MS)
            except browser.Timeout:
                return  # the read-back shows what was kept


def _click(page: Page, target: Locator) -> None:
    """Clicks, once more after what came over the target: the app's loading spinner, or a
    header fixed over the top of the page."""
    try:
        browser.click(target)
    except browser.Overlaid as e:
        if "loading" in str(e):
            _settle(page)
        else:  # a header fixed over the top of the page
            target.evaluate("e => e.scrollIntoView({block: 'center'})")
        browser.click(target)


def _geography(described: list[tuple[dict, Field]]) -> list[tuple[dict, Field]]:
    """Leaves one field of the address's geography (state, city, postal code), the most
    specific: its items name the levels above (`0200, Acton, ACT`), and picking one fills
    them. It is required when any level is."""
    levels = [(r, f) for r, f in described if r.get("geo") and f.id != COUNTRY]
    if len(levels) < 2 or not any(f.options for _, f in levels):
        return described
    parts = lambda f: max(len(o.split(",")) for o in f.options or [""])  # noqa: E731
    kept = max(levels, key=lambda rf: parts(rf[1]))
    required = any(f.required for _, f in levels)
    return [
        (r, replace(f, required=required) if (r, f) == kept else f)
        for r, f in described
        if (r, f) == kept or (r, f) not in levels
    ]


def _page_numbers(page: Page) -> list[str]:
    """The page numbers of a form over several pages, none for a single page."""
    return [_text(t) for t in page.locator(PAGE_NUMBERS).all_inner_texts() if _text(t).isdigit()]


def _go_to(page: Page, number: int) -> None:
    """Opens a page of the form by its number, clicked once more when the first click is lost
    while the page settles."""
    if not _page_numbers(page) or re.search(rf"/apply/section/{number}\b", page.url):
        return
    for _ in range(2):
        _click(page, page.locator(PAGE_NUMBERS).filter(has_text=_exact(str(number))).first)
        with suppress(browser.Timeout):
            page.wait_for_url(
                re.compile(rf"/apply/section/{number}\b"),
                timeout=PAGE_TIMEOUT_MS,
                wait_until="commit",
            )
            _settle(page)
            return
    raise RuntimeError(f"page {number} of the form did not open")


def _upload(field_id: str) -> str:
    """The element holding a file field's upload button."""
    hosts = {"resume": "resume-upload-button", "coverLetter": "cover-letter-upload-button"}
    return hosts.get(field_id) or f"apply-flow-block[data-scroll-spy-id='{field_id[6:]}']"


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", value).replace("*", "").strip()


def _exact(value: str) -> re.Pattern:
    return re.compile(rf"^\s*{re.escape(value)}\s*$")


def _settle(page: Page) -> None:
    """Waits for the dropdowns to load, and the address block for its country's format."""
    with suppress(browser.Timeout):
        page.wait_for_function(_SETTLED, arg=LOADING, timeout=STEP_TIMEOUT_MS)
    page.wait_for_timeout(SETTLE_MS)


def _open_list(page: Page, box: Locator) -> list[str]:
    """The items a dropdown lists once opened, then closed again."""
    _click(page, box)
    cells = page.locator(f"[id='{box.get_attribute('aria-controls')}'] [role=gridcell]")
    try:
        cells.first.wait_for(timeout=LIST_TIMEOUT_MS)
        options = [_text(t) for t in cells.all_inner_texts()]
    except browser.Timeout:
        options = []
    box.press("Escape")
    return options


def _choose(
    page: Page,
    box: Locator,
    answer: str,
    *,
    exact: bool = False,
    code: bool = False,
    field: Field | None = None,
) -> None:
    """Types into a dropdown, which filters its list, and picks the item that is the answer
    (or holds it as a phone code, or matches it as a suggestion). A list whose search does not match
    its items' text as typed is looked through whole."""
    for typed in (answer, ""):
        _click(page, box)
        box.fill("")
        if typed:
            box.press_sequentially(typed, delay=KEY_DELAY_MS)
        cells = page.locator(f"[id='{box.get_attribute('aria-controls')}'] [role=gridcell]")
        with suppress(browser.Timeout):
            cells.first.wait_for(timeout=SEARCH_TIMEOUT_MS)
        for i in range(cells.count()):
            item = _text(cells.nth(i).inner_text())
            if (
                (exact and item == answer)
                or (code and re.search(rf"{re.escape(answer)}(\D|$)", item))
                or (field is not None and matching.matches(field, answer, item))
            ):
                cells.nth(i).scroll_into_view_if_needed()
                _click(page, cells.nth(i))
                return
        box.press("Escape")
        if field is not None:
            break  # a search lists nothing untyped
    raise NotTaken(f"no item for {answer!r} in a dropdown")


def _field(raw: dict[str, Any]) -> Field:
    kind = FieldType.TYPEAHEAD if raw["kind"] == "typeahead" else KINDS[raw["kind"]]
    options = CHECKBOX if raw["kind"] == "check" else raw.get("options")
    return Field(
        raw["id"],
        raw["label"],
        kind,
        raw["required"],
        options,
        raw.get("maxLength"),
        raw.get("accept"),
        prefilled=bool(raw.get("value")),
    )
