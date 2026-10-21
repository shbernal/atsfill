"""Manatal (`www.careers-page.com`), as docs/adapters/manatal.md observes.

The apply page renders the fields its `application-form` endpoint lists and reads the inputs
back only when Apply is clicked, so the inputs are the read-back.
"""

import json
import re
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from atsfill.core.adapter import Blocked, Unsupported
from atsfill.core.browser import HTTP_TIMEOUT_S, Page
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOST = "www.careers-page.com"
API = f"https://{HOST}/api/v1.0/"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
JOB_ID = re.compile(r'const selectedJobId = "(\d+)"')
TERMS_REQUIRED = re.compile(r'const isTCPPRequired = "True"')
NUMBER = re.compile(r"\d+(\.\d+)?")  # what a number input with `min="0"` takes
TERMS = "terms_and_condition"
TERMS_LABEL = "I agree to the terms and conditions & privacy policy"
YES_NO = ["Yes", "No"]
GENDERS = ["Male", "Female", "Other"]
NOTICE_PERIODS = ["Immediately", *(f"{d} days" for d in range(10, 130, 10)), "120+ days"]
YEARS = ["No experience", *(f"{n}+ year" for n in range(1, 21))]
# Slugs shown as a multiple select of a list fetched apart: its path and an item's label.
LISTS: dict[str, tuple[str, Callable[[dict[str, Any]], str]]] = {
    "nationalities": ("nationalities/", lambda n: f"{n['demonym']} ({n['common_name']})"),
    "languages": ("languages/", lambda n: n["name"]),
    "industries": ("clients/{client}/industries/", lambda n: n["name"]),
}
ENTRIES = ("educations", "experiences")  # a sub-form of entries: left out when optional
SHORT = {"full_name", "email", "phone_number", "city", "address", "latest_degree",
         "latest_university", "current_company", "current_department", "current_position",
         "current_benefits", "expected_benefits", "skype", "contact"}  # fmt: skip
RESUME_TYPES = [".pdf", ".doc", ".docx", ".rtf"]
LOAD_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 120_000

# The answer each input holds, by field id, read as the page reads them on Apply.
_READ = """fields => Object.fromEntries(fields.map(([id, kind]) => {
  const el = document.getElementById('field' + id);
  const named = [...document.querySelectorAll(`input[name="${id}"]`)];
  const texts = s => [...s.selectedOptions].filter(o => o.value).map(o => o.text.trim());
  switch (kind) {
    case 'file': return [id, el && el.files.length ? el.files[0].name : null];
    case 'boolean': return [id, el.checked ? 'Yes' : 'No'];
    case 'radio': return [id, (named.find(i => i.checked) || {}).value || null];
    case 'checkbox': return [id, named.filter(i => i.checked).map(i => i.value)];
    case 'select': return [id, texts(el)[0] || null];
    case 'multiple': return [id, texts(el)];
    case 'terms': return [id, document.querySelector(`input[name="${id}"]`).checked ? 'Yes' : 'No'];
    default: return [id, el ? el.value || null : null];
  }
}))"""
# Selects the options with these labels on a select2 multiple select, which select2 follows.
_SELECT = """(el, labels) => {
  for (const o of el.options) o.selected = labels.includes(o.text.trim());
  el.dispatchEvent(new Event('change', {bubbles: true}));
}"""
# Clicks a radio or a box when its state differs: the styled mark over it can sit outside the
# viewport, out of reach of a pointer click.
_CHECK = """(el, checked) => { if (el.checked !== checked) el.click(); }"""
# Types a date into flatpickr's read-only input, in the `Y-m-d` format it is set up with.
_DATE = """(el, value) => {
  el.value = value;
  el.dispatchEvent(new Event('change', {bubbles: true}));
}"""


def _posting(url: str) -> str:
    """`https://www.careers-page.com/<client>/job/<code>`, without `/apply` or a query."""
    client, _, code = urlsplit(url).path.strip("/").split("/")[:3]
    return f"https://{HOST}/{client}/job/{code}"


