"""Dayforce (`jobs.dayforcehcm.com` career sites and their guest application), as
docs/adapters/dayforce.md observes.

The application is a wizard: Candidate Info (personal information, files, history entries),
one page per questionnaire, then Submit. The site context and the application template, both
public JSON, list every field; the page adds the options of its own dropdowns. Nothing but the
files reaches Dayforce before submit, so the read-back is the page, taken from each page
before leaving it.
"""

import dataclasses
import html
import re
from collections.abc import Callable
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

from atsfill.core.adapter import Unsupported
from atsfill.core.browser import Locator, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOST = "jobs.dayforcehcm.com"
JOB = re.compile(r"^/(?:([a-z]{2}(?:-[A-Za-z]{2,4})?)/)?([^/]+)/([^/]+)/jobs/(\d+)")
API = "https://jobs.dayforcehcm.com/api/geo/{ns}/"
FORM_ID = "jobPostingApplication_"
PERSONAL = f"#{FORM_ID}personalInfo_"
OPTION = ".ant-select-dropdown:has([id='{id}_list']) .ant-select-item-option"  # a select's own
NEXT = "main [test-id=application-next-step]"
RESUME = "section:has([test-id=resume-upload-title]) input[type=file]"
FORM_TIMEOUT_MS = 30_000
SETTLE_MS = 2_500
UPLOAD_TIMEOUT_MS = 30_000
STEP_TIMEOUT_MS = 20_000
UPDATE_TIMEOUT_MS = 5_000  # for a section to close, per Update click
SUBMIT_TIMEOUT_MS = 90_000
UPDATE_TRIES = 3
FOLLOWUP_TIMEOUT_MS = 3_000
DIAL_TIMEOUT_MS = 2_000  # per dial code tried, longest first
OPTIONS_TIMEOUT_MS = 5_000  # for a dropdown's options to render
NEXT_TRIES = 3
STEP_POLLS = 16  # of POLL_MS, before Next is clicked again
POLL_MS = 500
KEY_DELAY_MS = 10
YES_NO = ["Yes", "No"]
NO_YES = ["No", "Yes"]  # a box left as it is first
LONG_LABEL = 300  # a question whose description is a whole form is named by its display name
# A dial code several countries share, and the country a number under it is most likely from.
MAIN_COUNTRY = {"1": "United States", "7": "Russia", "44": "United Kingdom", "61": "Australia"}

# Personal information: the template's field name, the form's key and the field type.
PERSONAL_FIELDS = {
    "Prefix": ("prefixId", FieldType.CHOICE),
    "FirstName": ("firstName", FieldType.TEXT),
    "MiddleName": ("middleName", FieldType.TEXT),
    "LastName": ("lastName", FieldType.TEXT),
    "Suffix": ("suffixId", FieldType.CHOICE),
    "LinkedIn": ("linkedInURL", FieldType.TEXT),
    "Address1": ("address1", FieldType.TEXT),
    "Address2": ("address2", FieldType.TEXT),
    "Address3": ("address3", FieldType.TEXT),
    "Country": ("countryCode", FieldType.CHOICE),
    "StateProvince": ("stateCode", FieldType.TYPEAHEAD),
    "City": ("city", FieldType.TEXT),
    "ZipPostalCode": ("postalCode", FieldType.TEXT),
    "County": ("county", FieldType.TEXT),
    "PreferredContactMethod": ("preferredContactMethod", FieldType.CHOICE),
    "MobilePhone": ("mobilePhone", FieldType.PHONE),
    "HomePhone": ("homePhone", FieldType.PHONE),
    "Fax": ("fax", FieldType.PHONE),
    "Pager": ("pager", FieldType.PHONE),
    "CandidateSource": ("candidateSource", FieldType.CHOICE),
}
# History entries, as the page's own bundle maps them: the field name upper-cased, without
# spaces, to the form's key and its kind.
ENTRY_KINDS = {
    "text": FieldType.TEXT,
    "name": FieldType.TEXT,
    "digits": FieldType.TEXT,
    "gpa": FieldType.TEXT,
    "longText": FieldType.TEXT,
    "date": FieldType.DATE,
    "boolean": FieldType.CHOICE,
    "country": FieldType.CHOICE,
    "region": FieldType.TYPEAHEAD,
}
ENTRIES = {
    "WORKHISTORY": ("workHistory", {
        "EMPLOYERNAME": ("companyName", "text"), "POSITIONTITLE": ("title", "text"),
        "DIVISIONDEPT": ("department", "text"), "STARTDATE": ("effectiveStart", "date"),
        "ENDDATE": ("effectiveEnd", "date"), "CURRENTJOB": ("isCurrent", "boolean"),
        "COUNTRY": ("countryCode", "country"), "STATEPROVINCE": ("stateCode", "region"),
        "ADDRESS1": ("addressLine1", "text"), "ADDRESS2": ("addressLine2", "text"),
        "ADDRESS3": ("addressLine3", "text"), "CITY": ("city", "text"),
        "COUNTY": ("county", "text"), "ZIPPOSTALCODE": ("postalCode", "text"),
        "SALARY": ("salary", "digits"), "SUPERVISOR": ("supervisor", "name"),
        "REASONFORLEAVING": ("reasonForLeaving", "longText"),
        "DUTIESANDRESPONSIBILITIES": ("description", "longText"),
    }),
    "EDUCATIONHISTORY": ("educationHistory", {
        "SCHOOLNAME": ("schoolName", "text"), "DEGREE": ("degreeName", "text"),
        "MAJOR": ("majorName", "text"), "MINOR": ("minorName", "text"),
        "STARTDATE": ("effectiveStart", "date"), "ENDDATE": ("effectiveEnd", "date"),
        "EDUCATIONNOTCOMPLETED": ("notCompleted", "boolean"),
        "COUNTRY": ("countryCode", "country"), "STATEPROVINCE": ("stateCode", "region"),
        "CITY": ("city", "text"), "GPA": ("gpa", "gpa"),
    }),
}  # fmt: skip
CANDIDATE_INFO = {"PERSONALINFORMATION", "RESUMEUPLOAD", "AVAILABILITY", "SUBMIT", *ENTRIES}
# Work hours: a box per day, and a start and an end time for each day ticked.
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
HOURS = f"#{FORM_ID}workHoursAvailability_"
# Questionnaire answer types (the bundle's enum): 1 is a statement with nothing to answer.
STATEMENT, RADIO, TEXT, DROPDOWN, YES_NO_TYPE, CHECKBOX, MULTI = range(1, 8)
QUESTION_TYPES = {
    RADIO: FieldType.CHOICE,
    TEXT: FieldType.TEXT,
    DROPDOWN: FieldType.CHOICE,
    YES_NO_TYPE: FieldType.CHOICE,
    CHECKBOX: FieldType.CHOICE,
    MULTI: FieldType.MULTI_CHOICE,
}
DISABILITY = "DISABILITYQUESTIONNAIRE"  # its text questions (name, date, id) are the page's

