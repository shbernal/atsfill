"""Recruitee (`*.recruitee.com` career sites and custom domains on them), as
docs/adapters/recruitee.md observes.

The form is the career site's own page, `/o/<slug>/c/new`: every field is a named control,
`candidate.<field>` or `candidate.openQuestionAnswers.<question>.<value>`, and the name is the
field's id. Nothing reaches Recruitee before submit, so the read-back is the page.
"""

import re
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from atsfill.core import browser, html
from atsfill.core.adapter import Blocked, Gap, Unsupported, out_of_scope
from atsfill.core.browser import HTTP_TIMEOUT_S, Locator, Page, Timeout
from atsfill.core.html import Node
from atsfill.core.results import Answer, Field, FieldType, SideEffect

JOB = re.compile(r"^/o/([^/?#]+)")
CAREER_SITE = "script[src*='/js/PublicApp/'], script[src*='recruiteecdn.com']"
FORM_ID = "offer-application-form"
FORM = f"form#{FORM_ID}"
FORM_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 90_000
KEY_DELAY_MS = 20
YES_NO = ["Yes", "No"]
LETTER_FILE, LETTER = "candidate.coverLetterFile", "candidate.coverLetter"
LETTER_SWITCH_CY = "cover-letter-switch-button"
LETTER_SWITCH = f"[data-cy={LETTER_SWITCH_CY}]"
EXTENSIONS = {"image/png": [".png"], "image/jpeg": [".jpg", ".jpeg"], "application/pdf": [".pdf"]}
KINDS = {
    "text": FieldType.TEXT,
    "number": FieldType.TEXT,
    "phone": FieldType.PHONE,
    "date": FieldType.DATE,
    "file": FieldType.FILE,
    "letter": FieldType.TEXT,
    "radio": FieldType.CHOICE,
    "legal": FieldType.CHOICE,
    "checkboxes": FieldType.MULTI_CHOICE,
}

# What each field shows: a choice its checked option's text, a file its attached name.
_READ_BACK = """(form, kinds) => {
  const optionText = el => (form.querySelector(`label[for="${el.id}"]`)?.textContent || el.value)
    .replace(/\\s+/g, " ").trim();
  const out = {};
  for (const [name, kind] of Object.entries(kinds)) {
    const all = [...form.querySelectorAll(`[name="${CSS.escape(name)}"]`)];
    if (!all.length) continue;
    const el = all[0];
    if (kind === "file") out[name] = el.files[0]?.name || null;
    else if (kind === "radio") {
      const checked = all.find(e => e.checked);
      out[name] = checked ? optionText(checked) : null;
    } else if (kind === "checkboxes") out[name] = all.filter(e => e.checked).map(optionText);
    else if (kind === "legal") out[name] = el.checked ? "Yes" : "No";
    else out[name] = el.value;
  }
  return out;
}"""


def _on_form(url: str) -> bool:
    return urlsplit(url).path.rstrip("/").endswith("/c/new")


