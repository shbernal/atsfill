"""SmartRecruiters (`jobs.smartrecruiters.com` job pages and their One Click apply form), as
docs/adapters/smartrecruiters.md observes.

The form is two pages: the profile, then the screening questions and consents. The second
page's questions come from the page's own `__OC_CONTEXT__`, so `read_form` lists them without
leaving the first page. Nothing but the resume reaches SmartRecruiters before submit, so the
read-back is the page: the profile as it stood when `fill` left it, then the second page.
"""

import json
import re
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

from atsfill.core.adapter import Gap, Unsupported, known_gap
from atsfill.core.browser import Locator, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOSTS = ("jobs.smartrecruiters.com", "www.smartr.me")
JOB = re.compile(r"^/([^/]+)/(\d+)")
ONECLICK = re.compile(r"^/oneclick-ui/company/([^/]+)/publication/([^/]+)")
APPLY_LINK = "a[href*='/oneclick-ui/company/']"
QUESTIONS = "sr-screening-questions-form"
CONTAINER = f"{QUESTIONS} [data-test=question-container]"
# The footer's buttons, named in the posting's language.
NEXT = "[data-test=footer-next] button"
SUBMIT = "[data-test=footer-submit] button"
FORM_TIMEOUT_MS = 30_000
OPTION_TIMEOUT_MS = 10_000
UPLOAD_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
NEXT_TIMEOUT_MS = 8_000
KEY_DELAY_MS = 20
LOCATION_KEY_DELAY_MS = 30
NEXT_TRIES = 3
YES_NO = ["Yes", "No"]

# The profile page: (field id, the input, the element carrying `label` and `required`).
PROFILE = [
    ("first_name", "input#first-name-input", "spl-input#first-name-input"),
    ("last_name", "input#last-name-input", "spl-input#last-name-input"),
    ("email", "input#email-input", "spl-input#email-input"),
    ("confirm_email", "input#confirm-email-input", "spl-input#confirm-email-input"),
    ("location", "oc-location-autocomplete input", "oc-location-autocomplete spl-autocomplete"),
    ("phone", "oc-phone-number input[type=tel]", "oc-phone-number spl-phone-field"),
    ("linkedin", "input#linkedin-input", "spl-input#linkedin-input"),
    ("facebook", "input#facebook-input", "spl-input#facebook-input"),
    ("x", "input#twitter-input", "spl-input#twitter-input"),
    ("website", "input#website-input", "spl-input#website-input"),
    ("resume", "oc-resume-upload input[type=file]", "oc-resume-upload"),
    (
        "message",
        "textarea#hiring-manager-message-input",
        "spl-textarea#hiring-manager-message-input",
    ),
]
PROFILE_TYPES = {
    "location": FieldType.TYPEAHEAD,
    "phone": FieldType.PHONE,
    "resume": FieldType.FILE,
}
# The profile's optional photo, an image upload beside the name.
PHOTO = "oc-avatar input[type=file]"
# Sections of the profile that take entries through their own sub-form.
ENTRIES = ("oc-experience", "oc-education")
# Screening question types: one field per entry of the question's `fields`.
FIELD_TYPES = {
    "text": FieldType.TEXT,
    "textarea": FieldType.TEXT,
    "radio": FieldType.CHOICE,
    "select": FieldType.CHOICE,
    "checkbox": FieldType.CHOICE,
}
SKIPPED = ("info",)  # text with no input
GAPS = {
    "files": ("attachments", "a list of attachments"),
    "languages": ("languages", "a list of languages"),
}

_CONTEXT = """() => {
  const s = [...document.scripts].find(s => s.textContent.includes("__OC_CONTEXT__"));
  if (!s) return null;
  const t = s.textContent;
  return JSON.parse(t.slice(t.indexOf("{"), t.lastIndexOf("}") + 1));
}"""

# Which question each container on the questions page holds, in page order. A text, select or
# checkbox carries `question_<id>`; a radio group or an EEO pair does not, and is the next
# question of its type: a hidden conditional question has no container.
_CONTAINERS = """(form, questions) => {
  const root = form.shadowRoot ?? form;
  let at = 0;
  return [...root.querySelectorAll("[data-test=question-container]")].map(box => {
    const marked = box.querySelector("[id^=question_], [name^=question_]");
    const mark = [marked?.id, marked?.getAttribute("name")].find(v => v?.startsWith("question_"));
    const id = mark?.replace(/^question_/, "");
    const FIELD = "SR-QUESTION-FIELD-";
    const field = [...box.querySelectorAll("*")].find(e => e.tagName.startsWith(FIELD));
    const type = field ? field.tagName.slice(FIELD.length).toLowerCase() : null;
    const i = questions.findIndex((q, n) => n >= at && (id ? q.id === id : q.type === type));
    if (i < 0) return null;
    at = i + 1;
    return questions[i].id;
  });
}"""


