"""ADP MyJobs, ADP's candidate experience (`myjobs.adp.com/<career site>/cx/job/<id>`), as
docs/adapters/adpmyjobs.md observes.

Each career site keeps its own candidate profile, without a password: every sign-in is an
emailed OTP. Apply sends the application; the form is what follows it, a workflow of sections
each saved by its Next, the last Next completing the application. The read lists the open
sections from ADP's API with the page's token; the fill answers each on the page, page by page,
and the read-back is each page's values before its Next.
"""

import re
import time
from contextlib import suppress
from dataclasses import dataclass, field, replace
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from atsfill.core import matching, otp
from atsfill.core.adapter import (
    Blocked,
    Gap,
    NotTaken,
    Refused,
    SignedOut,
    SignUpDetails,
    Unsupported,
    known_gap,
)
from atsfill.core.browser import Locator, Page, Request, Timeout
from atsfill.core.results import Answer, AnyOf, Condition, Field, FieldType, Is, SideEffect

HOST = "myjobs.adp.com"
POSTING = re.compile(r"^/(?P<site>[^/]+)/cx/(?:job/(?P<job>\d+)|job-details)")
API = "https://my.adp.com/myadp_prefix/mycareer/api"
QUESTIONNAIRES = f"{API}/rm/staffing/v3/work-fulfillment/recruiting-questionnaires"
APPLICATIONS = f"https://{HOST}/cx/staffing/v2/job-applications"
SIGN_IN_TIMEOUT_MS = 60_000  # the sign-in page takes ~20 s to render its form
SIGN_IN_STEP_TIMEOUT_MS = 30_000
SIGNED_IN_TIMEOUT_MS = 60_000
POSTING_TIMEOUT_MS = 45_000
SECTION_TIMEOUT_MS = 45_000
LOAD_TRIES = 3
SUBMIT_TIMEOUT_MS = 90_000
SETTLE_MS = 1_500  # an answer shows its follow-ups, a Next saves and routes
OPTION_TIMEOUT_MS = 10_000
UPLOAD_TIMEOUT_MS = 60_000
KEY_DELAY_MS = 40
SKIPPED = {"INSTRUCTIONS", "HEADER"}
YES_NO = ["Yes", "No"]

_FETCH = """async ([url, role, site]) => {
  const headers = {myjobstoken: sessionStorage.getItem('myJobsToken') ?? ''};
  if (role) headers.rolecode = 'manager';
  if (site) headers.careersiteid = site;
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const sep = url.includes('?') ? '&' : '?';
  const response = await fetch(`${url}${sep}tz=${tz}`, {credentials: 'include', headers});
  const said = response.ok ? '' : (await response.text()).slice(0, 300);
  if (!response.ok) throw new Error(`${url} answered ${response.status}: ${said}`);
  return response.json();
}"""


@dataclass
class Section:
    component: str  # `contact`, `prescreen1`
    id: str  # `contact.1520639`
    title: str
    completed: bool
    fields: list[Field] = field(default_factory=list)
    pages: list[set[str]] = field(default_factory=list)  # the question ids on each page
    review: bool = False  # the pages end on a review of the answers, before that last Next


