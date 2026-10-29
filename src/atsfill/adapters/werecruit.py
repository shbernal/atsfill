"""WeRecruit (`careers.werecruit.io/<lang>/<company>/offres/<slug>-<id>`), as
docs/adapters/werecruit.md observes.

The apply form is on the posting page, rendered on the server: `request_read` fetches it over
HTTP, `browser_read` takes it from the browser, and both list the fields with one parser. The
documents go with the form's one multipart post, back to the posting's URL.
"""

import re
import urllib.request
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import urlsplit

from atsfill.core import html
from atsfill.core.adapter import Gap, Unsupported
from atsfill.core.browser import HTTP_TIMEOUT_S, Page, Timeout
from atsfill.core.results import Answer, Field, FieldType, SideEffect

HOST = "careers.werecruit.io"
# A posting on WeRecruit's host names its company; on an employer's own domain it does not.
HOSTED = re.compile(r"^/(?:fr|en)/([\w-]+)/(?:offres|offers)/(?:[\w-]*-)?([0-9a-f]{6})/?$")
OWN_DOMAIN = re.compile(r"^/(?:fr|en)/(?:offres|offers)/(?:[\w-]*-)?([0-9a-f]{6})/?$")
FORM = "form#form"
PORTAL = f"{FORM} input[name=isEmployerPortal]"  # WeRecruit's form, whatever the domain
FORM_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
KINDS = {
    "text": FieldType.TEXT,
    "email": FieldType.TEXT,
    "tel": FieldType.PHONE,
    "textarea": FieldType.TEXT,
    "select": FieldType.CHOICE,
    "radio": FieldType.CHOICE,
    "file": FieldType.FILE,
}

# What each control holds, by name: a value, a ticked radio's id, a document's file name.
_READ_BACK = """(form, names) => {
  const out = {};
  for (const name of names) {
    const els = [...form.querySelectorAll(`[name="${name}"]`)];
    const el = els[0];
    if (!el) continue;
    if (el.type === "radio") out[name] = els.find(e => e.checked)?.id ?? "";
    else if (el.type === "file") out[name] = el.files[0]?.name ?? "";
    else out[name] = el.value ?? "";
  }
  return out;
}"""


@dataclass
class _Control:
    field: Field
    name: str  # the control's own name, which may carry spaces (` photo`)
    kind: str
    options: dict[str, str]  # option text by value, or by radio id


class WeRecruit:
    name = "werecruit"
    known_gaps: dict[str, Gap] = {}
    side_effects = [SideEffect("submit", "employer", "the application reaches the employer")]

    def __init__(self) -> None:
        self._controls: list[_Control] = []

    def detect(self, url: str, page: Page | None) -> bool:
        """A posting on WeRecruit's host, or one on an employer's domain that serves its form."""
        parts = urlsplit(url)
        if parts.hostname == HOST:
            return HOSTED.match(parts.path) is not None
        if not OWN_DOMAIN.match(parts.path):
            return False
        return page is not None and page.locator(PORTAL).count() > 0

    def posting(self, url: str) -> str:
        """The company and the id: the slug and the language do not change the posting."""
        parts = urlsplit(url)
        if parts.hostname == HOST and (m := HOSTED.match(parts.path)):
            return f"https://{HOST}/fr/{m[1]}/offres/{m[2]}"
        if m := OWN_DOMAIN.match(parts.path):
            return f"https://{parts.hostname}/fr/offres/{m[1]}"
        return url

    def request_read(self, url: str) -> list[Field] | None:
        if not self.detect(url, None):
            return None
        try:
            controls = _parse(_get(url.split("?")[0]))
        except HTTPError, OSError, ValueError:
            return None
        return [c.field for c in controls] if controls is not None else None

    def browser_read(self, page: Page) -> list[Field]:
        try:
            page.locator(FORM).wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        controls = _parse(page.content())
        assert controls is not None
        self._controls = controls
        return [c.field for c in controls]

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        form = page.locator(FORM)
        for c in self._controls:
            if c.field.id not in answers or c.field.unfillable:
                continue
            value = _one(answers[c.field.id])
            control = form.locator(f'[name="{c.name}"]')
            match c.kind:
                case "select":
                    by_text = {t: v for v, t in c.options.items()}
                    control.select_option(value=by_text.get(value, value))
                case "radio":
                    radio = next((i for i, t in c.options.items() if t == value), value)
                    form.locator(f'label[for="{radio}"]').click()
                case "file":
                    control.set_input_files(value)
                case _:
                    control.fill(value)

    def read_back(self, page: Page) -> dict[str, Answer]:
        shown = [c for c in self._controls if not c.field.unfillable]
        values = page.locator(FORM).evaluate(_READ_BACK, [c.name for c in shown])
        out: dict[str, Answer] = {}
        for c in shown:
            value = values.get(c.name)
            if c.kind in ("select", "radio"):
                value = c.options.get(value, value)
            if value not in (None, ""):
                out[c.field.id] = value
        return out

    def submit(self, page: Page) -> str | None:
        """Clicks Je postule; the evidence is the URL and status of the form's post. The page
        validates first: no post, and its errors are the failure."""
        action = page.locator(FORM).get_attribute("action") or page.url
        try:
            with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.split("?")[0] == action,
                timeout=SUBMIT_TIMEOUT_MS,
            ) as posted:
                page.locator("#submit-button").click()
        except Timeout:
            raise RuntimeError(f"the form was not posted: {_errors(page)}") from None
        if posted.value.status >= 400:
            raise RuntimeError(f"the post answered {posted.value.status}")
        return f"{posted.value.url} {posted.value.status}"


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "atsfill"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return response.read().decode()


