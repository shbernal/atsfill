"""Read-back matched against the answers by field type (ADR 0005)."""

import re
import unicodedata
from pathlib import Path, PurePath
from urllib.parse import urlsplit

from atsfill.core.conditions import Form, describe
from atsfill.core.results import Answer, Check, Field, FieldType


def _norm(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def _in_options(field: Field, values: list[str]) -> bool:
    return field.options is None or all(v in field.options for v in values)


def _picked_from(answer: str, suggestion: str) -> bool:
    """The suggestion starts with the answer, or holds its comma-separated parts in order:
    "Paris" and "Paris, France" both pick "Paris, Île-de-France, France"."""
    answer, suggestion = _norm(answer).casefold(), _norm(suggestion).casefold()
    if suggestion.startswith(answer):
        return True
    parts = iter(p.strip() for p in suggestion.split(","))
    return all(any(p == wanted.strip() for p in parts) for wanted in answer.split(","))


def _absolute_url(value: str) -> bool:
    parts = urlsplit(_norm(value))
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def matches(field: Field, answer: Answer, read_back: Answer | None) -> bool:
    if read_back is None:
        return False
    match field.type:
        case FieldType.MULTI_CHOICE:
            a, r = [answer] if isinstance(answer, str) else answer, read_back
            r = [r] if isinstance(r, str) else r
            return sorted(a) == sorted(r) and _in_options(field, a)
    if not isinstance(answer, str) or not isinstance(read_back, str):
        return False
    match field.type:
        case FieldType.TEXT | FieldType.URL:
            return _norm(answer) == _norm(read_back)
        case FieldType.DATE:
            return answer == read_back
        case FieldType.CHOICE:
            return answer == read_back and _in_options(field, [answer])
        case FieldType.TYPEAHEAD:
            return _picked_from(answer, read_back)
        case FieldType.FILE:
            return PurePath(answer).name == PurePath(read_back).name
        case FieldType.PHONE:
            return re.sub(r"\D", "", answer) == re.sub(r"\D", "", read_back)
    return False


def check(
    fields: list[Field], answers: dict[str, Answer], read_back: dict[str, Answer]
) -> list[Check]:
    """Each answer against its read-back; a follow-up the answers keep hidden that reads back a
    value is a mismatch too, its condition read wrong."""
    by_id, form = {f.id: f for f in fields}, Form(fields, answers)
    checks = [
        Check(f.id, a, read_back.get(f.id), matches(f, a, read_back.get(f.id)))
        for f_id, a in answers.items()
        if (f := by_id.get(f_id))
    ]
    return checks + [
        Check(f.id, None, read_back[f.id], False)
        for f in fields
        if not form.shown(f) and f.id not in answers and read_back.get(f.id) not in (None, "", [])
    ]


def _answer_problem(field: Field, answer: object) -> str | None:
    if field.type is FieldType.MULTI_CHOICE:
        if not (isinstance(answer, list) and all(isinstance(v, str) for v in answer)):
            return "a multi_choice answer is a list of strings"
        values = answer
    elif not isinstance(answer, str):
        return f"a {field.type} answer is a string"
    else:
        values = [answer]
    if field.type in (FieldType.CHOICE, FieldType.MULTI_CHOICE) and not _in_options(field, values):
        return f"{answer!r} is not one of {field.options}"
    length = len(_norm(values[0]))
    if (
        field.type in (FieldType.TEXT, FieldType.URL)
        and field.max_length
        and length > field.max_length
    ):
        return f"{length} characters, over the limit of {field.max_length}"
    if field.type is FieldType.URL and not _absolute_url(values[0]):
        return "a url answer is an absolute http(s) URL"
    if field.type is FieldType.DATE and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", values[0]):
        return "a date answer is YYYY-MM-DD"
    if field.type is FieldType.FILE:
        path = Path(values[0])
        if not path.is_absolute():
            return "a file answer is an absolute path"
        if not path.is_file():
            return f"no file at {path}"
    return None


def problems(fields: list[Field], answers: dict[str, Answer]) -> list[str]:
    """What refuses a fill before typing: unknown ids, missing required answers, answers to
    follow-ups the answers keep hidden, bad values."""
    by_id = {f.id: f for f in fields}
    out = [f"no field {i}" for i in answers if i not in by_id]
    form = Form(fields, {i: a for i, a in answers.items() if i in by_id})
    out += [
        f"no answer for required {f.id} ({f.label})"
        for f in fields
        if form.required(f) and f.prefilled is None and not f.unfillable and f.id not in answers
    ]
    out += [
        f"{i}: not asked, it shows when {describe(f.shown_when, by_id)}"
        for i in answers
        if (f := by_id.get(i)) and f.shown_when and not form.shown(f)
    ]
    out += [
        f"{i}: cannot be filled ({f.unfillable})"
        for i in answers
        if (f := by_id.get(i)) and f.unfillable
    ]
    out += [
        f"{i}: {problem}"
        for i, a in answers.items()
        if i in by_id and not by_id[i].unfillable and (problem := _answer_problem(by_id[i], a))
    ]
    return out