class ADPMyJobs:
    name = "adpmyjobs"
    per_employer = True  # a candidate profile per career site: the scope is the site
    asks = {"name", "phone"}
    known_gaps = {"recruiting_workflow": Gap.FLOW}
    side_effects = [
        SideEffect("sign_up", "employer", "login --create creates the career site's profile"),
        SideEffect("load", "employer", "Apply sends the application before the form shows"),
        SideEffect("fill", "employer", "each section but the last is saved to it when left"),
        SideEffect("submit", "employer", "the last section's Next completes the application"),
    ]

    def __init__(self) -> None:
        self._site_id = ""  # the career site's `careersiteid`
        self._sections: list[Section] = []
        self._codes: dict[str, dict[str, str]] = {}  # by field id: an option's label by code
        self._values: dict[str, Answer] = {}
        self._fixed: set[str] = set()  # disabled questions, their value shown as text

    def detect(self, url: str, page: Page | None) -> bool:
        return _posting(url) is not None

    def posting(self, url: str) -> str:
        found = _posting(url)
        return f"https://{HOST}/{found[0]}/cx/job/{found[1]}" if found else url

    def account_scope(self, url: str) -> str:
        found = _posting(url)
        return found[0] if found else ""

    def login_url(self, scope: str | None) -> str:
        return f"https://{HOST}/{scope}/auth"

    def signed_in(self, request: Request, scope: str | None) -> bool:
        """Signed out, ADP's API redirects to its login page."""
        response = request.get(f"https://{HOST}/cx/staffing/v2/job-applications", max_redirects=0)
        return not 300 <= response.status < 400

    def sign_in_by_otp(self, page: Page, email: str) -> None:
        _sign_in(page, email, None)

    def sign_up_by_otp(self, page: Page, email: str, details: SignUpDetails) -> None:
        _sign_in(page, email, details)

    def open(self, page: Page) -> str | None:
        """Loads the posting signed in: Apply sends the application. One the account applied
        to has its unfinished workflow continued, as My Jobs does."""
        found = _posting(page.url)
        if found is None:
            raise RuntimeError(f"{page.url} is not an ADP MyJobs posting")
        site, req = found
        settings = _career_site(page, site)
        self._site_id = str(settings["externalId"])
        if not (settings.get("enabledFeatures") or {}).get("candidateApplicationWorkflow"):
            detail = f"{site} continues each application on ADP Recruiting, after Apply"
            raise Unsupported(known_gap(self, "recruiting_workflow", detail))
        page.goto(f"https://{HOST}/{site}/cx/job-details?reqId={req}")
        deadline = time.monotonic() + POSTING_TIMEOUT_MS / 1000
        while not page.evaluate("() => !!sessionStorage.getItem('myJobsToken')"):
            if urlsplit(page.url).path.endswith("/auth"):
                raise SignedOut("login_required: the posting sent to the sign-in")
            if time.monotonic() > deadline:
                raise RuntimeError("the posting loaded without a session token")
            page.wait_for_timeout(SETTLE_MS)
        with suppress(Timeout):  # left with its requests in flight, the workflow can spin
            page.wait_for_load_state("networkidle", timeout=POSTING_TIMEOUT_MS)
        if application := self._application(page, req):
            if (application.get("applicationStatusCode") or {}).get("label") != "CONTINUE":
                raise Unsupported(f"already_sent: the account applied ({_status(application)})")
            assignment = application["assignmentID"]
            workflow = self._get(
                page, f"{QUESTIONNAIRES}/WORKFLOW?assignmentID={assignment}&rmapp=forms"
            )
            component = next(
                (
                    labels["component"]
                    for raw in workflow.get("questionSections") or []
                    if raw.get("completionStatusCode") != "completed"
                    and "component" in (labels := _labels(raw))
                ),
                None,
            )
            if component is None:
                raise Unsupported("already_sent: the account completed its application")
            page.goto(
                f"https://{HOST}/{site}/cx/app?reqId={req}&assignmentId={assignment}"
                f"&workflowComponent={component}"
            )
            self._wait_for_section(page)
            return None
        apply = page.get_by_role("button", name="Apply", exact=True)
        try:
            apply.first.wait_for(timeout=max(0, deadline - time.monotonic()) * 1000)
        except Timeout:
            raise Unsupported("posting_gone: the posting shows no Apply") from None
        apply.first.click()
        deadline = time.monotonic() + POSTING_TIMEOUT_MS / 1000
        while not (made := self._application(page, req)):
            if time.monotonic() > deadline:
                raise RuntimeError("Apply listed no application")
            page.wait_for_timeout(SETTLE_MS)
        self._wait_for_section(page)
        return f"Apply made application {made['assignmentID']}, listed {_status(made)}"

    def _application(self, page: Page, req: str) -> dict[str, Any] | None:
        applications = self._get(page, APPLICATIONS).get("jobApplications") or []
        return next((a for a in applications if a.get("reqID") == req), None)

    def browser_read(self, page: Page) -> list[Field]:
        """Every section the workflow lists, from its questionnaire; a first section that is
        only a statement to agree to is agreed and saved, as ADP lists the rest only after."""
        sections = self._workflow(page)
        while len(sections) == 1 and not sections[0].completed and _statement(sections[0]):
            self._answer(page, sections[0], {f.id: "Yes" for f in sections[0].fields})
            self._next(page, sections[0])
            sections = self._workflow(page)
        self._sections = [s for s in sections if not s.completed]  # sent, with no way back
        return [f for s in self._sections for f in s.fields]

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        """Answers each section page by page, saving each with Next, up to the last section's
        last page, or its review."""
        self._values = {}
        answers = {f.id: f.prefilled for s in self._sections for f in s.fields
                   if f.prefilled is not None} | answers  # fmt: skip
        for section in self._sections:
            for number, ids in enumerate(section.pages or [set()]):
                _on_section(page, section)
                _to_page(page, section, ids)
                if unlisted := _unlisted(page, section):
                    raise NotTaken(f"{section.title} shows {unlisted}, which the read did not list")
                self._answer(page, section, answers)
                self._values |= self._read(page, section)
                last = number == max(len(section.pages), 1) - 1
                if section is self._sections[-1] and last and not section.review:
                    return
                self._next(page, section, last and not section.review)
            if section.review and section is not self._sections[-1]:
                self._next(page, section)  # the review's Next saves the section

    def read_back(self, page: Page) -> dict[str, Answer]:
        """Each section's values as it was left."""
        return dict(self._values)

    def submit(self, page: Page) -> str | None:
        """The last section's Next, which completes the application: the page then says so,
        at the same address."""
        page.get_by_role("button", name="Next").click()
        done = page.get_by_text("Application Submitted")
        try:
            done.first.wait_for(timeout=SUBMIT_TIMEOUT_MS)
        except Timeout:
            raise RuntimeError(f"the last Next showed no confirmation: {_errors(page)}") from None
        return "the page said Application Submitted!"

    def _get(self, page: Page, url: str) -> Any:
        """ADP's API as the page calls it: myjobs's with the token alone, my.adp.com's with a
        role too, and its forms' with the career site."""
        role = url.startswith(API)
        return page.evaluate(_FETCH, [url, role, self._site_id if "/api/rm/" in url else ""])

    def _assignment(self, page: Page) -> str:
        assignment = parse_qs(urlsplit(page.url).query).get("assignmentId")
        if not assignment:
            raise RuntimeError(f"{page.url} shows no application")
        return assignment[0]

    def _wait_for_section(self, page: Page) -> None:
        page.wait_for_url(
            lambda u: "workflowComponent=" in u, timeout=SECTION_TIMEOUT_MS, wait_until="commit"
        )
        workflow = page.url
        for attempt in range(LOAD_TRIES):  # it can spin: loaded again from the posting, it shows
            try:
                page.get_by_role("button", name="Next").wait_for(timeout=SECTION_TIMEOUT_MS)
                return
            except Timeout:
                if attempt == LOAD_TRIES - 1:
                    raise
                site = urlsplit(workflow).path.split("/")[1]
                req = parse_qs(urlsplit(workflow).query)["reqId"][0]
                page.goto(f"https://{HOST}/{site}/cx/job-details?reqId={req}")
                with suppress(Timeout):
                    page.wait_for_load_state("networkidle", timeout=POSTING_TIMEOUT_MS)
                page.goto(workflow)

    def _workflow(self, page: Page) -> list[Section]:
        assignment = self._assignment(page)
        workflow = self._get(
            page, f"{QUESTIONNAIRES}/WORKFLOW?assignmentID={assignment}&rmapp=forms"
        )
        sections = []
        for raw in workflow.get("questionSections") or []:
            labels = _labels(raw)
            section = Section(
                labels.get("component", raw["sectionID"]),
                raw["sectionID"],
                raw.get("sectionTitle") or "",
                raw.get("completionStatusCode") == "completed",
            )
            if not section.completed:
                section.fields = self._section_fields(page, section, assignment)
            sections.append(section)
        return sections

    def _section_fields(self, page: Page, section: Section, assignment: str) -> list[Field]:
        if section.component.startswith("prescreen"):
            url = f"{API}/staffing/v2/job-application/workflow/{assignment}/prescreen.questions"
            questions = self._get(page, url).get("prescreeningQuestions") or []
            return self._prescreen(section, questions)
        url = f"{QUESTIONNAIRES}/{section.id}?assignmentID={assignment}&rmapp=forms"
        pages = self._get(page, url).get("questionSections") or []
        section.review = any(_labels(p).get("sectionReview") == "true" for p in pages)
        pages = [p for p in pages if _labels(p).get("sectionReview") != "true"]
        questions = []
        for p in pages:  # a form of pages lists the open page's questions, the rest by id
            if "questions" not in p:
                listed = self._get(page, f"{url}&sectionID={p['sectionID']}")
                p = next(iter(listed.get("questionSections") or []), {})
            questions += p.get("questions") or []
            section.pages.append({q["questionID"] for q in p.get("questions") or []})
        # A form of pages that is not open yet lists none: the fill stops on its questions,
        # which a read once it is open lists.
        fields = [f for q in questions if (f := self._question(page, section, q, questions))]
        fields = _shown_by_dependencies(section, questions, fields, self._codes)
        return _required_by_answers(section, questions, fields, self._codes)

    def _question(
        self, page: Page, section: Section, raw: dict[str, Any], questions: list[dict[str, Any]]
    ) -> Field | None:
        kind = raw.get("answerDataTypeCode")
        if kind in SKIPPED or (raw.get("disabledIndicator") and not raw.get("answerDefaultValue")):
            return None  # a text, or a value the system fills itself
        field_id = f"{section.component}.{raw['questionID']}"
        label = _text(raw.get("questionText") or raw["questionID"])
        country = _country(questions)
        for x in raw.get("questionLabels") or []:  # `ZIP Code:` for `Postcode` in the US
            if country and x["code"].endswith(f"_{country}_label"):
                label = _text(x["value"]).rstrip(":")
        required = bool(raw.get("requiredIndicator"))
        default = raw.get("answerDefaultValue")
        options: list[str] | None = None
        match kind:
            case "TEXT" | "EMAIL":
                type_ = FieldType.TEXT
            case "PHONENUMBER":
                type_ = FieldType.PHONE
            case "BOOLEAN" | "CHECKBOX":
                type_, options = FieldType.CHOICE, YES_NO
                self._codes[field_id] = {"true": "Yes", "false": "No"}
            case "UPLOAD":
                type_ = FieldType.FILE
            case "ESIGNATURE":
                type_, label = FieldType.TEXT, "Electronic signature: your full legal name"
            case (
                "SELECTION"
                | "COUNTRIES"
                | "STATES"
                | "RADIO"
                | "LOOKUP"
                | "MONTHS"
                | "DAYS_OF_MONTH"
            ):
                type_ = FieldType.CHOICE
                self._codes[field_id] = self._options(page, raw, questions)
                options = list(self._codes[field_id].values())
            case _:
                reason = f"unhandled_rendering: {label!r} is a {kind} question"
                return Field(field_id, label, FieldType.TEXT, required, unfillable=reason)
        if isinstance(default, bool):
            default = "true" if default else "false"
        prefilled = self._codes.get(field_id, {}).get(default, default) if default else None
        if raw.get("disabledIndicator"):
            self._fixed.add(field_id)
        return Field(
            field_id, label, type_, required, options, prefilled=prefilled,
            max_length=raw.get("maxLength"), min_length=raw.get("minLength"),
        )  # fmt: skip

    def _options(
        self, page: Page, raw: dict[str, Any], questions: list[dict[str, Any]]
    ) -> dict[str, str]:
        """The codelist behind a question, by code; a state's, the default country's."""
        link = next((x["href"] for x in raw.get("links") or [] if x.get("linkID") == "OPTIONS"), "")
        if not link:
            return {}
        url = f"{API}/rm{link}&$skip=0&$top=1000"
        if raw.get("answerDataTypeCode") == "STATES" and (country := _country(questions)):
            url += "&$filter=" + quote(f'filter("COMPONENTcountry","{country}","eq")')
        return {i["codeValue"]: i["longName"] for i in self._get(page, url).get("listItems") or []}

    def _prescreen(self, section: Section, questions: list[dict[str, Any]]) -> list[Field]:
        """The prescreen questions in order: an answer goes on to the next, or to the question
        it branches to, or ends them; a question shows once an answer leads to it, a text to
        read passing on its own condition."""
        ordered = sorted(questions, key=lambda q: q.get("orderId", 0))
        ids = {q["questionId"]: f"{section.component}.{q['questionId']}" for q in ordered}
        incoming: dict[str, list[Condition | None]] = (
            {ordered[0]["questionId"]: [None]} if ordered else {}
        )
        fields = []
        for i, raw in enumerate(ordered):
            qid = raw["questionId"]
            ways = incoming.get(qid, [])
            if not ways:
                continue  # no answer leads to it
            here = None if None in ways else _any(ways)
            following = ordered[i + 1]["questionId"] if i + 1 < len(ordered) else None
            answers = raw.get("possibleAnswers") or []
            labels = {a["answerId"]: _text(a["answerLabel"]) for a in answers}
            if not answers and following:
                free = raw["questionType"] != "Instructions"
                incoming.setdefault(following, []).append(Is(ids[qid]) if free else here)
            leads: dict[str, list[str]] = {}
            for answer in answers:
                target = answer.get("branchingQuestionId") or (
                    None if answer.get("endOfQuestionIndicator") else following
                )
                if target:
                    leads.setdefault(target, []).append(labels[answer["answerId"]])
            for target, chosen in leads.items():
                every = len(chosen) == len(labels)
                incoming.setdefault(target, []).append(Is(ids[qid], None if every else chosen))
            label = _text(raw.get("questionDescription") or qid)
            match raw["questionType"]:
                case "Instructions":
                    continue
                case "Single choice":
                    self._codes[ids[qid]] = labels
                    f = Field(ids[qid], label, FieldType.CHOICE, True, list(labels.values()))
                case "Free form":
                    f = Field(ids[qid], label, FieldType.TEXT, True)
                case other:
                    reason = f"unhandled_rendering: {label!r} is a {other} question"
                    f = Field(ids[qid], label, FieldType.TEXT, True, unfillable=reason)
            fields.append(replace(f, shown_when=here))
        return fields

    def _answer(self, page: Page, section: Section, answers: dict[str, Answer]) -> None:
        """Answers the section's questions in order, each once it shows: a prescreen question
        shows once the one before is answered."""
        _on_section(page, section)
        for f in section.fields:
            if f.id not in answers or f.unfillable or f.id in self._fixed:
                continue
            box = _box(page, section, f)
            if not box.count():
                continue  # not shown: its condition is unmet
            if _value(box, f, self._codes.get(f.id, {})) == answers[f.id]:
                continue  # a list's default, the profile's value
            _set(page, box, f, str(answers[f.id]), self._codes.get(f.id, {}))
            page.wait_for_timeout(SETTLE_MS if f.type is FieldType.CHOICE else 0)

    def _read(self, page: Page, section: Section) -> dict[str, Answer]:
        values: dict[str, Answer] = {}
        for f in section.fields:
            box = _box(page, section, f)
            if f.id in self._fixed and box.count() and f.prefilled is not None:
                values[f.id] = f.prefilled  # shown as text, as the profile has it
            elif not f.unfillable and box.count():
                value = _value(box, f, self._codes.get(f.id, {}))
                if value:
                    values[f.id] = value
        return values

    def _next(self, page: Page, section: Section, last: bool = True) -> None:
        """Next, once the section is left, or on a page of several, once the next page shows;
        the page's errors where it stays."""
        shown = _groups(page)
        page.get_by_role("button", name="Next").click()
        deadline = time.monotonic() + SECTION_TIMEOUT_MS / 1000
        while f"workflowComponent={section.component}" in page.url:
            if not last and _groups(page) != shown:
                break
            if time.monotonic() > deadline:
                raise NotTaken(f"{section.title} did not save: {_errors(page)}")
            page.wait_for_timeout(SETTLE_MS)
        page.wait_for_timeout(SETTLE_MS)


