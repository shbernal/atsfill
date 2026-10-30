"""Read-back matched against the answers by field type (ADR 0005)."""

import re
import unicodedata
from pathlib import Path, PurePath
from urllib.parse import urlsplit

from atsfill.core.conditions import Form, describe
from atsfill.core.countries import ALPHA_3
from atsfill.core.results import Answer, Check, Field, FieldType


def _norm(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def _options(field: Field, form: Form | None) -> list[str] | None:
    """The options an answer is one of; without the form, a dependent list's are unknown."""
    if form is not None:
        return form.options(field)
    return None if field.option_sets else field.options


def _in_options(field: Field, values: list[str], form: Form | None) -> bool:
    options = _options(field, form)
    return options is None or all(v in options for v in values)


def _place(part: str) -> str:
    """A place's part, a country's name as its alpha-3 code, which some suggestions show."""
    part = part.strip()
    return ALPHA_3.get(part, part).casefold()


def _picked_from(answer: str, suggestion: str) -> bool:
    """The suggestion starts with the answer, or holds its comma-separated parts in order:
    "Paris" and "Paris, France" both pick "Paris, Île-de-France, France" and
    "Paris, Île-de-France, FRA"."""
    answer, suggestion = _norm(answer).casefold(), _norm(suggestion).casefold()
    if suggestion.startswith(answer):
        return True
    parts = iter(_place(p) for p in suggestion.split(","))
    return all(any(p == _place(wanted) for p in parts) for wanted in answer.split(","))


def _absolute_url(value: str) -> bool:
    parts = urlsplit(_norm(value))
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def matches(
    field: Field, answer: Answer, read_back: Answer | None, form: Form | None = None
) -> bool:
    if field.type is FieldType.MULTI_CHOICE and answer == []:
        # a cleared list: a read-back may leave out a field that holds nothing
        return read_back in (None, "", [])
    if read_back is None:
        return False
    match field.type:
        case FieldType.MULTI_CHOICE:
            a, r = [answer] if isinstance(answer, str) else answer, read_back
            r = [r] if isinstance(r, str) else r
            return sorted(a) == sorted(r) and _in_options(field, a, form)
    if not isinstance(answer, str) or not isinstance(read_back, str):
        return False
    match field.type:
        case FieldType.TEXT | FieldType.URL:
            return _norm(answer) == _norm(read_back)
        case FieldType.DATE:
            return answer == read_back
        case FieldType.CHOICE:
            return answer == read_back and _in_options(field, [answer], form)
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
        Check(f.id, a, read_back.get(f.id), matches(f, a, read_back.get(f.id), form))
        for f_id, a in answers.items()
        if (f := by_id.get(f_id))
    ]
    return checks + [
        Check(f.id, None, read_back[f.id], False)
        for f in fields
        if not form.shown(f) and f.id not in answers and read_back.get(f.id) not in (None, "", [])
    ]


def _answer_problem(field: Field, answer: object, form: Form) -> str | None:
    if field.type is FieldType.MULTI_CHOICE:
        if not (isinstance(answer, list) and all(isinstance(v, str) for v in answer)):
            return "a multi_choice answer is a list of strings"
        if not answer:
            return "required, so an empty list does not answer it" if form.asked(field) else None
        values = answer
    elif not isinstance(answer, str):
        return f"a {field.type} answer is a string"
    else:
        values = [answer]
    if field.type in (FieldType.CHOICE, FieldType.MULTI_CHOICE) and not _in_options(
        field, values, form
    ):
        held = form.option_set(field)
        under = f", the options when {describe(held.when, form.by_id)}" if held else ""
        return f"{answer!r} is not one of {form.options(field)}{under}"
    length = len(_norm(values[0]))
    if (
        field.type in (FieldType.TEXT, FieldType.URL)
        and field.max_length
        and length > field.max_length
    ):
        return f"{length} characters, over the limit of {field.max_length}"
    if field.type is FieldType.TEXT and field.min_length and length < field.min_length:
        return f"{length} characters, under the minimum of {field.min_length}"
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
    """What refuses a fill before typing: unknown ids, missing required answers, more than one
    alternative answered, answers to follow-ups the answers keep hidden, bad values."""
    by_id = {f.id: f for f in fields}
    out = [f"no field {i}" for i in answers if i not in by_id]
    form = Form(fields, {i: a for i, a in answers.items() if i in by_id})
    out += [
        f"no answer for required {f.id} ({f.label})"
        for f in fields
        if form.required(f) and f.prefilled is None and not f.unfillable and f.id not in answers
    ]
    for key, members in form.groups.items():
        given = [m.id for m in members if m.id in answers]
        if len(given) > 1:
            out.append(f"{key}: one of {', '.join(given)}, not both")
        elif form.group_required(key) and not given and all(m.prefilled is None for m in members):
            labels = " or ".join(dict.fromkeys(m.label for m in members))
            out.append(f"no answer for required {key} ({labels})")
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
        if i in by_id
        and not by_id[i].unfillable
        and (problem := _answer_problem(by_id[i], a, form))
    ]
    return out


def to_fill(fields: list[Field], answers: dict[str, Answer]) -> dict[str, Answer]:
    """The answers an adapter fills, after `problems` passed them: an empty list clears what a
    field holds, so one that holds nothing has nothing to fill."""
    by_id = {f.id: f for f in fields}
    return {i: a for i, a in answers.items() if a != [] or by_id[i].prefilled or by_id[i].default}


def knockouts(fields: list[Field], answers: dict[str, Answer]) -> list[str]:
    """The answers that end the application: never refused, as one can be the truthful one."""
    by_id = {f.id: f for f in fields}
    return [
        f"{i}: {', '.join(map(repr, chosen))} ends the application ({f.label})"
        for i, a in answers.items()
        if (f := by_id.get(i))
        and f.knockouts
        and (chosen := [v for v in ([a] if isinstance(a, str) else a) if v in f.knockouts])
    ]
