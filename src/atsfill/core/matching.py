"""Read-back matched against the answers by field type (ADR 0005)."""

import re
import unicodedata
from pathlib import Path, PurePath

from atsfill.core.results import Answer, Check, Field, FieldType


def _norm(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def _in_options(field: Field, values: list[str]) -> bool:
    return field.options is None or all(v in field.options for v in values)


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
        case FieldType.TEXT:
            return _norm(answer) == _norm(read_back)
        case FieldType.CHOICE:
            return answer == read_back and _in_options(field, [answer])
        case FieldType.TYPEAHEAD:
            return _norm(read_back).casefold().startswith(_norm(answer).casefold())
        case FieldType.FILE:
            return PurePath(answer).name == PurePath(read_back).name
        case FieldType.PHONE:
            return re.sub(r"\D", "", answer) == re.sub(r"\D", "", read_back)
    return False


def check(
    fields: list[Field], answers: dict[str, Answer], read_back: dict[str, Answer]
) -> list[Check]:
    by_id = {f.id: f for f in fields}
    return [
        Check(f.id, a, read_back.get(f.id), matches(f, a, read_back.get(f.id)))
        for f_id, a in answers.items()
        if (f := by_id.get(f_id))
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
    if field.type is FieldType.FILE:
        path = Path(values[0])
        if not path.is_absolute():
            return "a file answer is an absolute path"
        if not path.is_file():
            return f"no file at {path}"
    return None


def problems(fields: list[Field], answers: dict[str, Answer]) -> list[str]:
    """What refuses a fill before typing: unknown ids, missing required answers, bad values."""
    by_id = {f.id: f for f in fields}
    out = [f"no field {i}" for i in answers if i not in by_id]
    out += [
        f"no answer for required {f.id} ({f.label})"
        for f in fields
        if f.required and f.id not in answers
    ]
    out += [
        f"{i}: {problem}"
        for i, a in answers.items()
        if i in by_id and (problem := _answer_problem(by_id[i], a))
    ]
    return out