def _kind(entry: dict[str, Any]) -> str:
    """How the page renders the field: the input `_READ` and `fill` work on."""
    slug, ftype = entry["client_field_slug"], entry["client_field_field_type"]
    if entry["field_type"] in ("resume", "attachment"):
        return "file"
    if entry["field_type"] in ENTRIES:
        return "entries"
    if entry["field_type"] == "candidate_field":
        if slug in ("gender", "current_notice_period", "years_of_experience"):
            return "select"
        if slug in LISTS or ftype == "multiple_select_dropdown":
            return "multiple"
        if ftype == "dropdown":
            return "select"
        if ftype == "multiple_choice":
            return "radio"
        if ftype == "checkbox":
            return "checkbox"
    match entry["response_type"]:
        case "boolean":
            return "boolean"
        case "datetime":
            return "date"
        case "integer":
            return "number"
    return "text"


def _field(entry: dict[str, Any], options: list[str] | None) -> Field | None:
    """The field an entry of the endpoint lists, None for optional entries."""
    kind, slug = _kind(entry), entry["client_field_slug"]
    fid, label, required = str(entry["id"]), entry["label"], bool(entry["is_required"])
    match kind:
        case "entries" if required:
            raise Unsupported(f"unsupported_rendering: {entry['field_type']} entries are required")
        case "entries":
            return None
        case "file":
            accept = RESUME_TYPES if entry["field_type"] == "resume" else None
            return Field(fid, label, FieldType.FILE, required, accept=accept)
        case "boolean":
            return Field(fid, label, FieldType.CHOICE, required, YES_NO)
        case "date":
            return Field(fid, label, FieldType.DATE, required)
        case "select" | "radio":
            return Field(fid, label, FieldType.CHOICE, required, options)
        case "multiple" | "checkbox":
            return Field(fid, label, FieldType.MULTI_CHOICE, required, options)
        case "number":
            return Field(fid, f"{label} (a number)", FieldType.TEXT, required)
    if slug == "phone_number":
        return Field(fid, label, FieldType.PHONE, required)
    short = 255 if slug in SHORT or entry["field_type"] == "other" else 1000
    return Field(fid, label, FieldType.TEXT, required, max_length=short)


