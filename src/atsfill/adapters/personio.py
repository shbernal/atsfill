"""Personio (`<employer>.jobs.personio.com/job/<id>`), as docs/adapters/personio.md observes.

The apply page is rendered on the server, its form whole in the HTML: every field is one native
control named by its id, labelled by a `label[for]` or its group's label, a star marking it
required. `request_read` fetches that HTML over HTTP, `browser_read` takes the page's form, and both
list the fields from it with one parser. A document is uploaded to Personio once attached.
"""

import re
import urllib.request
from contextlib import suppress
from urllib.parse import urlsplit

from atsfill.core import html
from atsfill.core.adapter import Blocked, Gap, Unsupported
from atsfill.core.browser import HTTP_TIMEOUT_S, Locator, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOST = re.compile(r"^([a-z0-9-]+)\.jobs\.personio\.(?:com|de)$")
JOB = re.compile(r"^/job/(\d+)(?:/apply)?/?$")
APPLIED = re.compile(r"/api/v1/jobs/\d+/application$")
FORM = "form:has(button[type=submit])"
SUBMIT = f"{FORM} button[type=submit]"
FORM_TIMEOUT_MS = 30_000
UPLOAD_TIMEOUT_MS = 30_000
ENABLED_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
PHONE = "phone"
# Each control the form renders, by its tag or input type, and its field type.
KINDS = {
    "text": FieldType.TEXT,
    "email": FieldType.TEXT,
    "date": FieldType.DATE,
    "select": FieldType.CHOICE,
    "file": FieldType.FILE,
}

# What each control holds: a value, a select's option value, a document's uploaded names.
_READ_BACK = """(form, ids) => {
  const out = {};
  for (const id of ids) {
    const el = form.querySelector(`[name="${id}"]`);
    if (!el) continue;
    if (el.type === "file") {
      const group = el.closest("[role=group]");
      out[id] = [...(group?.querySelectorAll("[role=listitem] span") ?? [])]
        .map(e => e.textContent.trim());
    } else out[id] = el.value;
  }
  return out;
}"""


class Personio:
    name = "personio"
    known_gaps: dict[str, Gap] = {}
    side_effects = [
        SideEffect("upload", "hiring_system", "a document is uploaded to Personio at once"),
        SideEffect("submit", "employer", "the application reaches the employer"),
    ]

    def __init__(self) -> None:
        self._options: dict[str, dict[str, str]] = {}  # a choice's option texts by value
        self._fields: list[Field] = []

    def detect(self, url: str, page: Page | None) -> bool:
        return _job(url) is not None

    def posting(self, url: str) -> str:
        job = _job(url)
        return f"https://{job[0]}.jobs.personio.com/job/{job[1]}" if job else url

    def request_read(self, url: str) -> list[Field] | None:
        if _job(url) is None:
            return None
        request = urllib.request.Request(_apply(url), headers={"User-Agent": "atsfill"})
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
                page = html.parse(response.read().decode())
        except OSError, ValueError:
            return None
        form = next((n for n in page.walk() if n.tag == "form"), None)
        return [f for f, _ in _form(form)] if form else None

    def browser_read(self, page: Page) -> list[Field]:
        if _job(page.url) and not urlsplit(page.url).path.rstrip("/").endswith("/apply"):
            page.goto(_apply(page.url))
        try:
            page.locator(SUBMIT).wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        listed = _form(html.parse(page.locator(FORM).evaluate("f => f.outerHTML")))
        self._options = {f.id: o for f, o in listed if o and not f.unfillable}
        self._fields = [f for f, _ in listed if not f.unfillable]
        return [f for f, _ in listed]

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        form = page.locator(FORM)
        for f in self._fields:
            if f.id not in answers:
                continue
            answer = answers[f.id]
            value = answer if isinstance(answer, str) else answer[0]
            control = form.locator(f"[name='{f.id}']")
            match f.type:
                case FieldType.FILE:
                    _attach(control, value)
                case FieldType.CHOICE:
                    by_text = {t: v for v, t in self._options[f.id].items()}
                    control.select_option(value=by_text.get(value, value))
                case _:
                    control.fill(value)

    def shown_fields(self, page: Page) -> list[str]:
        form = html.parse(page.locator(FORM).evaluate("f => f.outerHTML"))
        return [f.id for f, _ in _form(form)]

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = page.locator(FORM).evaluate(_READ_BACK, [f.id for f in self._fields])
        out: dict[str, Answer] = {}
        for field_id, value in values.items():
            if field_id in self._options:
                value = self._options[field_id].get(value, value)
            elif isinstance(value, list):  # a document's last upload
                value = value[-1] if value else None
            if value not in (None, ""):
                out[field_id] = value
        return out

    def submit(self, page: Page) -> str | None:
        """Waits for submit to be enabled (Turnstile), clicks it; the evidence is the answer to the
        application's POST."""
        button = page.locator(SUBMIT)
        try:
            page.wait_for_function(
                "el => !el.disabled", arg=button.element_handle(), timeout=ENABLED_TIMEOUT_MS
            )
        except Timeout:
            raise Blocked("captcha: Turnstile kept the submit disabled") from None
        with page.expect_response(
            lambda r: (
                r.request.method == "POST" and APPLIED.search(urlsplit(r.url).path) is not None
            ),
            timeout=SUBMIT_TIMEOUT_MS,
        ) as applied:
            button.click()
        response = applied.value
        if not response.ok:
            raise RuntimeError(f"apply answered {response.status}: {response.text()[:200]}")
        return f"{response.url} {response.status}"