def _posting(url: str) -> tuple[str, str] | None:
    """The career site and the posting's id."""
    parts = urlsplit(url)
    if parts.hostname != HOST or not (match := POSTING.match(parts.path)):
        return None
    req = match["job"] or next(iter(parse_qs(parts.query).get("reqId", [])), None)
    return (match["site"].lower(), req) if req else None


def _career_site(page: Page, site: str) -> dict[str, Any]:
    """The career site's settings: its id, which ADP's API asks with each request, and the
    features it enables."""
    found = page.evaluate(
        "url => fetch(url).then(r => r.ok ? r.json() : null)",
        f"https://{HOST}/public/staffing/v1/career-site/{site}",
    )
    if not found:
        raise Unsupported(f"posting_gone: no career site {site}")
    return found["settings"]


def _sign_in(page: Page, email: str, details: SignUpDetails | None) -> None:
    """ADP's sign-in: the email, then the passcode it emails, which signs in once typed and the
    employer's privacy policy agreed to. An email the career site does not know gets "Create
    your candidate profile" first: its phone, name and that policy's box, then the passcode's
    way, Email Message."""
    box = page.get_by_label("Email Address")
    try:
        box.wait_for(timeout=SIGN_IN_TIMEOUT_MS)
    except Timeout:
        raise Unsupported(f"unhandled_rendering: no ADP sign-in on {page.url}") from None
    box.fill(email)
    page.get_by_role("button", name="Continue").first.click()
    passcode = page.get_by_label("Passcode")
    create = page.get_by_text("Create your candidate profile")
    method = page.get_by_role("button", name="Send passcode")
    passcode.or_(create).or_(method).first.wait_for(timeout=SIGN_IN_STEP_TIMEOUT_MS)
    if create.count():
        if details is None:
            raise Refused("no_account")
        _create_profile(page, details)
        passcode.or_(method).first.wait_for(timeout=SIGN_IN_STEP_TIMEOUT_MS)
    if method.count() and not passcode.count():
        page.get_by_text("Email Message").click()
        method.click()
        passcode.wait_for(timeout=SIGN_IN_STEP_TIMEOUT_MS)
    otp.enter(passcode, email, "ADP's sign-in")
    agree = page.get_by_role("button", name="Agree", exact=True)
    deadline = time.monotonic() + SIGNED_IN_TIMEOUT_MS / 1000
    while urlsplit(page.url).path.endswith("/auth"):
        if agree.count() and agree.is_visible():  # the employer's privacy policy, once typed
            agree.click()
        if time.monotonic() > deadline:
            raise Blocked(f"otp_rejected: ADP's sign-in sent an OTP to {email}")
        page.wait_for_timeout(SETTLE_MS)