def _get(path: str) -> Any:
    request = urllib.request.Request(API + path, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return json.load(response)


def _options(entry: dict[str, Any], client: str) -> list[str] | None:
    """The options the endpoint's entry names, the page's fixed lists or a list fetched apart."""
    match entry["client_field_slug"]:
        case "gender":
            return GENDERS
        case "current_notice_period":
            return NOTICE_PERIODS
        case "years_of_experience":
            return YEARS
        case slug if slug in LISTS:
            path, label = LISTS[slug]
            return [label(n) for n in _get(path.format(client=client))]
    return entry["client_field_answer_choices"] or None


class Manatal:
    name = "manatal"
    side_effects = [
        SideEffect("submit", "hiring_system", "the resume and attachments are uploaded"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []

    def detect(self, url: str, page: Page | None) -> bool:
        parts = urlsplit(url).path.strip("/").split("/")
        return urlsplit(url).hostname == HOST and len(parts) >= 3 and parts[1] == "job"

    def posting(self, url: str) -> str:
        return _posting(url)

    def read_api(self, url: str) -> list[Field] | None:
        """The fields from the endpoint the apply page fetches, its job id from that page."""
        client = urlsplit(url).path.strip("/").split("/")[0]
        try:
            apply = urllib.request.Request(_posting(url) + "/apply", headers=HEADERS)
            with urllib.request.urlopen(apply, timeout=HTTP_TIMEOUT_S) as response:
                html = response.read().decode()
            job = JOB_ID.search(html)
            if job is None:
                return None
            entries = _get(f"jobs/{job.group(1)}/application-form/")
            terms = bool(TERMS_REQUIRED.search(html))
            return self._fields(entries, terms, lambda e: _options(e, client))
        except OSError, ValueError, KeyError, TypeError, Unsupported:
            return None

    def read_form(self, page: Page) -> list[Field]:
        with page.expect_response(
            lambda r: r.request.method == "GET" and "/application-form/" in r.url,
            timeout=LOAD_TIMEOUT_MS,
        ) as loaded:
            page.goto(_posting(page.url) + "/apply")
        response = loaded.value
        if response.headers.get("x-amzn-waf-action") == "challenge":
            raise Blocked("captcha: AWS WAF challenge")
        if not response.ok:
            raise Unsupported(f"posting_gone: the form answered {response.status}")
        self._entries = response.json()
        page.wait_for_selector("#submit-id-submit", timeout=LOAD_TIMEOUT_MS)
        # The lists of a multiple select come in their own requests, once the form is drawn.
        page.wait_for_function(
            "() => [...document.querySelectorAll('select.multiselect')]"
            ".every(s => s.options.length && s.classList.contains('select2-hidden-accessible'))",
            timeout=LOAD_TIMEOUT_MS,
        )
        terms = page.locator(f'input[name="{TERMS}"]').count() > 0
        return self._fields(self._entries, terms, lambda e: self._shown(page, e))

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        kinds = {str(e["id"]): _kind(e) for e in self._entries} | {TERMS: "terms"}
        for field_id, answer in answers.items():
            box = page.locator(f"#field{field_id}")
            named = page.locator(f'input[name="{field_id}"]')
            match kinds.get(field_id), answer:
                case "text", str():
                    box.fill(answer)
                case "number", str() if NUMBER.fullmatch(answer.strip()):
                    box.fill(answer.strip())
                case "number", str():
                    pass  # a number input holds no text: it reads back empty
                case "date", str():
                    box.evaluate(_DATE, answer)
                case "file", str():
                    box.set_input_files(answer)
                case "select", str():
                    box.select_option(label=answer)
                case "multiple", list():
                    box.evaluate(_SELECT, answer)
                case "radio", str():
                    named.and_(
                        page.locator(f"[value={json.dumps(answer, ensure_ascii=False)}]")
                    ).evaluate(_CHECK, True)
                case "checkbox", list():
                    for value in answer:
                        named.and_(
                            page.locator(f"[value={json.dumps(value, ensure_ascii=False)}]")
                        ).evaluate(_CHECK, True)
                case "boolean", str():
                    box.evaluate(_CHECK, answer == "Yes")
                case "terms", str():
                    named.evaluate(_CHECK, answer == "Yes")
                case kind, _:
                    raise Unsupported(
                        f"unsupported_rendering: cannot fill {field_id}, a {kind}, with {answer!r}"
                    )

    def read_back(self, page: Page) -> dict[str, Answer]:
        kinds = [[str(e["id"]), _kind(e)] for e in self._entries if _kind(e) != "entries"]
        if page.locator(f'input[name="{TERMS}"]').count():
            kinds.append([TERMS, "terms"])
        held = page.evaluate(_READ, kinds)
        return {k: v for k, v in held.items() if v not in (None, [])}

    def submit(self, page: Page) -> str | None:
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/application-form/" in r.url,
            timeout=SUBMIT_TIMEOUT_MS,
        ) as submitted:
            page.locator("#submit-id-submit").click()
        response = submitted.value
        if response.headers.get("x-amzn-waf-action") in ("challenge", "captcha"):
            raise Blocked("captcha: AWS WAF challenge")
        if not response.ok:
            raise Blocked(f"form_errors: {response.status} {response.text()[:300]}")
        return f"POST application-form → {response.status} ({page.url})"

    def _fields(
        self,
        entries: list[dict[str, Any]],
        terms: bool,
        options: Callable[[dict[str, Any]], list[str] | None],
    ) -> list[Field]:
        fields = [f for e in entries if (f := _field(e, options(e))) is not None]
        if terms:
            fields.append(Field(TERMS, TERMS_LABEL, FieldType.CHOICE, True, YES_NO))
        return fields

    @staticmethod
    def _shown(page: Page, entry: dict[str, Any]) -> list[str] | None:
        """The options the page shows for a choice, without the empty placeholder."""
        match _kind(entry):
            case "select" | "multiple":
                return page.locator(f"#field{entry['id']} option").evaluate_all(
                    "os => os.filter(o => o.value).map(o => o.text.trim())"
                )
            case "radio" | "checkbox":
                return page.locator(f'input[name="{entry["id"]}"]').evaluate_all(
                    "is => is.map(i => i.value)"
                )
        return None