# Each option of a dropdown, by scrolling its virtual list: only a screenful is in the DOM.
_HARVEST = """async (id) => {
  const holder = document.getElementById(id + '_list')?.closest('.rc-virtual-list-holder');
  if (!holder) return [...document.querySelectorAll(
    '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')]
    .map(o => o.getAttribute('title') || o.innerText.trim());
  const seen = new Set();
  for (let top = 0; ; top += holder.clientHeight / 2) {
    holder.scrollTop = top;
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    holder.querySelectorAll('.ant-select-item-option')
      .forEach(o => seen.add(o.getAttribute('title') || o.innerText.trim()));
    if (holder.scrollTop + holder.clientHeight >= holder.scrollHeight - 1) break;
  }
  holder.scrollTop = 0;
  return [...seen];
}"""

# Scrolls an open dropdown until an option shows one of `texts`; true when one does.
_REVEAL = """async ([id, texts]) => {
  const wanted = new Set(texts.map(t => t.trim().toLowerCase()));
  const holder = document.getElementById(id + '_list')?.closest('.rc-virtual-list-holder');
  const shown = () => [...(holder || document).querySelectorAll('.ant-select-item-option')]
    .some(o => wanted.has((o.getAttribute('title') || o.innerText).trim().toLowerCase()));
  if (!holder) return shown();
  for (let top = 0; ; top += holder.clientHeight / 2) {
    holder.scrollTop = top;
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    if (shown()) return true;
    if (holder.scrollTop + holder.clientHeight >= holder.scrollHeight - 1) return false;
  }
}"""

# Clicks the n-th option of the open dropdown if it still shows `text`.
_CLICK = """([selector, n, text]) => {
  const o = document.querySelectorAll(selector)[n];
  if (!o || (o.getAttribute('title') || o.innerText) !== text) return false;
  o.scrollIntoView({block: 'nearest'});
  o.click();
  return true;
}"""


