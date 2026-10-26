"""HRMDirect, now ClearCompany (`<org>.hrmdirect.com/employment/job-opening.php?req=…`, which
redirects to `apply.clearcompany.com/<org>?jobId=<job>`, whose Apply opens
`<org>.clearcompany.com/careers/jobs/<job>/apply`), as docs/adapters/hrmdirect.md observes.

Applying needs an account, one per employer (the org): its sign-up is the apply page's first
step, a name and an email, and sets no password. The form is a wizard of pages, each saved to
the application as it is left, only the current one in the DOM: the read walks the pages that
save with what they hold, and the read-back is each page's values, read before it is saved.
"""

import re
import urllib.parse
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
from atsfill.core.results import Answer, Field, FieldType, SideEffect

OLD_HOST = re.compile(r"^([a-z0-9-]+)\.hrmdirect\.com$")
ORG_HOST = re.compile(r"^([a-z0-9-]+)\.clearcompany\.com$")
SITE_HOST = "apply.clearcompany.com"
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
APPLY_PATH = re.compile(rf"^/careers/jobs/({UUID})(?:/apply(?:/{UUID})?)?/?$")
JOB_ID = re.compile(rf"^{UUID}$")
FORM_TIMEOUT_MS = 30_000
PAGE_TIMEOUT_MS = 30_000
UPLOAD_TIMEOUT_MS = 60_000
SIGN_UP_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
SETTLE_MS = 1_500
MAX_PAGES = 10
IDENTITY = "#personal-info-form"
HAVE_ACCOUNT = "button.have-account-button"
LOGIN = "a.authenticate-button"
RECAPTCHA = f"{IDENTITY} iframe[title=reCAPTCHA], {IDENTITY} .g-recaptcha"
EMAIL_SIGN_IN = "text=Sign in with username and password instead"
PAGE_FORM = ".form-page-holder form.section-form"
ACTIVE_PAGE = ".form-progress-holder li.active"
NEXT = ".form-page-holder ~ * button.submit-button, button.submit-button:visible"
ERRORS = ".has-error:visible"
CLOSE_DATEPICKERS = (
    "() => document.querySelectorAll('.datepicker.dropdown-menu')"
    ".forEach(d => { d.style.display = 'none'; })"
)
TOKEN = re.compile(r"Token\$\$:\$\$([^$]+)")
API_HEADERS = {"API-Realm": "CWEB", "API-AppType": "careers"}

# Each field of the current page: id, kind, label, required, options, max length, value.
_FIELDS = """form => [...form.querySelectorAll('.form-field')].flatMap(f => {
  const title = f.querySelector('.field-title');
  if (!title || f.closest('.child-field-holder:empty')) return [];
  const label = [...title.childNodes].filter(n => !(n.classList?.contains('required-indicator')))
    .map(n => n.textContent).join(' ').replace(/\\s+/g, ' ').trim();
  const required = !!title.querySelector('.required-indicator');
  const visible = !!f.offsetParent;
  const qa = [...f.classList].find(c => c.startsWith('qa-')) || '';
  const base = {label, required, visible, qa, options: null, max: null, value: null};
  if (qa === 'qa-resume-upload') {
    const kind = [...f.classList].find(c => c !== 'form-field' && c !== qa);
    const names = [...f.querySelectorAll('.file-list tr td:first-child')]
      .map(t => t.textContent.trim()).filter(n => n && !n.includes('Uploading'));
    const upload = !!f.querySelector('input[type=file]');
    return [{...base, id: 'file.' + kind, kind: upload ? 'file' : 'linkedin',
             value: names.at(-1) || null}];
  }
  const boxes = [...f.querySelectorAll('input.form-radio, input.form-checkbox')];
  if (boxes.length) {
    const own = boxes.filter(b => b.closest('.form-field') === f);
    const text = b => b.closest('label').textContent.replace(/\\s+/g, ' ').trim();
    const checked = own.filter(b => b.checked).map(text);
    return [{...base, id: own[0].dataset.field, kind: own[0].type,
             options: own.map(text), value: checked}];
  }
  const control = f.querySelector('input:not([type=hidden]), select, textarea');
  if (!control || control.closest('.form-field') !== f) return [{...base, id: '', kind: 'none'}];
  let kind = control.tagName.toLowerCase();
  if (kind === 'input') kind = control.classList.contains('date-input') ? 'date' : control.type;
  const options = kind === 'select'
    ? [...control.options].filter(o => o.value).map(o => o.textContent.trim()) : null;
  const value = kind === 'select'
    ? (control.value ? control.selectedOptions[0].textContent.trim() : null) : control.value;
  const max = parseInt(control.dataset.max) || (control.maxLength > 0 ? control.maxLength : null);
  return [{...base, id: control.id, kind, options, value, max}];
})"""


