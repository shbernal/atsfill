"""The Test user, a fictional user whose answers come from each form's own fields (ADR 0009)."""

import re
import secrets
import shutil
import struct
import zlib
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from atsfill.core.conditions import Form
from atsfill.core.results import Answer, Field, FieldType

FIRST, LAST = "Ada", "Placeholder"
# A new address each run: Oracle emails an OTP, instead of showing the form, to one it has seen.
EMAIL = f"ada.placeholder.{secrets.token_hex(4)}@example.com"
PHONE = "+12025550143"  # 555-01xx numbers are reserved for fiction
WEBSITE = "https://example.com"
SALARY = "50000"
TEXT = "Placeholder answer for a dry run."
CITY = "London"
SKILL = "Python"
SCHOOL = "Aa"  # the start of the first school an alphabetical list of them gives
BIRTH_DATE = "1990-01-31"
COUNTRY = "United States"
COUNTRY_NAMES = (COUNTRY, "United States of America")  # the names a country list gives it
ZIP = "20001"
REGION = "District of Columbia"  # the state of the Test user's phone and zip code

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
# An option that opens a field the form did not list: who referred or contacted, which other.
OPENS_FIELD = re.compile(r"referr|contacted by|\bother\b", re.IGNORECASE)
PAST_DATE = re.compile(r"(from|to|end)\b", re.IGNORECASE)
BY_LABEL = [
    (re.compile(r"MM/YYYY"), "01/2020"),
    (re.compile(r"zip|postal", re.IGNORECASE), ZIP),
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
    """Writes the Test user's résumé to `path`."""
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
    """Writes the Test user's photo to `path`: a 64x64 grey PNG."""
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


def clip(path: Path) -> Path:
    """Writes the Test user's video clip to `path`: one grey second, 64x64 H.264 in MP4, the
    least a transcoder takes (a file that only names itself MP4 fails Teamtailor's). Made with
    `ffmpeg -f lavfi -i color=gray:s=64x64:r=1 -t 1 -c:v libx264 -pix_fmt yuv420p -crf 51
    -movflags +faststart -map_metadata -1 -fflags +bitexact clip.mp4`."""
    shutil.copyfile(Path(__file__).with_name("clip.mp4"), path)
    return path


def _option(field: Field) -> str:
    options = field.options or []
    if SENSITIVE.search(field.label):
        return next((o for o in options if DECLINE.search(o)), options[0])
    if re.search(r"country", field.label, re.IGNORECASE):
        # where the Test user's phone and zip code are; `United States of America (+1)`
        mentioned = sorted((o for o in options if COUNTRY in o), key=len)
        return next((o for o in options if o in COUNTRY_NAMES), next(iter(mentioned), options[0]))
    if re.search(r"state|province|region", field.label, re.IGNORECASE) and REGION in options:
        return REGION
    if ANSWER_NO.search(field.label) and "No" in options:
        return "No"
    return next((o for o in options if not OPENS_FIELD.search(o)), options[0])


def _text(field: Field) -> str:
    value = next((v for pattern, v in BY_LABEL if pattern.search(field.label)), TEXT)
    return value[: field.max_length] if field.max_length else value


def _accepts(field: Field, extension: str, mime: str) -> bool:
    """Whether a file field takes a file by its extension or its media type."""
    return not field.accept or extension in field.accept or mime in field.accept


def answer(
    field: Field, cv: Path, picture: Path | None = None, video: Path | None = None
) -> Answer | None:
    """The Test user's answer to one field; None for a choice with no options to pick from, an
    optional search of places, or a file that takes neither a PDF nor, given a `picture` or a
    `video`, a PNG or an MP4."""
    match field.type:
        case FieldType.FILE if not _accepts(field, ".pdf", "application/pdf"):
            if picture and _accepts(field, ".png", "image/png"):
                return str(picture)
            return str(video) if video and _accepts(field, ".mp4", "video/mp4") else None
        case FieldType.FILE:
            return str(cv)
        case FieldType.PHONE:
            return PHONE
        case FieldType.URL:  # a handle by its label is no answer to a field declared a URL
            return value if (value := _text(field)).startswith("https://") else WEBSITE
        case FieldType.DATE if re.search(r"birth", field.label, re.IGNORECASE):
            return BIRTH_DATE
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
            if re.search(r"zip|postal", field.label, re.I):
                return ZIP
            if re.search(r"skill", field.label, re.I):
                return SKILL
            if re.search(r"school|university", field.label, re.I):
                return SCHOOL
            if re.search(r"citizen", field.label, re.I):
                return f"Citizen ({COUNTRY_NAMES[1]})"
            if re.search(r"nationalit|country", field.label, re.I):
                return COUNTRY
            if re.search(r"state|province|region", field.label, re.I):
                return REGION
            # an optional search of places is left: it may list only the form's country's (Workday)
            return CITY if field.required else None
        case _:
            return _text(field)


def _as_listed(field: Field, form: Form) -> Field:
    """A dependent list as a plain choice of the options its parents leave."""
    if not field.option_sets:
        return field
    return replace(field, options=form.options(field), option_sets=None)


def _showing_most(
    field: Field,
    fields: list[Field],
    given: dict[str, Answer],
    cv: Path,
    picture: Path | None,
    video: Path | None,
) -> Answer:
    """The choice that shows the most follow-ups, so that the dry run reaches them, never one
    that shows a required follow-up, or leaves a required dependent list, it has no answer
    for: among equals the page's own pick, else the usual answer, and always the usual for a
    sensitive question."""
    usual = answer(field, cv, picture, video)
    assert usual is not None
    if SENSITIVE.search(field.label) or not field.options:
        return usual
    if field.type is FieldType.CHOICE and field.default in field.options:
        usual = field.default  # the page's country decides the lists and fields read after it

    def shows(a: Answer) -> tuple[int, int]:
        form = Form(fields, {**given, field.id: a})
        shown = [f for f in fields if f.shown_when and form.shown(f)]
        lists = [f for f in fields if f.option_sets and not f.shown_when and form.shown(f)]
        unanswerable = [
            f
            for f in (_as_listed(f, form) for f in shown + lists)
            if form.required(f) and (f.unfillable or answer(f, cv, picture, video) is None)
        ]
        return -len(unanswerable), len(shown)

    picks = [o if field.type is FieldType.CHOICE else [o] for o in field.options]
    return max([usual, *picks], key=shows)


def answers(
    fields: list[Field], cv: Path, picture: Path | None = None, video: Path | None = None
) -> dict[str, Answer]:
    """Answers every field it can, so the dry run reaches each one, but leaves what the account
    prefilled to the account, a résumé aside, a field its own answers keep hidden, and one
    atsfill cannot fill. A choice that shows follow-ups takes the option that shows the most.
    Of a group of alternatives, it answers one, the file where it can."""
    out, given, answered = {}, {}, set()
    files = {f.one_of for f in fields if f.type is FieldType.FILE and not f.unfillable}
    for f in fields:
        form = Form(fields, given)
        if not form.shown(f):
            continue
        if f.one_of and (
            f.one_of in answered or (f.type is not FieldType.FILE and f.one_of in files)
        ):
            continue
        f = _as_listed(f, form)  # a dependent list, answered from the options its parents leave
        if f.unfillable or (a := answer(f, cv, picture, video)) is None:
            continue
        if f.type in (FieldType.CHOICE, FieldType.MULTI_CHOICE):
            a = _showing_most(f, fields, given, cv, picture, video)
        given[f.id] = a
        answered.add(f.one_of)
        if f.prefilled is None or f.type is FieldType.FILE:
            out[f.id] = a
    return out