class SmartRecruiters:
    name = "smartrecruiters"
    known_gaps = {
        "entries": Gap.RENDERING,
        "attachments": Gap.RENDERING,
        "languages": Gap.RENDERING,
        "photo": Gap.RENDERING,
    }
    side_effects = [
        SideEffect(
            "upload", "hiring_system", "an attached resume is stored by SmartRecruiters at once"
        ),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._fields: dict[str, Field] = {}
        self._questions: dict[str, dict[str, Any]] = {}  # by question id
        self._conditions: list[dict[str, Any]] = []
        self._consents: dict[str, str] = {}  # field id: the consent box's label
        self._profile: dict[str, Answer] = {}  # the profile's read-back, taken before leaving it

    def detect(self, url: str, page: Page | None) -> bool:
        return _key(url) is not None

    def posting(self, url: str) -> str:
        return _key(url) or url

    def read_form(self, page: Page) -> list[Field]:
        if not ONECLICK.match(urlsplit(page.url).path):
            link = page.locator(APPLY_LINK).first
            try:
                link.wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
            except Timeout:
                raise Unsupported("posting_gone: no apply link on the job page") from None
            page.goto(link.get_attribute("href") or "")
        try:
            page.locator(PROFILE[0][1]).wait_for(timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        context = page.evaluate(_CONTEXT) or {}
        screening = context.get("screeningConfiguration") or {}
        self._questions = {q["id"]: q for q in screening.get("questions") or []}
        self._conditions = screening.get("conditionalConfigurations") or []
        fields = [f for f in (_profile_field(page, *row) for row in PROFILE) if f]
        if photo := self._photo(page):
            fields.append(photo)
        fields += [f for f in (self._entries(page, section) for section in ENTRIES) if f]
        for question in self._questions.values():
            fields += self._question_fields(question)
        self._consents = {}
        for scope in (context.get("consent") or {}).get("consentScopeConfigs") or []:
            if scope.get("checkboxRequired") is False:  # a notice with no box
                continue
            field_id = f"consent.{scope['scope']}"
            self._consents[field_id] = _plain(scope["label"])
            fields.append(Field(field_id, self._consents[field_id], FieldType.CHOICE,
                                bool(scope.get("required")), YES_NO))  # fmt: skip
        self._fields = {f.id: f for f in fields}
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        for field_id, selector, _ in PROFILE:
            if field_id in answers:
                _fill_profile(page, field_id, selector, answers[field_id])
        self._profile = _read_profile(page)
        _next(page)
        for question_id in self._questions:  # in page order: a trigger before its conditional
            box = self._container(page, question_id)
            if box is None:
                continue
            for field_id, field in self._question_answers(question_id):
                if field_id in answers:
                    _fill_question(box, self._questions[question_id], field, answers[field_id])
        for field_id, box in self._consent_boxes(page).items():
            if field_id in answers and box.is_checked() != (answers[field_id] == "Yes"):
                box.click()

    def read_back(self, page: Page) -> dict[str, Answer]:
        values: dict[str, Answer] = dict(self._profile)
        for question_id, question in self._questions.items():
            box = self._container(page, question_id)
            if box is None:
                continue
            for field_id, field in self._question_answers(question_id):
                if (value := _read_question(box, question, field)) is not None:
                    values[field_id] = value
        for field_id, box in self._consent_boxes(page).items():
            values[field_id] = "Yes" if box.is_checked() else "No"
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        before = page.url
        page.locator(SUBMIT).first.click()
        page.wait_for_url(lambda url: url != before, timeout=SUBMIT_TIMEOUT_MS)
        return page.url

    def _question_fields(self, question: dict[str, Any]) -> list[Field]:
        kind = question["type"]
        if kind in SKIPPED:
            return []
        label = _plain(question["label"])
        if condition := self._condition(question["id"]):
            trigger = self._questions[condition["questionId"]]
            picks = [v["label"] for f in trigger["fields"] for v in f["values"]
                     if v["id"] in condition["fieldValues"]]  # fmt: skip
            label += f" (asked when {_plain(trigger['label'])!r} is {' or '.join(picks)})"
        required = bool(question["required"]) and not condition
        unfillable = None
        if kind in GAPS:
            gap, what = GAPS[kind]
            unfillable = known_gap(self, gap, f"{question['id']} is {what}")
        elif kind != "currency" and any(e["type"] not in FIELD_TYPES for e in question["fields"]):
            unfillable = f"unhandled_rendering: {label!r} is a {kind} question"
        if unfillable:
            return [Field(question["id"], label, FieldType.TEXT, required, unfillable=unfillable)]
        fields = []
        for entry in question["fields"]:
            field_type = FieldType.TEXT if kind == "currency" else FIELD_TYPES[entry["type"]]
            options = [v["label"] for v in entry["values"]] or None
            if kind == "checkbox":
                options = YES_NO
            elif entry.get("multipleChoice"):
                field_type = FieldType.MULTI_CHOICE
            many = len(question["fields"]) > 1
            fields.append(Field(
                f"{question['id']}.{entry['id']}" if many else question["id"],
                f"{label}: {_plain(entry['label'])}" if many else label,
                field_type,
                required and entry.get("required", True),
                options,
            ))  # fmt: skip
        return fields

    def _question_answers(self, question_id: str) -> list[tuple[str, dict[str, Any]]]:
        """(field id, the question's field) for each field `read_form` listed."""
        entries = self._questions[question_id]["fields"]
        ids = [f"{question_id}.{e['id']}" if len(entries) > 1 else question_id for e in entries]
        return [
            (i, e)
            for i, e in zip(ids, entries, strict=True)
            if i in self._fields and not self._fields[i].unfillable
        ]

    def _photo(self, page: Page) -> Field | None:
        """The profile photo, shown: an image upload atsfill does not fill."""
        upload = page.locator(PHOTO).first
        if not upload.count():
            return None
        label = upload.get_attribute("aria-label") or "Photo"
        gap = known_gap(self, "photo", "photo is a profile photo")
        return Field("photo", label, FieldType.FILE, False, unfillable=gap)

    def _entries(self, page: Page, section: str) -> Field | None:
        """Experience or education, shown: entries added through a sub-form."""
        host = page.locator(section).first
        if not host.count():
            return None
        field_id = section.removeprefix("oc-")
        title = host.locator("[data-test=section-title]")
        label = title.first.inner_text().replace("*", "").strip() if title.count() else field_id
        required = host.locator("[data-test=section-required-mark]").count() > 0
        gap = known_gap(self, "entries", f"{field_id} is a list of entries")
        return Field(field_id, label, FieldType.TEXT, required, unfillable=gap)

    def _condition(self, question_id: str) -> dict[str, Any] | None:
        return next(
            (c for c in self._conditions if question_id in c["conditionalQuestionIds"]), None
        )

    def _consent_boxes(self, page: Page) -> dict[str, Locator]:
        """Each consent's box, in the order of the scopes: several open with a "Select all"."""
        boxes = page.locator("oc-consent spl-checkbox input[type=checkbox]")
        skip = boxes.count() - len(self._consents)
        if skip not in (0, 1):
            return {}
        return {field_id: boxes.nth(skip + n) for n, field_id in enumerate(self._consents)}

    def _container(self, page: Page, question_id: str) -> Locator | None:
        form = page.locator(QUESTIONS)
        if not form.count():
            return None
        held = form.first.evaluate(_CONTAINERS, list(self._questions.values()))
        if question_id not in held:
            return None
        return page.locator(CONTAINER).nth(held.index(question_id))


def _key(url: str) -> str | None:
    """`https://jobs.smartrecruiters.com/<employer>/<job id>`, or the One Click form's URL."""
    parts = urlsplit(url)
    if parts.hostname not in HOSTS:
        return None
    if match := ONECLICK.match(parts.path):
        return f"https://jobs.smartrecruiters.com/oneclick-ui/company/{match[1]}/publication/{match[2]}"
    if parts.hostname == HOSTS[0] and (match := JOB.match(parts.path)):
        return f"https://jobs.smartrecruiters.com/{match[1]}/{match[2]}"
    return None


def _plain(text: str) -> str:
    """A label without its markdown: links keep their text, emphasis marks go."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\s+", " ", text.replace("*", "")).strip()


def _profile_field(page: Page, field_id: str, selector: str, host: str) -> Field | None:
    element = page.locator(host).first
    if not page.locator(selector).count() or not element.count():
        return None
    if field_id == "resume":
        label = element.locator("[data-test=section-title]").inner_text()
        required = element.locator("[data-test=section-required-mark]").count() > 0
        accept = page.locator(selector).first.get_attribute("accept") or ""
        accepted = [a.strip() for a in accept.split(",") if a.strip()] or None
        return Field(field_id, label.replace("*", "").strip(), FieldType.FILE, required, None,
                     None, accepted)  # fmt: skip
    label = element.get_attribute("label") or field_id.replace("_", " ").capitalize()
    required = element.get_attribute("required") is not None
    return Field(field_id, label, PROFILE_TYPES.get(field_id, FieldType.TEXT), required)


def _fill_profile(page: Page, field_id: str, selector: str, answer: Answer) -> None:
    value = answer if isinstance(answer, str) else answer[0]
    target = page.locator(selector).first
    match field_id:
        case "resume":
            target.set_input_files(value)
            with suppress(Timeout):
                page.locator("oc-resume-upload .c-spl-file-list-item-name").first.wait_for(
                    timeout=UPLOAD_TIMEOUT_MS
                )
        case "location":  # takes one of its suggestions: the first for the typed text
            target.click()
            target.press_sequentially(value, delay=LOCATION_KEY_DELAY_MS)
            options = page.locator("oc-location-autocomplete spl-select-option")
            option = options.filter(has_text=re.compile(rf"^\s*{re.escape(value)}", re.I)).first
            try:
                option.wait_for(timeout=OPTION_TIMEOUT_MS)
            except Timeout:  # another option would switch the field to a country and postal code
                target.press("Escape")
                return
            option.click()
        case "phone":  # the number's dial code picks the country
            target.press_sequentially(value, delay=KEY_DELAY_MS)
        case _:
            target.fill(value)


def _next(page: Page) -> None:
    """Goes on to the questions page. Never Submit: a posting with no questions submits from
    the profile page. A click right after the last answer can go unheeded, so it is repeated."""
    button = page.locator(NEXT)
    for _ in range(NEXT_TRIES):
        if not button.count():
            return
        button.first.click()
        with suppress(Timeout):
            page.locator(QUESTIONS).first.wait_for(timeout=NEXT_TIMEOUT_MS)
            return


def _read_profile(page: Page) -> dict[str, Answer]:
    values: dict[str, Answer] = {}
    for field_id, selector, _ in PROFILE:
        target = page.locator(selector).first
        if not target.count():
            continue
        if field_id == "resume":
            names = page.locator("oc-resume-upload .c-spl-file-list-item-name")
            values[field_id] = names.first.inner_text().strip() if names.count() else ""
        elif field_id == "phone":  # the field's value: {"internationalFormat": "+1…", …}
            raw = page.locator("oc-phone-number spl-phone-field").first.get_attribute("value")
            with suppress(ValueError, TypeError, AttributeError):
                values[field_id] = json.loads(raw or "{}").get("internationalFormat") or ""
        else:
            values[field_id] = target.input_value()
    return values


def _option_ids(entry: dict[str, Any]) -> dict[str, str]:
    return {v["label"]: v["id"] for v in entry["values"]}


def _inputs(box: Locator, entry: dict[str, Any], question: dict[str, Any]) -> Locator:
    """The input of one of the question's fields: an EEO pair holds one per field."""
    n = [e["id"] for e in question["fields"]].index(entry["id"])
    return box.locator("input:not([type=checkbox]), textarea").nth(n)


def _fill_question(
    box: Locator, question: dict[str, Any], entry: dict[str, Any], answer: Answer
) -> None:
    values = [answer] if isinstance(answer, str) else answer
    match entry["type"]:
        case "radio":
            box.locator(f"spl-radio[value='{_option_ids(entry).get(values[0], '')}']").click()
        case "checkbox":
            target = box.locator("input[type=checkbox]").first
            if target.is_checked() != (values[0] == "Yes"):
                target.click()
        case "select":
            target = _inputs(box, entry, question)
            for value in values:
                option_id = _option_ids(entry).get(value)
                if option_id is None:
                    continue
                target.click()
                target.press_sequentially(value[:20], delay=KEY_DELAY_MS)
                option = box.locator(f"spl-select-option[value='{option_id}']").first
                try:
                    option.wait_for(timeout=OPTION_TIMEOUT_MS)
                except Timeout:
                    target.press("Escape")
                    continue
                option.click()
        case _:
            _inputs(box, entry, question).fill(values[0])


def _read_question(box: Locator, question: dict[str, Any], entry: dict[str, Any]) -> Answer | None:
    match entry["type"]:
        case "radio":
            checked = box.locator("spl-radio[aria-checked=true]")
            return checked.first.get_attribute("label") if checked.count() else None
        case "checkbox":
            return "Yes" if box.locator("input[type=checkbox]").first.is_checked() else "No"
        case "select" if entry.get("multipleChoice"):  # the picked option ids, comma separated
            picked = box.locator("spl-multiselect-autocomplete").first.get_attribute("value") or ""
            labels = {v: k for k, v in _option_ids(entry).items()}
            return [labels.get(i, i) for i in picked.split(",") if i]
        case _:
            return _inputs(box, entry, question).input_value()