def _job(url: str) -> tuple[str, str] | None:
    """The account and job id, from `<account>.jobs.personio.{com,de}/job/<id>[/apply]`."""
    parts = urlsplit(url)
    host, path = HOST.match(parts.hostname or ""), JOB.match(parts.path)
    return (host[1], path[1]) if host and path else None


def _apply(url: str) -> str:
    """The posting's apply page, in the language the URL asks for."""
    parts = urlsplit(url)
    job = JOB.match(parts.path)
    assert job is not None
    language = re.search(r"(?:^|&)(language=[\w-]+)", parts.query)
    query = f"?{language[1]}" if language else ""
    return f"https://{parts.hostname}/job/{job[1]}/apply{query}"


def _form(form: html.Node) -> list[tuple[Field, dict[str, str] | None]]:
    """Each named control of the form, with a choice's option texts by value."""
    nodes = form.walk()
    by_id = {n.attrs["id"]: n for n in nodes if "id" in n.attrs}
    labels = {n.attrs["for"]: n for n in nodes if n.tag == "label" and "for" in n.attrs}
    out: list[tuple[Field, dict[str, str] | None]] = []
    for node in nodes:
        name = node.attrs.get("name")
        if node.tag not in ("input", "select", "textarea") or not name:
            continue
        kind = node.attrs.get("type", "text") if node.tag == "input" else node.tag
        if kind == "hidden":
            continue
        group = node.closest(lambda n: n.attrs.get("role") == "group")
        group_label = by_id.get(group.attrs.get("aria-labelledby", "")) if group else None
        own = labels.get(node.attrs.get("id", ""))
        label = (_label(own) if own else "") or (_label(group_label) if group_label else "") or name
        required = any(n is not None and _starred(n) for n in (own, group_label))
        if kind not in KINDS or "multiple" in node.attrs and kind == "select":
            reason = f"unhandled_rendering: {name} is a {kind}"
            out.append((Field(name, label, FieldType.TEXT, required, unfillable=reason), None))
            continue
        field_type = FieldType.PHONE if name == PHONE else KINDS[kind]
        options = None
        if kind == "select":
            options = {
                o.attrs["value"]: " ".join(o.text().split())
                for o in node.walk()
                if o.tag == "option" and o.attrs.get("value")
            }
        max_length = int(node.attrs.get("maxlength", 0)) or None
        accept = (
            node.attrs["accept"].split(",") if kind == "file" and "accept" in node.attrs else None
        )
        out.append(
            (
                Field(
                    name,
                    label,
                    field_type,
                    required,
                    list(options.values()) if options else None,
                    max_length if field_type in (FieldType.TEXT, FieldType.PHONE) else None,
                    accept,
                ),
                options,
            )
        )
    return out


def _label(node: html.Node) -> str:
    """A label's text, without the spans that mark it required or optional."""
    own = (c for c in node.children if isinstance(c, str) or c.tag != "span")
    return " ".join("".join(c if isinstance(c, str) else c.text() for c in own).split())


def _starred(node: html.Node) -> bool:
    return any(
        isinstance(c, html.Node) and c.tag == "span" and c.text().strip() == "*"
        for c in node.children
    )


def _attach(control: Locator, path: str) -> None:
    """Attaches a document and waits for Personio's upload to show its name in the category."""
    name = path.rsplit("/", 1)[-1]
    control.set_input_files(path)
    shown = control.locator("xpath=ancestor::*[@role='group'][1]").locator(
        "[role=listitem] span", has_text=name
    )
    with suppress(Timeout):
        shown.first.wait_for(timeout=UPLOAD_TIMEOUT_MS)
