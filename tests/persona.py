"""A fictional applicant whose answers come from each form's own fields (ADR 0009)."""

import re
import secrets
import struct
import zlib
from datetime import date, timedelta
from pathlib import Path

from atsfill.core.results import Answer, Field, FieldType

FIRST, LAST = "Ada", "Placeholder"
# A new address each run: Oracle emails a code, instead of showing the form, to one it has seen.
EMAIL = f"ada.placeholder.{secrets.token_hex(4)}@example.com"
PHONE = "+12025550143"  # 555-01xx numbers are reserved for fiction
WEBSITE = "https://example.com"
SALARY = "50000"
TEXT = "Placeholder answer for a dry run."
CITY = "London"
COUNTRY = "United States"
COUNTRY_NAMES = (COUNTRY, "United States of America")  # the names a country list gives it
REGION = "District of Columbia"  # the state of the persona's phone and zip code

SENSITIVE = re.compile(
    r"gender|sex\b|race|ethnic|hispanic|latin|veteran|disab|pronoun|orientation|demograph",
    re.IGNORECASE,
)
DECLINE = re.compile(
    r"decline|prefer not|not to (answer|say|disclose|self-identify)|(don.?t|do not) (wish|want)",
    re.IGNORECASE,
)
# A question a "Yes" can end the application on, or open fields the form did not list.
ANSWER_NO = re.compile(
    r"employee of|employed by|worked (for|at)|relative|sponsor|preferred name", re.IGNORECASE
)
PAST_DATE = re.compile(r"(from|to|end)\b", re.IGNORECASE)
# A field's label that says it is asked only after another's answer.
ASKED_WHEN = re.compile(
    r"""\(asked when (["'])(?P<trigger>.+?)\1 is (?P<negated>not )?"?(?P<value>.+?)"?\)$"""
)
BY_LABEL = [
    (re.compile(r"MM/YYYY"), "01/2020"),
    (re.compile(r"zip|postal", re.IGNORECASE), "20001"),
    (re.compile(r"extension", re.IGNORECASE), "1"),
    (re.compile(r"e-?mail", re.IGNORECASE), EMAIL),
    (re.compile(r"first.?name|given name|pr[ée]nom", re.IGNORECASE), FIRST),
    (re.compile(r"last.?name|family name|surname", re.IGNORECASE), LAST),
    (re.compile(r"name", re.IGNORECASE), f"{FIRST} {LAST}"),
    (re.compile(r"phone|mobile", re.IGNORECASE), PHONE),
    (re.compile(r"end time", re.IGNORECASE), "17:00"),
    (re.compile(r"start time", re.IGNORECASE), "09:00"),
    (re.compile(r"how many|number of|years of", re.IGNORECASE), "3"),
    (re.compile(r"\byear\b", re.IGNORECASE), "2020"),
    (re.compile(r"salar|compensation|pay\b", re.IGNORECASE), SALARY),
    (re.compile(r"\(a number\)"), "3"),
    (re.compile(r"linkedin", re.IGNORECASE), "https://www.linkedin.com/in/ada-placeholder"),
    (re.compile(r"facebook", re.IGNORECASE), "https://www.facebook.com/ada.placeholder"),
    (re.compile(r"twitter|\bX\b"), "adaplaceholder"),
    (re.compile(r"linkedin|github|website|portfolio|url|link", re.IGNORECASE), WEBSITE),
    (re.compile(r"city|location|where", re.IGNORECASE), CITY),
]

# One page, "Ada Placeholder", in Helvetica: the smallest PDF a résumé parser accepts.
_PAGE = b"BT /F1 24 Tf 72 720 Td (Ada Placeholder) Tj ET"
_OBJECTS = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
    b" /Resources << /Font << /F1 5 0 R >> >> >>",
    b"<< /Length %d >>\nstream\n%s\nendstream" % (len(_PAGE), _PAGE),
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
]


def resume(path: Path) -> Path:
    """Writes the persona's résumé to `path`."""
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, body in enumerate(_OBJECTS, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (n, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(_OBJECTS) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(_OBJECTS) + 1,
        xref,
    )
    path.write_bytes(out)
    return path


def photo(path: Path) -> Path:
    """Writes the persona's photo to `path`: a 64x64 grey PNG."""
    chunk = lambda kind, data: (  # noqa: E731
        struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    )
    rows = b"".join(b"\0" + b"\x80" * 3 * 64 for _ in range(64))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    return path


def _option(field: Field) -> str:
    options = field.options or []
    if SENSITIVE.search(field.label):
        return next((o for o in options if DECLINE.search(o)), options[0])
    if re.search(r"country", field.label, re.IGNORECASE):
        # where the persona's phone and zip code are; `United States of America (+1)`
        mentioned = sorted((o for o in options if COUNTRY in o), key=len)
        return next((o for o in options if o in COUNTRY_NAMES), next(iter(mentioned), options[0]))
    if ANSWER_NO.search(field.label) and "No" in options:
        return "No"
    return options[0]


def _text(field: Field) -> str:
    value = next((v for pattern, v in BY_LABEL if pattern.search(field.label)), TEXT)
    return value[: field.max_length] if field.max_length else value


def answer(field: Field, cv: Path, picture: Path | None = None) -> Answer | None:
    """The persona's answer to one field; None for a choice with no options to pick from, or
    a file that takes neither a PDF nor, given a `picture`, a PNG."""
    match field.type:
        case FieldType.FILE if field.accept and ".pdf" not in field.accept:
            return str(picture) if picture and ".png" in field.accept else None
        case FieldType.FILE:
            return str(cv)
        case FieldType.PHONE:
            return PHONE
        case FieldType.DATE:  # an end date after a start date; a past one in a history entry
            end = re.search(r"\bend|finish|until", field.label, re.IGNORECASE)
            if PAST_DATE.match(field.label) or re.search(
                r"history|employment|education", field.label, re.IGNORECASE
            ):
                days = -365 if end or field.label.lower().startswith("to") else -3 * 365
            else:
                days = 60 if end else 30
            return (date.today() + timedelta(days=days)).isoformat()
        case FieldType.CHOICE | FieldType.MULTI_CHOICE if not field.options:
            return None
        case FieldType.CHOICE:
            return _option(field)
        case FieldType.MULTI_CHOICE:
            return [_option(field)]
        case FieldType.TYPEAHEAD:
            if field.options:
                return field.options[0]
            return REGION if re.search(r"state|province|region", field.label, re.I) else CITY
        case _:
            return _text(field)


def _asked(field: Field, given: dict[str, Answer]) -> bool:
    """Whether the page shows the field, given the answers so far, when its label says it is
    asked only after another field's answer."""
    match = ASKED_WHEN.search(field.label)
    if not match or match["trigger"] not in given:
        return True
    picked = given[match["trigger"]]
    picks = match["value"].split(" or ")
    shown = any(p in picks for p in ([picked] if isinstance(picked, str) else picked))
    return shown != bool(match["negated"])


def answers(fields: list[Field], cv: Path, picture: Path | None = None) -> dict[str, Answer]:
    """Answers every field it can, so the dry run reaches each one, but leaves what the account
    prefilled to the account, a résumé aside, a field its own answers keep hidden, and one
    atsfill cannot fill."""
    out, by_label = {}, {}
    for f in fields:
        if f.unfillable or (a := answer(f, cv, picture)) is None or not _asked(f, by_label):
            continue
        by_label[f.label] = a
        if not (f.prefilled and f.type is not FieldType.FILE):
            out[f.id] = a
    return out