class Dayforce:
    name = "dayforce"
    side_effects = [
        SideEffect(
            "load",
            "hiring_system",
            "an employer privacy policy shown before the form is accepted to open it",
        ),
        SideEffect(
            "upload", "hiring_system", "an attached file is stored and parsed by Dayforce at once"
        ),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._fields: dict[str, Field] = {}
        self._sections: list[dict[str, Any]] = []  # the questionnaires, in page order
        self._options: dict[str, dict[str, str]] = {}  # field id: page text to option
        self._values: dict[str, Answer | None] = {}  # read back from each page before leaving it

    def detect(self, url: str, page: Page | None) -> bool:
        return _job(url) is not None

    def posting(self, url: str) -> str:
        found = _job(url)
        return f"https://{HOST}/{found[1]}/{found[2]}/jobs/{found[3]}" if found else url

    def read_form(self, page: Page) -> list[Field]:
        found = _job(page.url)
        if found is None:
            raise Unsupported("posting_gone: not a Dayforce job page")
        _open_form(page, found)
        fields = self._read(found, lambda u: page.evaluate("u => fetch(u).then(r => r.json())", u))
        _close_dialogs(page, sms=None)
        for i, field in enumerate(fields):
            key = _personal_key(field.id)
            if field.type is FieldType.CHOICE and key and page.locator(_id(key)).count():
                options = _harvest(page, _id(key)) or field.options
                fields[i] = _with(field, options=options)
            elif field.type is FieldType.TEXT and key and page.locator(_id(key)).count():
                limit = page.locator(_id(key)).first.get_attribute("maxlength")
                fields[i] = _with(field, max_length=int(limit) if limit else None)
            elif field.type is FieldType.FILE and page.locator(_file(field.id)).count():
                accept = page.locator(_file(field.id)).first.get_attribute("accept") or ""
                accepted = [f".{a.strip()}" for a in accept.split(",") if a.strip()] or None
                fields[i] = _with(field, accept=accepted)
        for prefix, _ in ENTRIES.values():  # an entry's controls show once it is added
            texts = [i for i, f in enumerate(fields)
                     if f.id.startswith(f"{prefix}.") and f.type is FieldType.TEXT]  # fmt: skip
            if texts and page.locator(f"[test-id=add-{prefix.lower()}-record]").count():
                base = _open_entry(page, prefix)
                for i in texts:
                    control = page.locator(_id(base + fields[i].id.split(".")[1]))
                    limit = control.first.get_attribute("maxlength") if control.count() else None
                    fields[i] = _with(fields[i], max_length=int(limit) if limit else None)
        countries = next((f.options for f in fields if f.id == "countryCode"), None)
        fields = [
            _with(f, options=countries) if f.id.endswith(".countryCode") else f for f in fields
        ]
        self._fields = {f.id: f for f in fields}
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        found = _job(page.url)
        if found is None:
            raise Unsupported("posting_gone: not a Dayforce job page")
        page.evaluate("key => sessionStorage.removeItem(key)", _draft(found))
        _open_form(page, found)
        self._values = {}
        self._dialogs(page, answers)
        self._fill_candidate_info(page, answers)
        if not _next(page):
            return
        self._fill_questionnaires(page, answers)
        if page.locator("[test-id=embeddedassessment-openassessment-button]").count():
            raise Unsupported("external_step: an assessment on another site comes before submit")
        box = page.locator(f"#{FORM_ID}userAcknowledged")
        wanted = answers.get("userAcknowledged")
        if wanted and box.count() and box.is_visible() and box.is_checked() != (wanted == "Yes"):
            box.click()

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = {k: v for k, v in self._values.items() if v is not None}
        box = page.locator(f"#{FORM_ID}userAcknowledged")
        if box.count():
            values["userAcknowledged"] = "Yes" if box.is_checked() else "No"
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        before = page.url
        page.locator("main button", has_text=re.compile(r"^\s*Submit\s*$")).last.click()
        page.wait_for_url(lambda url: url != before, timeout=SUBMIT_TIMEOUT_MS)
        return page.url

    def _read(self, found: tuple[str, ...], get: Callable[[str], Any]) -> list[Field]:
        culture, ns, site, job = found
        culture = culture or "en-US"
        api = API.format(ns=ns)
        context = get(f"{api}sitecontext/{ns}/{site}/{culture}?jobPostingId={job}")
        template = get(f"{api}jobapplication/{ns}/{culture}/{context['jobBoardId']}/{job}")
        sections = sorted(template["sections"], key=lambda s: s.get("sequence") or 0)
        by_ref = {s.get("xRefCode"): s for s in sections}
        fields = [Field("email", "Email Address", FieldType.TEXT, True),
                  Field("confirmEmail", "Confirm Email Address", FieldType.TEXT, True)]  # fmt: skip
        personal = by_ref.get("PERSONALINFORMATION") or {}
        sources, extras = [], {}
        if any(f["fieldName"] == "CandidateSource" for f in personal.get("fields") or []):
            listed = get(f"{api}candidatesource/{ns}/{culture}")["candidateSources"]
            sources = [s["displayName"] for s in listed]
            explained = [s["displayName"] for s in listed if s.get("requiresAdditionalExplanation")]
        if {"Prefix", "Suffix"} & {f["fieldName"] for f in personal.get("fields") or []}:
            extras = get(f"{api}applicationformfieldsoptions/{ns}/{culture}")
        for f in _visible(personal):
            key, kind = PERSONAL_FIELDS.get(f["fieldName"], (None, None))
            if key is None or kind is None:
                if f["isRequired"]:
                    raise Unsupported(f"unsupported_rendering: personal field {f['fieldName']!r}")
                continue
            options = {
                "candidateSource": sources,
                "prefixId": [o["displayName"] for o in extras.get("prefix") or []],
                "suffixId": [o["displayName"] for o in extras.get("suffix") or []],
            }.get(key) or None
            fields.append(Field(key, f["displayName"], kind, bool(f["isRequired"]), options))
            if key == "candidateSource" and explained:
                asked = f"asked when {f['displayName']!r} is {' or '.join(explained)}"
                label = f"Additional details ({asked})"
                fields.append(Field("candidateSourceDescription", label, FieldType.TEXT, False))
        if context.get("hasActiveSMSProvider"):
            fields.append(Field("smsConsent", "Consent to receive SMS messages", FieldType.CHOICE,
                                False, NO_YES))  # fmt: skip
        if files := by_ref.get("RESUMEUPLOAD"):
            fields += [
                Field("resume", files["displayName"], FieldType.FILE, bool(files["isRequired"])),
                Field("coverLetter", "Cover Letter", FieldType.FILE, False),
                Field("additionalDocument", "Additional Documents", FieldType.FILE, False),
            ]
        for ref, (prefix, keys) in ENTRIES.items():
            if section := by_ref.get(ref):
                fields += _entry_fields(section, prefix, keys)
        hours = by_ref.get("AVAILABILITY") or {}
        if any(f["fieldName"] == "WorkHoursAvailability" for f in _visible(hours)):
            name, required = hours["displayName"], bool(hours["isRequired"])
            fields += [
                Field("workHours.days", f"{name}: days", FieldType.MULTI_CHOICE, required, DAYS),
                Field("workHours.start", f"{name}: start time (HH:MM)", FieldType.TEXT, required),
                Field("workHours.end", f"{name}: end time (HH:MM)", FieldType.TEXT, required),
                Field("workHours.comment", f"{name}: comments", FieldType.TEXT, False),
            ]
        for section in sections:
            ref = section.get("xRefCode")
            if (
                not section.get("questionnaire")
                and ref not in CANDIDATE_INFO
                and section.get("isRequired")
            ):
                raise Unsupported(
                    f"unsupported_rendering: {section['displayName']!r} section is required"
                )
        self._sections = [
            s for s in sections if s.get("questionnaire") and s.get("xRefCode") != "SUBMIT"
        ]
        for section in self._sections:
            for _, question in _questions(section):
                fields += _question_fields(section, question)
        if template.get("candidateAcknowledgement"):
            label = "Candidate Acknowledgement: " + _plain(template["candidateAcknowledgement"])
            fields.append(Field("userAcknowledged", label[:LONG_LABEL], FieldType.CHOICE, True,
                                YES_NO))  # fmt: skip
        self._fields = {f.id: f for f in fields}
        return fields

    def _fill_candidate_info(self, page: Page, answers: dict[str, Answer]) -> None:
        if "resume" in answers:  # first: the resume is imported over the personal information
            _attach(page, RESUME, _one(answers["resume"]))
            self._dialogs(page, answers)
        for file_id in ("coverLetter", "additionalDocument"):
            if file_id in answers:
                _attach(page, _file(file_id), _one(answers[file_id]))
        for file_id in ("resume", "coverLetter", "additionalDocument"):
            if file_id in self._fields and (name := _file_name(page, file_id)):
                self._values[file_id] = name
        order = sorted(  # the country first: the state's list is the country's
            (f for f in self._fields.values() if _personal_key(f.id)),
            key=lambda f: f.id != "countryCode",
        )
        for field in order:
            if field.id in answers:
                _fill_control(page, _id(field.id), field, answers[field.id])
            if field.id == "candidateSource" and "candidateSourceDescription" in answers:
                description = page.locator(_id("candidateSourceDescription"))
                with suppress(Timeout):  # it shows once a source asking for details is picked
                    description.wait_for(timeout=FOLLOWUP_TIMEOUT_MS)
                for _ in range(UPDATE_TRIES if description.count() else 0):
                    description.fill(_one(answers["candidateSourceDescription"]))
                    page.wait_for_timeout(POLL_MS)  # the box can render again once, empty
                    if description.input_value() == answers["candidateSourceDescription"]:
                        break
        for field in order:
            self._values[field.id] = _read_control(page, _id(field.id), field)
        description = page.locator(_id("candidateSourceDescription"))
        if description.count():
            self._values["candidateSourceDescription"] = description.input_value()
        _update(page, f"{PERSONAL}email")
        if page.locator(f"{HOURS}sunday_isChecked").count():
            self._fill_hours(page, answers)
        for prefix, _ in ENTRIES.values():
            entry = {k: v for k, v in answers.items() if k.startswith(f"{prefix}.")}
            if entry:
                self._fill_entry(page, prefix, entry)

    def _dialogs(self, page: Page, answers: dict[str, Answer]) -> None:
        if (sms := _close_dialogs(page, answers.get("smsConsent"))) is not None:
            self._values["smsConsent"] = sms

    def _fill_hours(self, page: Page, answers: dict[str, Answer]) -> None:
        """Ticks the days answered and gives each the same hours; an open section keeps Next
        from going on, and one with no day ticked does not save."""
        days = answers.get("workHours.days") or []
        for day in DAYS:
            box = page.locator(f"{HOURS}{day.lower()}_isChecked")
            if box.is_checked() != (day in days):
                box.click()
            if day in days:
                for end in ("start", "end"):
                    if f"workHours.{end}" in answers:
                        page.locator(f"{HOURS}{day.lower()}_{end}").fill(
                            _one(answers[f"workHours.{end}"])
                        )
        if "workHours.comment" in answers:
            page.locator(f"{HOURS}comment").fill(_one(answers["workHours.comment"]))
        ticked = [d for d in DAYS if page.locator(f"{HOURS}{d.lower()}_isChecked").is_checked()]
        self._values["workHours.days"] = ticked
        for end in ("start", "end"):
            shown = [page.locator(f"{HOURS}{d.lower()}_{end}").input_value() for d in ticked]
            self._values[f"workHours.{end}"] = shown[0] if len(set(shown)) == 1 else None
        self._values["workHours.comment"] = page.locator(f"{HOURS}comment").input_value()
        _update(page, f"{HOURS}sunday_isChecked")

    def _fill_entry(self, page: Page, prefix: str, answers: dict[str, Answer]) -> None:
        """Adds one entry to a history section and fills it."""
        base = _open_entry(page, prefix)
        fields = [f for f in self._fields.values() if f.id.startswith(f"{prefix}.")]
        for field in fields:
            if field.id in answers:
                _fill_control(page, _id(base + field.id.split(".")[1]), field, answers[field.id])
        for field in fields:
            self._values[field.id] = _read_control(page, _id(base + field.id.split(".")[1]), field)
        _update(page, f"main [id^='{base}']")  # not its dropdowns' lists, kept after main

    def _fill_questionnaires(self, page: Page, answers: dict[str, Answer]) -> None:
        left = list(self._sections)
        for n in range(len(self._sections)):
            title = _questionnaire_title(page) or ""  # some sites show no counter
            section = next((s for s in left if _same(s["displayName"], title)), left[0])
            left.remove(section)
            for j, question in _questions(section):
                base = f"{FORM_ID}sections_{n}_questionnaire_questions_{j}_"
                self._fill_question(page, base, question, answers)
            if not _next(page):
                return

    def _fill_question(
        self, page: Page, base: str, question: dict[str, Any], answers: dict[str, Answer]
    ) -> None:
        field_id = f"q{question['questionId']}"
        field = self._fields.get(field_id)
        if field is None:
            return
        target = _question_target(page, base, question)
        if target is None:
            return
        if field_id in answers and not field.prefilled and target.is_enabled():
            self._set_question(page, target, field, question, answers[field_id])
        self._values[field_id] = self._get_question(page, target, field, question)
        followup = f"{field_id}.followup"
        if followup in self._fields:
            boxes = page.locator(  # a box group's is one per option asking for comments
                f"[id='{base}answerFollowup'],"
                f" :is(input, textarea)[id^='{base}answerFollowups_'],"
                f" [id*='questionnaireFollowUp-{question['questionId']}-']"
            )
            if followup in answers:
                with suppress(Timeout):  # it shows once an option asking for comments is picked
                    boxes.first.wait_for(timeout=FOLLOWUP_TIMEOUT_MS)
            for box in boxes.all():
                if followup in answers and box.is_visible():
                    box.fill(_one(answers[followup]))
            shown = [b.input_value() for b in boxes.all() if b.is_visible()]
            self._values[followup] = shown[0] if shown else ""

    def _set_question(
        self, page: Page, target: Locator, field: Field, question: dict[str, Any], answer: Answer
    ) -> None:
        values = [answer] if isinstance(answer, str) else answer
        texts = _option_texts(question)
        match question["answerType"]:
            case 6:
                box = target
                if box.is_checked() != (values[0] == "Yes"):
                    box.click()
            case 3:
                target.fill(values[0])
            case 7:
                for wrapper in target.locator(".ant-checkbox-wrapper").all():
                    wanted = _option_of(wrapper.inner_text(), texts) in values
                    box = wrapper.locator("input[type=checkbox]")
                    if box.is_checked() != wanted:
                        wrapper.click()
            case _ if target.get_attribute("role") == "combobox":
                _pick(page, target, [t for t, o in texts.items() if o == values[0]])
            case _:
                for wrapper in target.locator(".ant-radio-wrapper").all():
                    if _option_of(wrapper.inner_text(), texts) == values[0]:
                        wrapper.click()
                        break

    def _get_question(
        self, page: Page, target: Locator, field: Field, question: dict[str, Any]
    ) -> Answer | None:
        texts = _option_texts(question)
        match question["answerType"]:
            case 6:
                return "Yes" if target.is_checked() else "No"
            case 3:
                return target.input_value()
            case 7:
                return [
                    _option_of(w.inner_text(), texts) or w.inner_text().strip()
                    for w in target.locator(".ant-checkbox-wrapper").all()
                    if w.locator("input[type=checkbox]").is_checked()
                ]
            case _ if target.get_attribute("role") == "combobox":
                shown = _selected(target)
                return _option_of(shown, texts) if shown else None
            case _:
                checked = target.locator(".ant-radio-wrapper-checked")
                return _option_of(checked.first.inner_text(), texts) if checked.count() else None


def _job(url: str) -> tuple[str, ...] | None:
    """(culture or "", namespace, career site, job posting id) of a Dayforce job URL."""
    parts = urlsplit(url)
    if parts.hostname != HOST or not (match := JOB.match(parts.path)):
        return None
    return (match[1] or "", match[2], match[3], match[4])


def _draft(found: tuple[str, ...]) -> str:
    """The sessionStorage key the page keeps its draft under."""
    return f"jobPostingApplication-{found[3]}-{found[1]}"


def _plain(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _same(a: str, b: str) -> bool:
    return _plain(a).casefold() == _plain(b).casefold()


def _with(field: Field, **changes: Any) -> Field:
    return dataclasses.replace(field, **changes)


def _one(answer: Answer) -> str:
    return answer if isinstance(answer, str) else answer[0]


def _visible(section: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        (f for f in section.get("fields") or [] if f.get("isVisible")),
        key=lambda f: f.get("sequence") or 0,
    )


def _entry_fields(
    section: dict[str, Any], prefix: str, keys: dict[str, tuple[str, str]]
) -> list[Field]:
    """One entry of a history section; its fields are required only when the section is."""
    fields = []
    for f in _visible(section):
        key, kind = keys.get(re.sub(r"\s", "", f["fieldName"]).upper(), (None, None))
        if key is None or kind is None:
            if f["isRequired"] and section["isRequired"]:
                raise Unsupported(f"unsupported_rendering: {prefix} field {f['fieldName']!r}")
            continue
        required = bool(f["isRequired"] and section["isRequired"])
        options = NO_YES if kind == "boolean" else None
        label = f"{section['displayName']}: {f['displayName']}"
        fields.append(Field(f"{prefix}.{key}", label, ENTRY_KINDS[kind], required, options))
    return fields


def _questions(section: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """The questions that take an answer, with their index in the page's control ids: a
    statement has no control but keeps its index, except on the disability form, laid out
    on its own."""
    questions = (section.get("questionnaire") or {}).get("questions") or []
    if section.get("xRefCode") == DISABILITY:
        questions = [q for q in questions if q["answerType"] != STATEMENT]
    return [(j, q) for j, q in enumerate(questions) if q["answerType"] != STATEMENT]


def _option_texts(question: dict[str, Any]) -> dict[str, str]:
    """Each text the page may show for an option, mapped to the option's label."""
    texts: dict[str, str] = {}
    for option in question["options"]:
        label = _option_label(option)
        for text in (option.get("description"), option.get("displayName")):
            if text and _plain(text):
                texts.setdefault(_plain(text).casefold(), label)
    return texts


def _option_label(option: dict[str, Any]) -> str:
    return _plain(option.get("description") or "") or _plain(option.get("displayName") or "")


def _option_of(text: str, texts: dict[str, str]) -> str | None:
    return texts.get(_plain(text).casefold())


def _question_fields(section: dict[str, Any], question: dict[str, Any]) -> list[Field]:
    kind = QUESTION_TYPES.get(question["answerType"])
    label = _plain(question.get("description") or "")
    if not label or len(label) > LONG_LABEL:
        label = _plain(question["displayName"])
    if kind is None:
        if question["isRequired"]:
            raise Unsupported(
                f"unsupported_rendering: {label!r} has answer type {question['answerType']}"
            )
        return []
    field_id = f"q{question['questionId']}"
    options = None
    if question["answerType"] == CHECKBOX:
        options = YES_NO
    elif kind in (FieldType.CHOICE, FieldType.MULTI_CHOICE):
        options = [_option_label(o) for o in question["options"]]
    prefilled = section.get("xRefCode") == DISABILITY and question["answerType"] == TEXT
    fields = [Field(field_id, label, kind, bool(question["isRequired"]), options,
                    prefilled=prefilled)]  # fmt: skip
    asking = [_option_label(o) for o in question["options"] if o.get("hasFollowUp")]
    if asking:
        followup = f"Additional comments (asked when {label!r} is {' or '.join(asking)})"
        fields.append(Field(f"{field_id}.followup", followup, FieldType.TEXT, False))
    return fields


def _personal_key(field_id: str) -> str | None:
    keys = {key for key, _ in PERSONAL_FIELDS.values()} | {"email", "confirmEmail"}
    return field_id if field_id in keys else None


def _id(key: str) -> str:
    return f"[id='{FORM_ID}personalInfo_{key}']" if "_" not in key else f"[id='{key}']"


def _file(file_id: str) -> str:
    return RESUME if file_id == "resume" else f"[id='{FORM_ID}files_{file_id}']"


def _open_form(page: Page, found: tuple[str, ...]) -> None:
    """Goes to the guest application of the posting, past the cookie banner."""
    culture, ns, site, job = found
    path = f"/{culture}" if culture else ""
    page.goto(f"https://{HOST}{path}/{ns}/{site}/jobs/{job}/apply/manualApplication"
              "?applicationSource=Manual")  # fmt: skip
    try:
        page.locator(f"{PERSONAL}email").wait_for(timeout=FORM_TIMEOUT_MS)
    except Timeout:
        raise Unsupported("posting_gone: no application form for this posting") from None
    page.wait_for_timeout(SETTLE_MS)  # the dialogs open once the site context has loaded


def _close_dialogs(page: Page, sms: Answer | None) -> str | None:
    """Accepts the employer's privacy policy, answers the SMS consent (no, unless `sms` is
    yes) and turns the optional cookies off: each dialog covers the form. Returns the SMS
    consent as the dialog held it, None when it did not open."""
    held = None
    agree = page.locator("[test-id=client-privacy-policy-modal-agree-checkbox]")
    if agree.count() and agree.is_visible():
        agree.check()
        page.locator("[test-id=client-privacy-policy-modal-save-button]").click()
    sms_save = page.locator("[test-id=sms-consent-dialog-save-button]")
    if sms_save.count() and sms_save.is_visible():
        value = "yes" if sms == "Yes" else "no"
        page.locator(f"[role=dialog] input[type=radio][value={value}]").check()
        checked = page.locator("[role=dialog] input[type=radio]:checked")
        held = {"yes": "Yes", "no": "No"}.get(checked.first.get_attribute("value") or "")
        sms_save.click()
    for button in ("cookie-preferences-banner-reject-button", "disable-all-cookie-details-button",
                   "save-button-cookie-details"):  # fmt: skip
        target = page.locator(f"[test-id={button}]")
        if target.count() and target.first.is_visible():
            target.first.click()
    return held


def _selector(target: Locator) -> Locator:
    return target.locator("xpath=ancestor::div[contains(@class,'ant-select-selector')]").first


def _open(target: Locator) -> None:
    """Opens a dropdown in the middle of the window: the step's button bar covers the bottom."""
    target.evaluate("e => e.scrollIntoView({block: 'center'})")
    _selector(target).click()


def _harvest(page: Page, selector: str) -> list[str]:
    target = page.locator(selector).first
    if target.is_disabled():
        return []
    _open(target)
    options = page.evaluate(_HARVEST, target.get_attribute("id"))
    target.press("Escape")
    return [o for o in options if o]


def _selected(target: Locator) -> str:
    shown = target.locator(
        "xpath=ancestor::div[contains(@class,'ant-select-selector')]//*[contains(@class,"
        "'ant-select-selection-item')]"
    )
    if not shown.count():
        return ""
    return shown.first.get_attribute("title") or shown.first.inner_text().strip()


def _pick(page: Page, target: Locator, texts: list[str]) -> bool:
    """Types each text into the dropdown and clicks the option it names exactly."""
    for text in texts:
        _open(target)
        options = page.locator(_option(target))
        if target.get_attribute("readonly") is None:
            target.fill("")
            target.press_sequentially(text[:40], delay=KEY_DELAY_MS)
        with suppress(Timeout):
            options.first.wait_for(timeout=OPTIONS_TIMEOUT_MS)
        page.evaluate(_REVEAL, [target.get_attribute("id"), [text]])
        if _click_option(page, target, _plain(text).casefold().__eq__):
            return True
        target.press("Escape")
    return False


def _option(target: Locator) -> str:
    """The options of the dropdown `target` opens, not of another one still closing."""
    return OPTION.format(id=target.get_attribute("id"))


def _click_option(page: Page, target: Locator, wanted: Callable[[str], bool]) -> bool:
    """Clicks the first option of the open dropdown whose text `wanted` takes, read in one go:
    the virtual list renders its options anew as it moves."""
    selector = _option(target)
    shown = page.locator(selector).evaluate_all(
        "os => os.map(o => o.getAttribute('title') || o.innerText)"
    )
    n = next((i for i, text in enumerate(shown) if wanted(_plain(text).casefold())), None)
    if n is None:
        return False
    return page.evaluate(_CLICK, [selector, n, shown[n]])


def _fill_control(page: Page, selector: str, field: Field, answer: Answer) -> None:
    value = _one(answer)
    target = page.locator(selector).first
    if field.type is FieldType.PHONE:
        _fill_phone(page, selector, value)
    elif not target.count() or target.is_disabled():
        return
    elif target.get_attribute("type") == "checkbox":
        if target.is_checked() != (value == "Yes"):
            target.click()
    elif target.get_attribute("role") == "combobox":
        if field.type is FieldType.TYPEAHEAD:
            _pick_suggestion(page, target, value)
        else:
            _pick(page, target, [value])
    else:
        target.fill(value)


def _pick_suggestion(page: Page, target: Locator, value: str) -> None:
    """Types the answer and clicks the first option that starts with it."""
    _open(target)
    target.press_sequentially(value[:40], delay=KEY_DELAY_MS)
    with suppress(Timeout):
        page.locator(_option(target)).first.wait_for(timeout=OPTIONS_TIMEOUT_MS)
    if not _click_option(page, target, lambda shown: shown.startswith(_plain(value).casefold())):
        target.press("Escape")


def _fill_phone(page: Page, selector: str, value: str) -> None:
    """Picks the dial code the number starts with, then types the rest."""
    key = selector.split("personalInfo_")[1].rstrip("']")
    item = page.locator(f".ant-form-item:has(label[for='{FORM_ID}personalInfo_{key}'])").first
    dial = item.locator("input[role=combobox]").first
    digits = re.sub(r"\D", "", value)
    if value.startswith("+") and dial.count():  # fax and pager have no dial code
        for size in (3, 2, 1):
            code = digits[:size]
            _open(dial)
            dial.fill("")
            dial.press_sequentially(f"+{code}", delay=KEY_DELAY_MS)
            options = page.locator(_option(dial)).filter(has_text=re.compile(rf"\+{code}(\s|$)"))
            with suppress(Timeout):
                options.first.wait_for(timeout=DIAL_TIMEOUT_MS)
            if options.count():
                main = options.filter(has_text=MAIN_COUNTRY.get(code, "\0"))
                (main if main.count() else options).first.click()
                digits = digits[size:]
                break
            dial.press("Escape")
    page.locator(selector).first.fill(digits)


def _read_control(page: Page, selector: str, field: Field) -> Answer | None:
    target = page.locator(selector).first
    if field.type is FieldType.PHONE:
        key = selector.split("personalInfo_")[1].rstrip("']")
        item = page.locator(f".ant-form-item:has(label[for='{FORM_ID}personalInfo_{key}'])").first
        code = re.search(r"\+\d+", _selected(item.locator("input[role=combobox]").first) or "")
        number = target.input_value() if target.count() else ""
        return f"{code.group(0) if code else ''}{number}" if number else None
    if not target.count():
        return None
    if target.get_attribute("type") == "checkbox":
        return "Yes" if target.is_checked() else "No"
    if target.get_attribute("role") == "combobox":
        return _selected(target) or None
    return target.input_value()


def _file_name(page: Page, file_id: str) -> str | None:
    """The attached file's name, shown in the file input's form item."""
    item = page.locator(_file(file_id)).first.locator(
        "xpath=ancestor::*[contains(@class,'ant-form-item')][1]"
    )
    if not item.count():
        return None
    names = re.findall(r"[^\n/\\]+\.\w{2,4}", item.inner_text())
    return names[-1].strip() if names else None


def _attach(page: Page, selector: str, path: str) -> None:
    """Sets the file and waits for its upload: a resume's import then rewrites the personal
    information, so nothing is typed before it is done."""
    with (
        suppress(Timeout),
        page.expect_response(
            lambda r: "/FileService/" in r.url and r.request.method == "POST",
            timeout=UPLOAD_TIMEOUT_MS,
        ),
    ):
        page.locator(selector).first.set_input_files(path)
    page.wait_for_timeout(SETTLE_MS)


def _open_entry(page: Page, prefix: str) -> str:
    """Adds the first entry of a history section, unless it is open; its controls' id prefix."""
    base = f"{FORM_ID}{prefix}_0_"
    if not page.locator(f"[id^='{base}']").count():
        page.locator(f"[test-id=add-{prefix.lower()}-record]").first.click()
        page.locator(f"[id^='{base}']").first.wait_for(timeout=FORM_TIMEOUT_MS)
    return base


def _update(page: Page, field: str) -> None:
    """Saves an open section with its Update button, the first after the section's `field`,
    until the section closes: a click while a dropdown is still closing is lost."""
    for _ in range(UPDATE_TRIES):
        first = page.locator(field).first
        if not first.count():
            return
        button = first.locator("xpath=following::button[@type='submit'][1]")
        page.keyboard.press("Escape")
        button.evaluate("e => e.scrollIntoView({block: 'center'})")
        button.click()
        with suppress(Timeout):
            first.wait_for(state="detached", timeout=UPDATE_TIMEOUT_MS)
            return
        if page.locator(".ant-form-item-explain-error:visible").count():
            return  # the section refuses its answers: Next reports the stop


def _next(page: Page) -> bool:
    """Goes to the next step; False when the page stays (a validation error), or when the
    next button is Submit, which `fill` never presses."""
    button = page.locator(NEXT)
    if not _says_next(button):
        return False
    before = _controls(page)
    for _ in range(NEXT_TRIES):  # a click right after a section's save can go unheeded
        if not button.count():
            return _controls(page) != before
        button.first.click()
        for _ in range(STEP_POLLS):
            page.wait_for_timeout(POLL_MS)
            if _controls(page) != before or not _says_next(button):  # Submit: the last step
                page.wait_for_timeout(POLL_MS)  # the step renders its controls in turn
                return True
            if page.locator(".ant-form-item-explain-error:visible").count():
                return False
    return False


def _says_next(button: Locator) -> bool:
    return bool(button.count()) and bool(
        re.search(r"next", button.first.inner_text(), re.IGNORECASE)
    )


def _controls(page: Page) -> list[str]:
    """The ids of the step's controls: what changes when the wizard moves on, not a control's
    error (`<id>_help`) showing."""
    return page.locator(f"main [id^='{FORM_ID}']:not([id$='_help'])").evaluate_all(
        "es => es.map(e => e.id)"
    )


def _questionnaire_title(page: Page) -> str | None:
    """The current questionnaire's name, under its "Questionnaire n of m" counter."""
    text = page.locator("main").inner_text()
    match = re.search(r"(?m)^\s*[^\W\d]+ \d+ [^\W\d]+ \d+\s*$\n+\s*([^\n]+)", text)
    return match[1].strip() if match else None


def _question_target(page: Page, base: str, question: dict[str, Any]) -> Locator | None:
    """The control of one question: a select's input, a radio or box group, a text, a box."""
    suffix = {TEXT: "answerText", MULTI: "selectedOptionIds", CHECKBOX: "isChecked"}
    target = page.locator(f"[id='{base}{suffix.get(question['answerType'], 'selectedOption')}']")
    return target.first if target.count() else None