class HRMDirect:
    name = "hrmdirect"
    per_employer = True
    asks = {"name"}
    known_gaps = {"screening_first": Gap.FLOW, "sign_up_phone": Gap.FLOW}
    side_effects = [
        SideEffect(
            "sign_up", "employer", "the account is the employer's candidate, emailed a link"
        ),
        SideEffect("fill", "hiring_system", "each page, the resume too, is saved when left"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._jobs: dict[str, str] = {}  # by org, the last posting's job
        self._fields: dict[str, Field] = {}
        self._kinds: dict[str, str] = {}
        self._values: dict[str, Answer] = {}
        self._apply = ""

    def detect(self, url: str, page: Page | None) -> bool:
        return _job(url) is not None

    def posting(self, url: str) -> str:
        """The job's id, the same on the career site and its apply page's host; an HRMDirect
        URL that has not redirected yet keeps its requisition."""
        job = _job(url)
        if job is None:
            return url
        org, key = job
        if JOB_ID.match(key):
            return f"https://{SITE_HOST}/jobs/{key.lower()}"
        return f"https://{org}.hrmdirect.com/employment/job-opening.php?req={key}"

    def account_scope(self, url: str) -> str | None:
        """The career site's name, or the apply host's org for an apply page."""
        job = _job(url)
        if job is None:
            return None
        if JOB_ID.match(job[1]):
            self._jobs[job[0]] = job[1].lower()
        return job[0]

    def login_url(self, scope: str | None) -> str:
        """The career site's page of the last posting seen for the scope, else its job list."""
        if scope in self._jobs:
            return f"https://{SITE_HOST}/{scope}?jobId={self._jobs[scope]}"
        return f"https://{SITE_HOST}/{scope}"

    def signed_in(self, request: Request, scope: str | None) -> bool:
        """An org's `users/auth/authenticate` answers 200 for the token its cookie holds; the
        cookie is the apply host's, which can be another org than the career site's."""
        for cookie in request.storage_state()["cookies"]:
            org = ORG_HOST.match(cookie["domain"].lstrip("."))
            token = TOKEN.search(urllib.parse.unquote(cookie["value"]))
            if cookie["name"] != "HrmBase-careers" or not org or not token:
                continue
            headers = API_HEADERS | {"API-ShortName": org[1], "API-Token": token[1]}
            response = request.get(
                f"https://{org[1]}.clearcompany.com/api/v1/users/auth/authenticate",
                headers=headers,
            )
            if response.ok:
                return True
        return False

    def sign_in(self, page: Page, email: str, password: str) -> None:
        """Signs in through the header's login, whose modal takes a username and a password;
        an account gets a password through the link its sign-up emailed."""
        _to_apply(page)
        if page.locator(IDENTITY).is_visible():
            page.locator(HAVE_ACCOUNT).click()
            page.locator(EMAIL_SIGN_IN).click()
            page.locator("input.login-username:visible").fill(email)
            page.locator("input.login-password:visible").fill(password)
            page.get_by_role("button", name="Continue").locator("visible=true").click()
        else:
            page.locator(LOGIN).click()
            modal = page.locator(".modal:visible").filter(has=page.locator("input[type=password]"))
            modal.locator("input:not([type=password]):visible").first.fill(email)
            modal.locator("input[type=password]").fill(password)
            modal.locator("button.btn-primary").click()
        try:
            page.locator(f"{PAGE_FORM}, {LOGIN}:text-is('logout')").first.wait_for(
                timeout=SIGN_UP_TIMEOUT_MS
            )
        except Timeout:
            raise Refused("wrong_password") from None

    def sign_up(self, page: Page, email: str, password: str, details: SignUpDetails) -> None:
        """Fills the apply page's first step, which creates the account signed in and sets no
        password: the email that follows holds a link to set one, `blocked: email_link`."""
        _to_apply(page)
        if not page.locator(IDENTITY).is_visible():
            raise Unsupported(
                known_gap(self, "screening_first", "the form's first page comes before sign-up")
            )
        phone = page.locator(f"{IDENTITY} .form-group:has(input.mobile-number)")
        if phone.count() and not phone.locator(".common-option-label").count():
            raise Unsupported(
                known_gap(self, "sign_up_phone", "the sign-up step requires a mobile phone")
            )
        if page.locator(RECAPTCHA).count():
            raise Blocked("captcha: a reCAPTCHA box keeps the sign-up's Continue disabled")
        first, _, last = (details.name or "").strip().partition(" ")
        form = page.locator(IDENTITY)
        form.locator("input.first-name").fill(first)
        form.locator("input.last-name").fill(last or first)
        form.locator("input.email").fill(email)
        form.locator("input.email-confirm").fill(email)
        for group in form.locator(".radio-group:has(input[required])").all():
            group.locator("label:has(input[value=false])").click()  # declines, the least given
        form.locator("button.submit-button").click()
        try:
            moved = page.locator(f"{PAGE_FORM}, {IDENTITY} .has-error, .alert-danger:visible")
            moved.or_(page.locator(EMAIL_SIGN_IN)).first.wait_for(timeout=SIGN_UP_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("unhandled_rendering: the sign-up step did not move") from None
        if page.locator(EMAIL_SIGN_IN).is_visible():  # the email has an account here
            raise Refused("email_in_use")
        if not page.locator(PAGE_FORM).count():
            message = " ".join(page.locator(".alert-danger:visible, .has-error").all_inner_texts())
            raise Refused(f"email_domain_rejected: {message.strip()[:200]}")
        raise Blocked("email_link: set the account's password through the emailed link")

    def browser_read(self, page: Page) -> list[Field]:
        """Walks the wizard from its first page as far as its pages save with what they hold,
        reading each page it reaches."""
        self._open(page)
        self._fields = {}
        for _ in range(MAX_PAGES):
            for f in self._page(page):
                self._fields.setdefault(f.id, f)
            if not self._continue(page):
                break
        return list(self._fields.values())

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        """Fills each page from the first and saves it, up to the last; a page showing a
        required field the read did not list is refused, the pages before it saved."""
        self._open(page)
        self._values = {}
        for _ in range(MAX_PAGES):
            shown = self._page(page)
            for f in shown:
                if f.id in answers and f.id in self._kinds:
                    _fill(page, f, self._kinds[f.id], answers[f.id])
            values = self._read(page)
            for f in shown:  # a country renders its state list again, emptying the state
                answer = answers.get(f.id)
                if self._kinds.get(f.id) == "select" and answer and values.get(f.id) != answer:
                    control = page.locator(PAGE_FORM).locator(f"[id='{f.id}']")
                    tag = control.evaluate("el => el.tagName") if control.count() else ""
                    _fill(page, f, "select" if tag == "SELECT" else "text", answer)
            unread = [f.id for f in shown if f.required and f.id not in self._fields]
            if unread:
                raise NotTaken(f"the page shows fields the read did not list: {unread}; read again")
            self._values |= self._read(page)
            if _last(page) or not self._continue(page):
                return

    def read_back(self, page: Page) -> dict[str, Answer]:
        return self._values | self._read(page)

    def submit(self, page: Page) -> str | None:
        """The last page's Submit; atsfill expects it to post the application. No send was
        made."""
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/api/" in r.url and "submit" in r.url,
            timeout=SUBMIT_TIMEOUT_MS,
        ) as posted:
            page.locator(NEXT).first.click()
        response = posted.value
        return f"{response.url} {response.status}"

    def _open(self, page: Page) -> None:
        """The wizard's first page, loaded again; `posting_gone` for a page with no
        application."""
        parts = urlsplit(page.url)
        if not self._apply or ORG_HOST.match(parts.hostname or "") is None:
            self._apply = _apply_link(page)
        elif APPLY_PATH.match(parts.path):
            org, job = _job(page.url) or ("", "")
            self._apply = _apply_url(org, job)
        page.goto(self._apply)
        try:
            page.locator(f"{PAGE_FORM}, {IDENTITY}").first.wait_for(timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        if page.locator(IDENTITY).is_visible():
            raise Unsupported("unhandled_rendering: the apply page asks to sign up again")
        first = page.locator(".form-progress-holder li.page-0")
        if first.count() and "active" not in (first.get_attribute("class") or ""):
            first.click()
            page.wait_for_timeout(SETTLE_MS)

    def _page(self, page: Page) -> list[Field]:
        """The current page's fields; each kept with its kind for the fill."""
        page.wait_for_timeout(SETTLE_MS)
        raw: list[dict[str, Any]] = page.locator(PAGE_FORM).evaluate(_FIELDS)
        out = []
        for r in raw:
            if not r["visible"] and r["kind"] != "file":
                continue
            field = _field(r)
            if not field.unfillable:
                self._kinds[field.id] = r["kind"]
            out.append(field)
        return out

    def _read(self, page: Page) -> dict[str, Answer]:
        out: dict[str, Answer] = {}
        for r in page.locator(PAGE_FORM).evaluate(_FIELDS):
            value = r["value"]
            if r["id"] not in self._fields or value in (None, ""):
                continue
            if value == [] and not (r["kind"] == "checkbox" and len(r["options"]) == 1):
                continue
            if r["kind"] == "date" and re.match(r"^\d\d/\d\d/\d{4}$", value):
                month, day, year = value.split("/")
                value = f"{year}-{month}-{day}"
            if r["kind"] == "radio":
                value = value[0]
            elif r["kind"] == "checkbox" and len(r["options"]) == 1:
                value = "Yes" if value else "No"
            out[r["id"]] = value
        return out

    def _continue(self, page: Page) -> bool:
        """Presses Continue; whether the next page showed, not errors."""
        if _last(page):
            return False
        before = _active(page)
        page.locator(NEXT).first.click()
        try:
            page.wait_for_function(
                "([sel, before]) => { const a = document.querySelector(sel);"
                " return (a && a.textContent !== before)"
                " || !!document.querySelector('.has-error'); }",
                arg=[ACTIVE_PAGE, before],
                timeout=PAGE_TIMEOUT_MS,
            )
        except Timeout:
            return False
        return _active(page) != before


def _job(url: str) -> tuple[str, str] | None:
    """The org and the job: the job's id on ClearCompany, the requisition on HRMDirect."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    query = parse_qs(parts.query)
    if (old := OLD_HOST.match(host)) and parts.path == "/employment/job-opening.php":
        req = query.get("req", [""])[0]
        return (old[1], req) if req.isdigit() else None
    if host == SITE_HOST:
        org = parts.path.strip("/").split("/")[0]
        job = query.get("jobId", [""])[0]
        return (org.lower(), job) if org and JOB_ID.match(job) else None
    org, path = ORG_HOST.match(host), APPLY_PATH.match(parts.path)
    return (org[1], path[1]) if org and path else None


def _apply_url(org: str, job: str) -> str:
    return f"https://{org}.clearcompany.com/careers/jobs/{job.lower()}/apply"


def _apply_link(page: Page) -> str:
    """The apply page of the job page, or of an apply page already open; its host is the
    org the application goes to, which the career site's Apply link names."""
    if ORG_HOST.match(urlsplit(page.url).hostname or "") and "/careers/jobs/" in page.url:
        org, job = _job(page.url) or ("", "")
        return _apply_url(org, job)
    link = page.locator("a[href*='/careers/jobs/'][href*='/apply']").first
    try:
        link.wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
    except Timeout:
        raise Unsupported("posting_gone: no Apply link on the page") from None
    return link.get_attribute("href") or ""


def _to_apply(page: Page) -> None:
    """The apply page's first step, from the job's page or the career site's first job."""
    if "jobId=" not in page.url and "/careers/jobs/" not in page.url:
        jobs = page.locator("a[href*='jobId=']").first
        try:
            jobs.wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("unhandled_rendering: no job on the career site") from None
        page.goto(jobs.get_attribute("href") or "")
    page.goto(_apply_link(page))
    page.locator(f"{IDENTITY}, {PAGE_FORM}").first.wait_for(timeout=FORM_TIMEOUT_MS)


def _field(r: dict[str, Any]) -> Field:
    kinds = {
        "text": FieldType.TEXT,
        "email": FieldType.TEXT,
        "tel": FieldType.TEXT,
        "textarea": FieldType.TEXT,
        "select": FieldType.CHOICE,
        "radio": FieldType.CHOICE,
        "checkbox": FieldType.MULTI_CHOICE,
        "date": FieldType.DATE,
        "file": FieldType.FILE,
    }
    label, required = r["label"] or r["id"], r["required"]
    if r["kind"] == "linkedin":
        reason = "out_of_scope: a LinkedIn profile is imported by signing in to LinkedIn"
        return Field(r["id"], label, FieldType.FILE, False, unfillable=reason)
    if r["kind"] not in kinds or not r["id"]:
        reason = f"unhandled_rendering: {r['id'] or label} is a {r['qa'] or r['kind']}"
        return Field(r["id"] or label, label, FieldType.TEXT, required, unfillable=reason)
    kind = kinds[r["kind"]]
    if kind is FieldType.MULTI_CHOICE and len(r["options"]) == 1:
        kind = FieldType.CHOICE
        return Field(r["id"], label, kind, required, ["Yes", "No"])
    return Field(
        r["id"],
        label,
        kind,
        required,
        r["options"],
        r["max"] if kind is FieldType.TEXT else None,
    )


def _last(page: Page) -> bool:
    """Whether the page's primary button is Submit, not Continue."""
    button = page.locator(NEXT).first
    key = button.get_attribute("data-localized-key") or "" if button.count() else ""
    return bool(button.count()) and not key.endswith("continue")


def _active(page: Page) -> str:
    active = page.locator(ACTIVE_PAGE)
    return active.first.inner_text() if active.count() else ""


def _fill(page: Page, field: Field, kind: str, answer: Answer) -> None:
    values = [answer] if isinstance(answer, str) else list(answer)
    form = page.locator(PAGE_FORM)
    if kind in ("radio", "checkbox"):
        boxes = form.locator(f"input[data-field='{field.id}']")
        if kind == "checkbox" and boxes.count() == 1:  # one box, answered Yes or No
            box = boxes.first
            if box.is_checked() != (values[0] == "Yes"):
                box.locator("xpath=ancestor::label[1]").click()
            return
        for value in values:
            box = _option(boxes, value)
            if not box.is_checked():
                box.locator("xpath=ancestor::label[1]").click()
        return
    if kind == "file":
        _upload(form, field.id.removeprefix("file."), values[0])
        return
    control = form.locator(f"[id='{field.id}']")
    if kind == "select":
        options = field.options or []
        if values[0] not in options:
            raise NotTaken(f"{field.id}: no option {values[0]!r}")
        control.select_option(label=values[0])
    elif kind == "date":
        year, month, day = values[0].split("-")
        control.fill(f"{month}/{day}/{year}")
        control.press("Tab")
        page.evaluate(CLOSE_DATEPICKERS)  # an open one covers the fields below it
    else:
        control.fill(values[0])
    control.dispatch_event("change")
    control.blur()


def _option(boxes: Locator, value: str) -> Locator:
    for i in range(boxes.count()):
        box = boxes.nth(i)
        text = " ".join(box.locator("xpath=ancestor::label[1]").inner_text().split())
        if text == value:
            return box
    raise NotTaken(f"no option {value!r}")


def _upload(form: Locator, kind: str, path: str) -> None:
    """Attaches the file and waits for its row in the field's file list."""
    holder = form.locator(f".qa-resume-upload.{kind}")
    holder.locator("input[type=file]").set_input_files(path)
    name = path.rsplit("/", 1)[-1]
    holder.page.wait_for_function(
        "([el, name]) => [...el.querySelectorAll('.file-list tr td:first-child')]"
        ".some(t => t.textContent.trim() === name)"
        " && !el.querySelector('.file-list').textContent.includes('Uploading')",
        arg=[holder.element_handle(), name],
        timeout=UPLOAD_TIMEOUT_MS,
    )