def _create_profile(page: Page, details: SignUpDetails) -> None:
    assert details.name and details.phone  # in `asks`: the core gives them
    first, _, last = details.name.rpartition(" ")
    phone = page.get_by_placeholder("Enter phone number")
    phone.press_sequentially(details.phone, delay=KEY_DELAY_MS)  # E.164 picks the country
    page.get_by_label("First Name").fill(first or last)
    page.get_by_label("Last Name").fill(last)
    page.get_by_role("checkbox").first.check(force=True)  # the employer's privacy policy
    page.get_by_role("button", name="Continue").first.click()


def _country(questions: list[dict[str, Any]]) -> str | None:
    """The form's country, whose states and address labels it shows: the default of the
    question the state depends on."""
    return next(
        (q.get("answerDefaultValue") for q in questions if "state" in (q.get("dependents") or [])),
        None,
    )


def _status(application: dict[str, Any]) -> str:
    return (application.get("applicationStatusCode") or {}).get("codeValue") or "with no status"


def _labels(section: dict[str, Any]) -> dict[str, str]:
    return {x["code"]: x["value"] for x in section.get("sectionLabels") or []}


def _text(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def _any(ways: list[Condition | None]) -> Condition:
    conditions = [w for w in ways if w is not None]
    return conditions[0] if len(conditions) == 1 else AnyOf(conditions)


def _statement(section: Section) -> bool:
    """A section that only asks to agree to what it says: one Yes or No box."""
    return [f.options for f in section.fields] == [YES_NO] and section.fields[0].required


def _shown_by_dependencies(
    section: Section,
    questions: list[dict[str, Any]],
    fields: list[Field],
    codes: dict[str, dict[str, str]],
) -> list[Field]:
    """A question that shows only on another's answer, from that one's `dependencies`."""
    when: dict[str, list[Condition | None]] = {}
    for q in questions:
        parent = f"{section.component}.{q['questionID']}"
        labels = codes.get(parent, {})
        for dependency in q.get("dependencies") or []:
            for attribute in dependency.get("attributes") or []:
                answer = labels.get(attribute["answerID"], attribute["answerID"])
                child = f"{section.component}.{attribute['questionID']}"
                when.setdefault(child, []).append(Is(parent, [answer]))
    return [replace(f, shown_when=_any(when[f.id])) if f.id in when else f for f in fields]


def _required_by_answers(
    section: Section,
    questions: list[dict[str, Any]],
    fields: list[Field],
    codes: dict[str, dict[str, str]],
) -> list[Field]:
    """A question required only on another's answer, from that one's validation rules."""
    when: dict[str, Condition] = {}
    for q in questions:
        parent = f"{section.component}.{q['questionID']}"
        rules = (q.get("validationRules") or {}).get("conditionalRequiredValidations") or []
        for rule in rules:
            values = [codes.get(parent, {}).get(v, v) for v in rule.get("drivingValues") or []]
            for child in rule.get("requiredFields") or []:
                when[f"{section.component}.{child}"] = Is(parent, values)
    return [replace(f, required_when=when[f.id]) if f.id in when else f for f in fields]


def _to_page(page: Page, section: Section, ids: set[str]) -> None:
    """Makes sure the open page is the one expected before its Next: a form reopened on a
    later page, or on its review, whose Next would send, goes back with Previous."""
    if not ids or section.component.startswith("prescreen"):
        return
    with suppress(Timeout):  # Next shows before the page's questions
        page.locator("adp-form-group").first.wait_for(timeout=SECTION_TIMEOUT_MS)
    for _ in range(len(section.pages) + 1):
        if ids & set(_groups(page)):
            return
        back = page.get_by_role("button", name="Previous")
        if not back.count():
            break
        shown = _groups(page)
        back.click()
        deadline = time.monotonic() + SECTION_TIMEOUT_MS / 1000
        while _groups(page) == shown and time.monotonic() < deadline:
            page.wait_for_timeout(SETTLE_MS)
    raise RuntimeError(f"{section.title} shows another page than the one to fill")


def _groups(page: Page) -> list[str]:
    return page.locator("adp-form-group").evaluate_all("es => es.map(e => e.dataset.name)")


_SHOWN = """es => es
  .filter(e => ![...e.classList].some(c => /van-type-(instructions|header|sdf-date-time)/.test(c)))
  .map(e => [e.dataset.name, e.querySelector('label')?.innerText.trim()])"""


def _unlisted(page: Page, section: Section) -> str:
    """The questions the open page shows that the read did not list, by label."""
    if section.component.startswith("prescreen"):
        return ""
    listed = {f.id.removeprefix(f"{section.component}.") for f in section.fields}
    shown = page.locator("adp-form-group").evaluate_all(_SHOWN)
    return ", ".join(repr(label or name) for name, label in shown if name not in listed)


def _on_section(page: Page, section: Section) -> None:
    try:
        page.wait_for_url(
            lambda url: f"workflowComponent={section.component}" in url,
            timeout=SECTION_TIMEOUT_MS,
            wait_until="commit",
        )
    except Timeout:
        raise RuntimeError(f"the workflow is not on {section.title}: {page.url}") from None
    page.get_by_role("button", name="Next").wait_for(timeout=SECTION_TIMEOUT_MS)


def _box(page: Page, section: Section, f: Field) -> Locator:
    question = f.id.removeprefix(f"{section.component}.")
    if section.component.startswith("prescreen"):
        labelled = f'[aria-labelledby="{question}-error"]:not(sdf-radio-button)'
        return page.locator(f"#{question}, {labelled}").first
    return page.locator(f'adp-form-group[data-name="{question}"]').first


def _set(page: Page, box: Locator, f: Field, answer: str, codes: dict[str, str]) -> None:
    by_label = {v: k for k, v in codes.items()}
    if f.type is FieldType.FILE:
        _upload(page, box, f, answer)
        return
    if box.locator("dynamic-form-e-signature").count():
        box.locator('sdf-radio-button[value="ACCEPT"]').click()
        box.locator("sdf-input input").fill(answer)
        return
    radios = box.locator("sdf-radio-button")
    if radios.count():
        code = by_label.get(answer, {"Yes": "true", "No": "false"}.get(answer, answer))
        radio = box.locator(f'sdf-radio-button[value="{code}"]')
        if not radio.count():
            raise NotTaken(f"{f.id} has no option {answer!r}")
        radio.click()
        return
    if box.locator("sdf-checkbox").count():
        boxed = box.locator("sdf-checkbox")
        if (boxed.get_attribute("aria-checked") == "true") != (answer == "Yes"):
            boxed.click()
        return
    if box.locator("sdf-phone-number-input").count():  # its country is a select of its own
        target = box.locator("sdf-phone-number-input input").first
        target.fill("")
        target.press_sequentially(answer, delay=KEY_DELAY_MS)  # E.164 picks the country
        return
    if select := _select(box):
        _pick(page, select, f, answer)
        return
    target = box.locator("textarea, input").first
    if not target.count():
        raise NotTaken(f"{f.id} shows no input")
    target.fill(answer)


def _select(box: Locator) -> Locator | None:
    select = box if _tag(box) == "SDF-SELECT-SIMPLE" else box.locator("sdf-select-simple")
    return select.first if select.count() else None


def _tag(box: Locator) -> str:
    return box.evaluate("e => e.tagName")


def _pick(page: Page, select: Locator, f: Field, answer: str) -> None:
    """Opens the list and picks the option that matches; a long list shows its first options
    only, and its filter box narrows it to the answer."""
    select.locator("[part=frame]").first.click()
    items = page.locator("sdf-select-item:visible")  # the open list's, in a floating pane
    narrowed = False
    deadline = time.monotonic() + OPTION_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        for item in items.all():
            text = (item.get_attribute("aria-label") or item.inner_text()).strip()
            if text and matching.matches(f, answer, text):
                item.click()
                return
        search = select.locator("input#select-filter-input")
        if not narrowed and search.count():
            search.press_sequentially(answer, delay=KEY_DELAY_MS)
            narrowed = True
        page.wait_for_timeout(SETTLE_MS)
    page.keyboard.press("Escape")
    raise NotTaken(f"{f.id} has no option {answer!r}")


def _upload(page: Page, box: Locator, f: Field, answer: str) -> None:
    """The upload button opens a pane with a file input and Save; the question then shows the
    file's name."""
    box.locator("sdf-button[icon=action-upload]").click()
    page.locator("input[type=file]").set_input_files(answer)
    page.locator("#accept-button").click()
    name = answer.replace("\\", "/").rsplit("/", 1)[-1]
    try:
        box.get_by_text(name).wait_for(timeout=UPLOAD_TIMEOUT_MS)
    except Timeout:
        raise NotTaken(f"{f.id} did not take {name}: {_errors(page)}") from None


def _value(box: Locator, f: Field, codes: dict[str, str]) -> Answer | None:
    if f.type is FieldType.FILE:  # the upload's text, its buttons icons alone: the file's name
        lines = box.locator("dynamic-form-sdf-upload").inner_text().splitlines()
        return next((t.strip() for t in lines if t.strip()), None)
    if box.locator("dynamic-form-e-signature").count():
        accepted = box.locator('sdf-radio-button[value="ACCEPT"][aria-checked="true"]').count()
        return box.locator("sdf-input input").input_value() if accepted else None
    checked = box.locator('sdf-radio-button[aria-checked="true"]')
    if box.locator("sdf-radio-button").count():
        code = checked.first.get_attribute("value") if checked.count() else None
        return codes.get(code or "", code)
    if box.locator("sdf-checkbox").count():
        return (
            "Yes" if box.locator("sdf-checkbox").get_attribute("aria-checked") == "true" else "No"
        )
    if box.locator("sdf-phone-number-input").count():
        digits = re.sub(r"\D", "", box.locator("sdf-phone-number-input input").first.input_value())
        return f"+{digits}" if digits else None
    if select := _select(box):
        return select.evaluate(
            "e => (e.shadowRoot ?? e).querySelector('#selected-label')?.textContent.trim() || null"
        )
    target = box.locator("textarea, input").first
    return target.input_value() if target.count() else None


def _errors(page: Page) -> str:
    """Each question's error, from the text kept for screen readers."""
    said = page.locator("rm-sr-field-error, sdf-alert-inline").all_text_contents()
    return "; ".join(dict.fromkeys(t.strip() for t in said if t.strip())) or "no error shown"