class Recruitee:
    name = "recruitee"
    known_gaps: dict[str, Gap] = {}
    side_effects = [SideEffect("submit", "employer", "the application reaches the employer")]

    def __init__(self) -> None:
        self._kinds: dict[str, str] = {}  # how the page renders each field

    def detect(self, url: str, page: Page | None) -> bool:
        """A `*.recruitee.com` posting, or a posting page that loads Recruitee's career site."""
        parts = urlsplit(url)
        if not JOB.match(parts.path):
            return False
        if (parts.hostname or "").endswith(".recruitee.com"):
            return True
        return page is not None and page.locator(CAREER_SITE).count() > 0

    def posting(self, url: str) -> str:
        parts = urlsplit(url)
        match = JOB.match(parts.path)
        return f"https://{parts.hostname}/o/{match[1]}" if match else url

    def open_form(self, page: Page, url: str) -> None:
        page.goto(url if _on_form(url) else f"{self.posting(url)}/c/new")

    def browser_read(self, page: Page) -> list[Field]:
        if not _on_form(page.url):
            self.open_form(page, page.url)
        form = page.locator(FORM)
        try:
            form.wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        except Timeout:
            raise Unsupported("posting_gone: no application form on the page") from None
        return self._listed(html.parse(form.evaluate("f => f.outerHTML")))

    def request_read(self, url: str) -> list[Field] | None:
        """The form's own page, server-rendered, on a `*.recruitee.com` host: a custom domain is
        known only by its page."""
        parts = urlsplit(url)
        if not JOB.match(parts.path) or not (parts.hostname or "").endswith(".recruitee.com"):
            return None
        request = urllib.request.Request(
            f"{self.posting(url)}/c/new", headers={"User-Agent": "atsfill"}
        )
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
                page = html.parse(response.read().decode())
        except OSError, ValueError:
            return None
        form = page.find(lambda n: n.tag == "form" and n.attrs.get("id") == FORM_ID)
        return self._listed(form) if form else None

    def _listed(self, form: Node) -> list[Field]:
        fields, self._kinds = [], {}
        for raw in _read(form):
            fields.append(_field(raw))
            if raw["kind"] != "video":
                self._kinds[raw["id"]] = raw["kind"]
        return fields

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        for name, answer in answers.items():
            values = [answer] if isinstance(answer, str) else answer
            controls = page.locator(f"{FORM} [name='{name}']")
            match self._kinds[name]:
                case "file":
                    controls.first.set_input_files(values[0])
                case "radio" | "checkboxes":
                    for value in values:
                        if (option := _option(page, controls, value)) is not None:
                            _set(option, True)
                case "legal":
                    _set(controls.first, answer == "Yes")
                case "letter":  # the switch swaps the upload for the textarea
                    if not controls.count():
                        browser.click(page.locator(f"{FORM} {LETTER_SWITCH}"))
                    controls.first.fill(values[0])
                case "phone":  # the country picker follows the number's own prefix
                    controls.first.fill("")
                    controls.first.press_sequentially(values[0], delay=KEY_DELAY_MS)
                case _:
                    controls.first.fill(values[0])

    def shown_fields(self, page: Page) -> list[str]:
        form = html.parse(page.locator(FORM).evaluate("f => f.outerHTML"))
        return [raw["id"] for raw in _read(form)]

    def read_back(self, page: Page) -> dict[str, Answer]:
        values = page.locator(FORM).evaluate(_READ_BACK, self._kinds)
        return {k: v for k, v in values.items() if v not in (None, "", [])}

    def submit(self, page: Page) -> str | None:
        """Clicks submit; hCaptcha checks the visitor, then the page shows its thanks panel, the
        evidence, or a panel saying this address already applied."""
        browser.click(page.locator(f"{FORM} [data-testid=submit-application-form-button]"))
        panel = page.locator("[role=tabpanel]:not([hidden]):has([aria-live=assertive] h3)")
        try:
            panel.wait_for(timeout=SUBMIT_TIMEOUT_MS)
        except Timeout:
            if page.locator("iframe[src*='hcaptcha'][title*='challenge']").is_visible():
                raise Blocked("captcha: hCaptcha asked for a challenge") from None
            raise
        shown = " ".join((panel.locator("h3").text_content() or "").split())
        if panel.locator("a[href^='mailto:']").count():
            raise Blocked(f"already_applied: {shown}")
        return f"{page.url} ({shown})"


def _field(raw: dict[str, Any]) -> Field:
    kind = raw["kind"]
    if kind == "video":
        reason = out_of_scope(raw["id"], "a video recorded live")
        return Field(raw["id"], raw["label"], FieldType.FILE, raw["required"], unfillable=reason)
    label = raw["label"] + (" (a number)" if kind == "number" else "")
    options = {"legal": YES_NO, "radio": raw["options"], "checkboxes": raw["options"]}.get(kind)
    accept = None
    if raw["accept"]:
        accept = [e for a in raw["accept"] for e in EXTENSIONS.get(a, [])] or None
    return Field(
        raw["id"],
        label,
        KINDS[kind],
        raw["required"],
        options,
        raw.get("maxLength"),
        accept,
        one_of=raw.get("oneOf"),
    )


def _set(control: Locator, checked: bool) -> None:
    """Checks or unchecks a radio or checkbox, drawn hidden under its styled box, by a click
    event on the input itself."""
    if control.is_checked() != checked:
        control.dispatch_event("click")