def _parse(source: str) -> list[_Control] | None:
    """Each named control of the apply form, a radio group once; None without the form."""
    nodes = html.parse(source).walk()
    form = next((n for n in nodes if n.tag == "form" and n.attrs.get("id") == "form"), None)
    if form is None:
        return None
    labels = {n.attrs["for"]: n for n in form.walk() if n.tag == "label" and "for" in n.attrs}
    out: list[_Control] = []
    seen: set[str] = set()
    for node in form.walk():
        name = node.attrs.get("name")
        if node.tag not in ("input", "select", "textarea") or not name or name in seen:
            continue
        kind = node.attrs.get("type", "text") if node.tag == "input" else node.tag
        if kind in ("hidden", "submit", "button"):
            continue
        seen.add(name)
        group = node.closest(lambda n: "form-group" in n.classes())
        own = group.find(lambda n: n.tag == "label") if group else None
        label = _label(own) if own is not None else name.strip()
        required = "required" in node.attrs
        fid = name.strip()
        if kind not in KINDS:
            reason = f"unhandled_rendering: {fid} is a {kind}"
            field = Field(fid, label, FieldType.TEXT, required, unfillable=reason)
            out.append(_Control(field, name, kind, {}))
            continue
        options: dict[str, str] = {}
        if kind == "select":
            options = {
                o.attrs["value"]: " ".join(o.text().split())
                for o in node.walk()
                if o.tag == "option" and o.attrs.get("value")
            }
        elif kind == "radio":
            options = {
                r.attrs["id"]: _label(labels[r.attrs["id"]])
                for r in form.walk()
                if r.tag == "input" and r.attrs.get("name") == name and r.attrs.get("id") in labels
            }
        accept = node.attrs.get("accept", "").split(",") if kind == "file" else []
        field = Field(
            fid,
            label,
            KINDS[kind],
            required,
            list(options.values()) or None,
            accept=[a.strip() for a in accept if a.strip()] or None,
        )
        out.append(_Control(field, name, kind, options))
    return out


def _label(node: html.Node) -> str:
    """A label's text, without its required marker."""

    def text(n: html.Node | str) -> str:
        if isinstance(n, str):
            return n
        return "" if "required" in n.classes() else "".join(text(c) for c in n.children)

    return " ".join(text(node).split())


def _one(answer: Answer) -> str:
    return answer if isinstance(answer, str) else answer[0]


def _errors(page: Page) -> str:
    shown = page.locator(f"{FORM} .invalid-feedback:visible, #errorMessageDiv:visible")
    return " ".join(" ".join(shown.all_inner_texts()).split())[:200] or "no error shown"
