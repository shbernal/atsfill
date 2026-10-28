"""A failed run's page, for `detailed` telemetry and `atsfill report` (ADR 0016): the DOM
without scripts and styles, every value a field holds stripped in the page, then every answer,
uploaded file name and account email replaced, here in the core, before it is kept."""

import html
import re
from collections.abc import Iterable
from pathlib import Path

from atsfill.core.browser import Page
from atsfill.core.results import Answer, Field

MAX_BYTES = 800 * 1024  # the SDK drops an event above 900 KiB; room for its other properties
MIN_SECRET = 3  # shorter answers ("No", "5") would wipe the page's own words, and tell little
NAME_WORDS = 4  # an answer of up to this many words is scrubbed word by word too
SCRUBBED = "[scrubbed]"
TRUNCATED = "<!-- truncated -->"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# The page and the failing step's subtree, the smallest element holding every form control.
CLEAN = """() => {
    const root = document.documentElement.cloneNode(true);
    root.querySelectorAll("script, style, noscript, template, svg, link, meta").forEach(
        e => e.remove());
    for (const e of root.querySelectorAll("*")) {
        for (const a of ["value", "checked", "selected", "aria-valuenow", "aria-valuetext"])
            e.removeAttribute(a);
    }
    root.querySelectorAll("textarea, [contenteditable]").forEach(e => { e.textContent = ""; });
    const controls = [...root.querySelectorAll(
        "input, select, textarea, button, [role=combobox], [role=textbox], [contenteditable]")];
    let step = controls[0] ?? root.querySelector("body") ?? root;
    while (!controls.every(c => step.contains(c))) step = step.parentElement;
    return [root.outerHTML, step.outerHTML];
}"""


def capture(page: Page) -> tuple[str, str]:
    """The page and its failing step, values stripped, not yet scrubbed of secrets."""
    whole, step = page.evaluate(CLEAN)
    return whole, step


def secrets(answers: dict[str, Answer], fields: Iterable[Field], emails: Iterable[str]) -> set[str]:
    """What may not leave the machine: each answer, a file answer's name and stem, each
    prefilled value and each account email; each word of a short one, a name the page may
    show in part."""
    out = set(emails)
    values = [*answers.values(), *(f.prefilled for f in fields if f.prefilled)]
    for value in values:
        for v in [value] if isinstance(value, str) else value:
            out |= {v, Path(v).name, Path(v).stem} if "/" in v or "\\" in v else {v}
            if len(words := v.split()) <= NAME_WORDS:
                out |= set(words)
    return {s.strip() for s in out if len(s.strip()) >= MIN_SECRET}


def scrub(text: str, secrets: Iterable[str]) -> str:
    """The text with each secret, as typed or HTML-escaped, and every email address replaced."""
    forms = {f for s in secrets for f in (s, html.escape(s), html.escape(s, quote=False))}
    if forms:
        alternatives = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
        whole_word = re.compile(rf"(?<![^\W_])(?:{alternatives})(?![^\W_])", re.IGNORECASE)
        text = whole_word.sub(SCRUBBED, text)
    return EMAIL.sub(SCRUBBED, text)


def kept(captured: tuple[str, str], secrets: Iterable[str]) -> str:
    """The scrubbed page, or its failing step when the page is over the limit, cut at it."""
    whole, step = (scrub(c, secrets) for c in captured)
    for snapshot in (whole, step):
        if len(snapshot.encode()) <= MAX_BYTES:
            return snapshot
    cut = step.encode()[: MAX_BYTES - len(TRUNCATED)].decode(errors="ignore")
    return cut + TRUNCATED