def _option(page: Page, controls: Locator, value: str) -> Locator | None:
    """The radio or checkbox whose label is `value`."""
    for control in controls.all():
        label = page.locator(f"{FORM} label[for='{control.get_attribute('id')}']")
        text = (label.text_content() if label.count() else None) or control.get_attribute("value")
        if " ".join((text or "").split()) == value:
            return control
    return None


def _text(node: Node | None) -> str:
    """A node's text without what it hides from screen readers, its runs of whitespace one
    space."""
    if node is None:
        return ""

    def shown(n: Node) -> str:
        return "".join(
            c if isinstance(c, str) else shown(c)
            for c in n.children
            if isinstance(c, str) or c.attrs.get("aria-hidden") != "true"
        )

    return re.sub(r"[\s\ufeff]+", " ", shown(node)).strip()  # as JavaScript's `\s`


def _read(form: Node) -> list[dict[str, Any]]:
    """The fields in page order, one per control name: its question's label, and how it
    renders. A labelled question without a named control (a video recorder, drawn late) comes as
    kind `video`; one without a label is text only. A cover letter upload the switch turns into
    a textarea is followed by that textarea, its alternative."""
    nodes = form.walk()
    by_id = {n.attrs["id"]: n for n in nodes if "id" in n.attrs}
    labels: dict[str, Node] = {}
    for n in nodes:
        if n.tag == "label" and "for" in n.attrs:
            labels.setdefault(n.attrs["for"], n)
    found: dict[str, dict[str, Any]] = {}
    for el in nodes:
        name, kind = el.attrs.get("name"), el.attrs.get("type", "text").lower()
        if el.tag not in ("input", "textarea") or not name:
            continue
        if el.tag == "textarea":
            kind = "textarea"
        if kind == "hidden":
            if not name.endswith(".openQuestionId") or el.parent is None:
                continue
            question = name.removesuffix(".openQuestionId")
            label = el.parent.find(lambda n: n.tag == "label")
            answered = any(
                n.attrs.get("name", "").startswith(f"{question}.")
                and n.attrs.get("type") != "hidden"
                for n in nodes
            )
            if label is not None and not answered:
                found[question] = {"id": question, "kind": "video", "label": _text(label),
                                   "required": "*" in label.text(), "options": []}  # fmt: skip
            continue
        field = found.get(name)
        if field is None:
            max_length = int(el.attrs.get("maxlength") or 0)
            field: dict[str, Any] = {
                "id": name,
                "required": False,
                "options": [],
                "accept": None,
                "maxLength": max_length if max_length > 0 else None,
            }
            if kind == "radio":
                field["kind"] = "radio"
            elif kind == "checkbox" and re.search(r"\.(flag|consent)$", name):
                field["kind"] = "legal"
                field["label"] = _text(by_id.get(el.attrs.get("aria-labelledby", "")))
            elif kind == "checkbox":
                field["kind"] = "checkboxes"
            elif kind == "file":
                field["kind"] = "file"
                accept = el.attrs.get("accept")
                field["accept"] = [a.strip() for a in accept.split(",")] if accept else None
            else:
                field["kind"] = {"tel": "phone", "date": "date", "number": "number"}.get(
                    kind, "text"
                )
            if "label" not in field:
                if field["kind"] in ("radio", "checkboxes"):
                    group = el.closest(lambda n: n.tag == "fieldset")
                    field["label"] = _text(
                        group.find(lambda n: n.tag == "legend") if group else None
                    )
                else:
                    own = labels.get(el.attrs.get("id", ""))
                    field["label"] = _text(own) or el.attrs.get("placeholder", "")
            found[name] = field
        field["required"] = field["required"] or "required" in el.attrs
        if kind == "radio" or (kind == "checkbox" and field["kind"] == "checkboxes"):
            field["options"].append(
                _text(labels.get(el.attrs.get("id", ""))) or el.attrs.get("value", "")
            )
    switch = any(n.attrs.get("data-cy") == LETTER_SWITCH_CY for n in nodes)
    out = []
    for field in found.values():
        out.append(field)
        if field["id"] == LETTER_FILE and switch:
            field["oneOf"] = LETTER_FILE
            out.append({"id": LETTER, "kind": "letter", "label": field["label"],
                        "required": field["required"], "options": [], "accept": None,
                        "maxLength": None, "oneOf": LETTER_FILE})  # fmt: skip
    return out
