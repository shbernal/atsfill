"""A fictional applicant whose answers come from each form's own fields (ADR 0009)."""

import re
from datetime import date, timedelta
from pathlib import Path

from atsfill.core.results import Answer, Field, FieldType

FIRST, LAST = "Ada", "Placeholder"
EMAIL = "ada.placeholder@example.com"
PHONE = "+12025550143"  # 555-01xx numbers are reserved for fiction
WEBSITE = "https://example.com"
SALARY = "50000"
TEXT = "Placeholder answer for a dry run."
CITY = "London"

SENSITIVE = re.compile(
    r"gender|sex\b|race|ethnic|hispanic|latin|veteran|disab|pronoun|orientation|demograph",
    re.IGNORECASE,
)
DECLINE = re.compile(
    r"decline|prefer not|not to (answer|say|disclose)|don.?t wish|do not wish", re.IGNORECASE
)
BY_LABEL = [
    (re.compile(r"e-?mail", re.IGNORECASE), EMAIL),
    (re.compile(r"first.?name|given name|pr[ée]nom", re.IGNORECASE), FIRST),
    (re.compile(r"last.?name|family name|surname", re.IGNORECASE), LAST),
    (re.compile(r"name", re.IGNORECASE), f"{FIRST} {LAST}"),
    (re.compile(r"phone|mobile", re.IGNORECASE), PHONE),
    (re.compile(r"how many|number of|years of", re.IGNORECASE), "3"),
    (re.compile(r"\byear\b", re.IGNORECASE), "2020"),
    (re.compile(r"salar|compensation|pay\b", re.IGNORECASE), SALARY),
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


def _option(field: Field) -> str:
    options = field.options or []
    if SENSITIVE.search(field.label):
        return next((o for o in options if DECLINE.search(o)), options[0])
    return options[0]


def _text(field: Field) -> str:
    value = next((v for pattern, v in BY_LABEL if pattern.search(field.label)), TEXT)
    return value[: field.max_length] if field.max_length else value


def answer(field: Field, cv: Path) -> Answer | None:
    """The persona's answer to one field; None for a choice with no options to pick from."""
    match field.type:
        case FieldType.FILE:
            return str(cv)
        case FieldType.PHONE:
            return PHONE
        case FieldType.DATE:
            return (date.today() + timedelta(days=30)).isoformat()
        case FieldType.CHOICE | FieldType.MULTI_CHOICE if not field.options:
            return None
        case FieldType.CHOICE:
            return _option(field)
        case FieldType.MULTI_CHOICE:
            return [_option(field)]
        case FieldType.TYPEAHEAD:
            return field.options[0] if field.options else CITY
        case _:
            return _text(field)


def answers(fields: list[Field], cv: Path) -> dict[str, Answer]:
    """Answers every field it can, so the dry run reaches each one, but leaves what the account
    prefilled to the account, a résumé aside."""
    return {
        f.id: a
        for f in fields
        if (a := answer(f, cv)) is not None and not (f.prefilled and f.type is not FieldType.FILE)
    }
