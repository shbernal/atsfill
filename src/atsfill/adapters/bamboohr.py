"""BambooHR (`<employer>.bamboohr.com/careers/<job>`), as docs/adapters/bamboohr.md observes.

The job page renders its application form, behind Apply for This Job, from a public JSON
answer that lists every field with its label, requirement and options. `read_api` fetches it
over HTTP, `read_form` through the page's own browser context, and both list the fields from
it. Dropdowns are Fabric menus over a hidden `select`; a file is uploaded once attached.
"""

import json
import re
import urllib.request
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser
from atsfill.core.adapter import Blocked, Gap, Unsupported
from atsfill.core.browser import HTTP_TIMEOUT_S, Locator, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOST = re.compile(r"^[\w-]+\.bamboohr\.com$")
JOB = re.compile(r"^/careers/(\d+)(?:/|$)")
DETAIL = "https://{host}/careers/{job}/detail"
STATES = "https://{host}/ajax/get_states?country={country}"
FORM = "form:has(#firstName)"
APPLY = "button:has-text('Apply for This Job'), a:has-text('Apply for This Job')"
SUBMIT = "button:has-text('Submit Application')"
CAPTCHA = "iframe[title=reCAPTCHA]"
CHALLENGE = "iframe[src*='recaptcha/api2/bframe']"
FORM_TIMEOUT_MS = 30_000
UPLOAD_TIMEOUT_MS = 30_000
SHOWN_TIMEOUT_MS = 2_000
STATES_TIMEOUT_MS = 15_000  # for the state's list a country change refetches
SETTLE_MS = 300  # for React to render what a response brought
CAPTCHA_TIMEOUT_MS = 20_000
SUBMIT_TIMEOUT_MS = 90_000
YES_NO = ["Yes", "No"]
FILE_TYPES = [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".rtf"]
FILE_TYPES += [".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".png", ".bmp"]
EMPTY = "–Select–"
DATE_PARTS = r"yyyy|mon|mm|dd"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
OTHER = "Other"
# The countries the page has a state list for, by id; any other country's state is typed.
STATE_LISTS = {"1": "United States", "2": "Canada", "3": "Australia", "222": "United Kingdom"}
# The veteran question is a radio, then for a veteran a multi-select of the API's kinds.
VETERAN = {"Decline to Answer": "declineToAnswer", "Not a Veteran": "notVeteran"}

# Each standard field: (its kind, the `name` of its control).
STANDARD = {
    "firstName": ("text", "firstName"),
    "lastName": ("text", "lastName"),
    "email": ("text", "email"),
    "phone": ("phone", "phone"),
    "streetAddress": ("text", "streetAddress.value"),
    "city": ("text", "city.value"),
    "state": ("state", "state.value"),
    "zip": ("text", "zip.value"),
    "countryId": ("select", "countryId.value"),
    "resumeFileId": ("file", "resumeFileId"),
    "coverLetterFileId": ("file", "coverLetterFileId"),
    "dateAvailable": ("date", None),
    "desiredPay": ("text", "desiredPay"),
    "websiteUrl": ("text", "websiteUrl"),
    "linkedinUrl": ("text", "linkedinUrl"),
    "twitterUsername": ("text", "twitterUsername"),
    "referredBy": ("text", "referredBy"),
    "educationLevelId": ("select", "educationLevelId"),
    "educationInstitutionName": ("text", "educationInstitutionName"),
    "references": ("text", "references"),
    "genderId": ("select", "genderId"),
    "ethnicityId": ("select", "ethnicityId"),
    "veteranStatusId": ("veteran", "veteranStatusId"),
    "disabilityId": ("select", "disabilityId"),
}
# A custom question's kind by its API type; its control is `customQuestionAnswers.<type>_<id>`.
CUSTOM = {
    "short": "text",
    "long": "text",
    "yes_no": "radio",
    "multi": "radio",
    "checkbox": "checkbox",
    "file": "file",
}
TYPES = {
    "text": FieldType.TEXT,
    "phone": FieldType.PHONE,
    "date": FieldType.DATE,
    "select": FieldType.CHOICE,
    "veteran": FieldType.CHOICE,
    "state": FieldType.CHOICE,
    "radio": FieldType.CHOICE,
    "checkbox": FieldType.CHOICE,
    "file": FieldType.FILE,
}

# The form's JSON answer and the state lists, fetched as the page fetches them.
_FETCH = """async urls => {
  const out = [];
  for (const url of urls) {
    const response = await fetch(url);
    out.push(response.ok ? {json: await response.json()} : {status: response.status});
  }
  return out;
}"""

# What each present control shows, by the field's kind.
_READ_BACK = """(form, specs) => {
  const named = name => form.querySelector(`[name="${name}"]`);
  const shown = select => {
    const text = select?.closest("[data-fabric-component=Select]")
      ?.querySelector("button[data-menu-id]")?.textContent.trim();
    return text && text !== "–Select–" ? text : null;
  };
  const out = {};
  for (const [id, [kind, name, extra]] of Object.entries(specs)) {
    const el = name ? named(name) : null;
    if (kind === "radio") {
      const checked = form.querySelector(`input[name="${name}"]:checked`);
      out[id] = checked ? checked.closest("label").textContent.trim() : null;
    } else if (kind === "veteran") {
      const checked = form.querySelector("#declineToAnswer:checked, #notVeteran:checked");
      const kinds = el ? [...el.selectedOptions].map(o => extra[o.value]).filter(Boolean) : [];
      out[id] = checked ? checked.closest("label").textContent.trim() : kinds[0] ?? null;
    } else if (kind === "checkbox") out[id] = el ? (el.checked ? "Yes" : "No") : null;
    else if (kind === "select") out[id] = shown(el);
    else if (kind === "state") out[id] = el?.tagName === "SELECT" ? shown(el) : el?.value;
    else if (kind === "file") {
      const list = el?.parentElement
        .querySelector("[data-fabric-component=FileUploadList]");
      out[id] = el?.value && list ? list.textContent.trim() : null;
    } else if (kind === "date") {
      const input = form.querySelector("input[placeholder*=yyyy]");
      out[id] = input ? [input.value, input.placeholder] : null;
    } else out[id] = el?.value;
  }
  return out;
}"""


class BambooHR:
    name = "bamboohr"
    known_gaps: dict[str, Gap] = {}
    side_effects = [
        SideEffect("upload", "hiring_system", "an attached file is stored by BambooHR at once"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        # each field's (kind, control name, extra): a radio's option ids by text, the
        # veteran kinds' texts by id, a date's nothing
        self._specs: dict[str, tuple[str, str | None, Any]] = {}

    def detect(self, url: str, page: Page | None) -> bool:
        return _job(url) is not None

    def posting(self, url: str) -> str:
        job = _job(url)
        return f"https://{job[0]}/careers/{job[1]}" if job else url

    def read_api(self, url: str) -> list[Field] | None:
        if (job := _job(url)) is None:
            return None
        try:
            detail = _get(DETAIL.format(host=job[0], job=job[1]))
            states = {c: _get(STATES.format(host=job[0], country=c)) for c in STATE_LISTS}
            return [f for f, _ in _form(detail, states, None)]
        except OSError, ValueError, KeyError, TypeError, Unsupported:
            return None

    def read_form(self, page: Page) -> list[Field]:
        host, job = _job(page.url) or ("", "")
        form = page.locator(FORM)
        try:
            page.locator(f"{APPLY}, {FORM}").first.wait_for(timeout=FORM_TIMEOUT_MS)
            if not form.count():
                browser.click(page.locator(APPLY).first)
            form.locator("#firstName").wait_for(timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        urls = [DETAIL.format(host=host, job=job)]
        urls += [STATES.format(host=host, country=c) for c in STATE_LISTS]
        detail, *lists = page.evaluate(_FETCH, urls)
        if "status" in detail:
            raise Unsupported(f"posting_gone: the form's detail answered {detail['status']}")
        states = {c: s.get("json", {}) for c, s in zip(STATE_LISTS, lists, strict=True)}
        country = form.locator("select[name='countryId.value']")
        default = (_shown(country) or "") if country.count() else None
        fields = _form(detail["json"], states, default)
        self._specs = {f.id: spec for f, spec in fields}
        for field, (kind, name, _) in fields:
            if field.id.endswith("_other"):
                continue  # shown once its question is answered Other
            present = (
                form.locator("input[placeholder*=yyyy]")
                if kind == "date"
                else form.locator("#veteran")
                if kind == "veteran"
                else form.locator(f"[name='{name}']")
            )
            if not present.count():
                raise Unsupported(f"unsupported_flow: {field.id} is not on the page")
        return [f for f, _ in fields]

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        form = page.locator(FORM)
        # the country before the state, whose list follows it
        order = sorted(self._specs, key=lambda i: i != "countryId")
        for field_id in order:
            if field_id not in answers:
                continue
            kind, name, extra = self._specs[field_id]
            answer = answers[field_id]
            value = answer if isinstance(answer, str) else answer[0]
            control = form.locator(f"[name='{name}']")
            match kind:
                case "file":
                    _attach(form, str(name), answer)
                case "select" if field_id == "countryId":
                    if _shown(control) == value:
                        continue  # the state's list stays
                    with (
                        suppress(Timeout),
                        page.expect_response(
                            lambda r: "/ajax/get_states" in r.url, timeout=STATES_TIMEOUT_MS
                        ),
                    ):  # the state's list follows the country
                        _pick(page, control, value)
                    page.wait_for_timeout(SETTLE_MS)
                case "select":
                    _pick(page, control, value)
                case "state":
                    if control.evaluate("e => e.tagName") == "SELECT":
                        _pick(page, control, value)
                    else:
                        control.fill(value)
                case "veteran":
                    if value in VETERAN:
                        browser.click(_label(form, f"#{VETERAN[value]}"))
                    else:
                        browser.click(_label(form, "#veteran"))
                        control.wait_for(state="attached", timeout=SHOWN_TIMEOUT_MS)
                        _pick(page, control, value)
                case "radio":
                    option = (extra or {}).get(value, value)
                    browser.click(_label(form, f"input[name='{name}'][value='{option}']"))
                case "checkbox":
                    if control.is_checked() != (value == "Yes"):
                        browser.click(_label(form, f"[name='{name}']"))
                case "date":
                    date = form.locator("input[placeholder*=yyyy]")
                    date.fill(_to_page(value, date.get_attribute("placeholder") or ""))
                case _:
                    if field_id.endswith("_other"):
                        try:
                            control.wait_for(state="attached", timeout=SHOWN_TIMEOUT_MS)
                        except Timeout:
                            continue  # its question was not answered Other
                    control.fill(value)

    def read_back(self, page: Page) -> dict[str, Answer]:
        specs = {
            i: (kind, name, extra if kind == "veteran" else None)
            for i, (kind, name, extra) in self._specs.items()
        }
        values = page.locator(FORM).evaluate(_READ_BACK, specs)
        if isinstance(date := values.get("dateAvailable"), list):
            values["dateAvailable"] = _from_page(*date)
        return {k: v for k, v in values.items() if v not in (None, "")}

    def submit(self, page: Page) -> str | None:
        """Ticks the reCAPTCHA box, then clicks submit; the evidence is the answer to the
        form's POST. An image challenge instead of the tick is `Blocked`."""
        host = urlsplit(page.url).hostname
        captcha = page.locator(CAPTCHA)
        if captcha.count():
            captcha.content_frame.locator("#recaptcha-anchor").click()
            token = page.locator("#g-recaptcha-response")
            try:
                page.wait_for_function(
                    "el => el.value !== ''", arg=token.element_handle(), timeout=CAPTCHA_TIMEOUT_MS
                )
            except Timeout:
                if page.locator(CHALLENGE).is_visible():
                    raise Blocked("captcha: reCAPTCHA image challenge") from None
                raise
        with page.expect_response(
            lambda r: (
                r.request.method == "POST"
                and urlsplit(r.url).hostname == host
                and "/ajax/files/" not in r.url
            ),
            timeout=SUBMIT_TIMEOUT_MS,
        ) as applied:
            browser.click(page.locator(SUBMIT))
        response = applied.value
        if not response.ok:
            raise RuntimeError(f"apply answered {response.status}: {response.text()[:200]}")
        return f"{response.url} {response.status}"


def _job(url: str) -> tuple[str, str] | None:
    parts = urlsplit(url)
    if not (parts.hostname and HOST.match(parts.hostname)) or not (m := JOB.match(parts.path)):
        return None
    return parts.hostname, m[1]


def _get(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "atsfill"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return json.load(response)


def _shown(select: Locator) -> str | None:
    """The text a Fabric select's button shows, None while nothing is picked."""
    button = select.locator("xpath=..").locator("button[data-menu-id]")
    text = (button.text_content() or "").strip()
    return text if text and text != EMPTY else None


def _pick(page: Page, select: Locator, text: str) -> None:
    """Picks a Fabric select's option by its text from the menu its button opens."""
    button = select.locator("xpath=..").locator("button[data-menu-id]")
    browser.click(button)
    menu = page.locator(f"[id='{button.get_attribute('data-menu-id')}']")
    item = menu.locator("[role=menuitem], [role=option]").filter(has_text=_exact(text))
    multiple = select.evaluate("e => e.multiple")
    browser.click(item.first)
    if multiple:  # stays open for more picks
        page.keyboard.press("Escape")


def _attach(form: Locator, name: str, answer: Answer) -> None:
    """Attaches through the file input beside the hidden id, then waits for the upload."""
    hidden = f"[name='{name}']"
    form.locator(f"*:has(> {hidden}) input[type=file]").set_input_files(answer)
    form.page.wait_for_function(
        "el => el.value !== ''",
        arg=form.locator(hidden).element_handle(),
        timeout=UPLOAD_TIMEOUT_MS,
    )


def _label(form: Locator, control: str) -> Locator:
    return form.locator(f"label:has({control})")


def _exact(value: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(value)}\s*$")


def _to_page(iso: str, placeholder: str) -> str:
    """A YYYY-MM-DD date in the page's format, its placeholder (`dd mon yyyy`, `mm/dd/yyyy`)."""
    year, month, day = iso.split("-")
    parts = {"yyyy": year, "mon": MONTHS[int(month) - 1], "mm": month, "dd": day}
    return re.sub(DATE_PARTS, lambda m: parts[m[0]], placeholder)


def _from_page(value: str, placeholder: str) -> str:
    """The page's date as YYYY-MM-DD, by the placeholder's order of its parts."""
    order, words = re.findall(DATE_PARTS, placeholder), re.findall(r"\w+", value)
    parts = dict(zip(order, words, strict=False))
    if parts.get("mon") in MONTHS:
        parts["mm"] = f"{MONTHS.index(parts['mon']) + 1:02}"
    if not {"yyyy", "mm", "dd"} <= parts.keys():
        return value
    return f"{parts['yyyy']}-{parts['mm']}-{parts['dd']}"


def _form(
    detail: dict[str, Any], states: dict[str, Any], default: str | None
) -> list[tuple[Field, tuple[str, str | None, Any]]]:
    """Each field of the form's JSON answer with how to find and fill it; `default` is the
    country the page shows before any answer (empty for none), None to take the JSON's."""
    result = detail["result"]
    country = (result["formFields"].get("countryId") or {}).get("value") or ""
    if default is not None:
        by_name = {name: c for c, name in STATE_LISTS.items()}
        country = by_name.get(default, default)
    if result["jobOpening"].get("jobOpeningStatus") not in (None, "Open"):
        raise Unsupported("posting_gone: the job opening is not open")
    out = []
    for key, entry in result["formFields"].items():
        if key == "customQuestions":
            for question in entry:
                out += _custom(question)
            continue
        if not entry:  # a demographic question the employer does not ask
            continue
        if key not in STANDARD:
            raise Unsupported(f"unsupported_rendering: {key} is a field it does not know")
        kind, name = STANDARD[key]
        label, required = entry["label"].strip(), entry["isRequired"]
        options = [o["text"] for o in entry.get("options") or []] or None
        extra: Any = None
        prefilled = key == "countryId" and bool(country)
        if key == "state" and options:
            options = _states(states, country)
        if key == "state" and not options:
            kind = "text"
        if kind == "veteran":
            extra = {o["id"]: o["text"] for o in entry["options"] if int(o["id"]) > 0}
            options = list(VETERAN) + list(extra.values())
        accept = FILE_TYPES if kind == "file" else None
        field = Field(key, label, TYPES[kind], required, options, None, accept, prefilled)
        out.append((field, (kind, name, extra)))
    return out


def _states(states: dict[str, Any], default: str | None) -> list[str]:
    """The state lists of every country that has one, the default country's (an id) first:
    the list the page shows follows the country answered."""
    order = sorted(STATE_LISTS, key=lambda c: c != default)
    names = [s["full_name"] for c in order for s in (states.get(c) or {}).get("data") or []]
    return list(dict.fromkeys(names))


def _custom(question: dict[str, Any]) -> list[tuple[Field, tuple[str, str | None, Any]]]:
    qid, kind_api = question["id"], question["type"]
    if kind_api not in CUSTOM:
        raise Unsupported(f"unsupported_rendering: question {qid} is a {kind_api}")
    kind = CUSTOM[kind_api]
    name = f"customQuestionAnswers.{kind_api}_{qid}"
    label, required = question["question"].strip(), question["isRequired"]
    options, extra = None, None
    if kind_api == "yes_no" or kind == "checkbox":
        options = YES_NO
    elif kind_api == "multi":
        extra = {o["option"].strip(): str(o["id"]) for o in question["options"]}
        if question.get("hasOther") == "yes":
            extra[OTHER] = "other"
        options = list(extra)
    accept = FILE_TYPES if kind == "file" else None
    field_id = f"question_{qid}"
    field = Field(field_id, label, TYPES[kind], required, options, None, accept)
    out = [(field, (kind, name, extra))]
    if extra and OTHER in extra and question.get("hasOther") == "yes":
        other = Field(
            f"{field_id}_other",
            f"{label}: please specify (asked when {label!r} is {OTHER})",
            FieldType.TEXT,
            False,
        )
        out.append((other, ("text", f"{name}_other", None)))
    return out
