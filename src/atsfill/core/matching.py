"""Read-back matched against the answers by field type (ADR 0005)."""

import re
import unicodedata
from pathlib import Path, PurePath
from typing import Self
from urllib.parse import urlsplit

from atsfill.core.conditions import Form, describe
from atsfill.core.countries import ALPHA_3
from atsfill.core.results import Answer, Check, Field, FieldType


class Problem(str):
    """What refuses an answer, in words that can quote it. `field_id` and `rule` never do: the
    id is a listed field's and the rule one of this module's, so telemetry can carry them."""

    field_id: str
    rule: str

    def __new__(cls, field_id: str, rule: str, text: str) -> Self:
        problem = super().__new__(cls, text)
        problem.field_id, problem.rule = field_id, rule
        return problem


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


def _answer_problem(field: Field, answer: object, form: Form) -> tuple[str, str] | None:
    """The rule an answer breaks, and how."""
    if field.type is FieldType.MULTI_CHOICE:
        if not (isinstance(answer, list) and all(isinstance(v, str) for v in answer)):
            return "shape", "a multi_choice answer is a list of strings"
        if not answer:
            if form.asked(field):
                return "missing", "required, so an empty list does not answer it"
            return None
        values = answer
    elif not isinstance(answer, str):
        return "shape", f"a {field.type} answer is a string"
    else:
        values = [answer]
    if field.type in (FieldType.CHOICE, FieldType.MULTI_CHOICE) and not _in_options(
        field, values, form
    ):
        held = form.option_set(field)
        under = f", the options when {describe(held.when, form.by_id)}" if held else ""
        return "not_an_option", f"{answer!r} is not one of {form.options(field)}{under}"
    length = len(_norm(values[0]))
    if (
        field.type in (FieldType.TEXT, FieldType.URL)
        and field.max_length
        and length > field.max_length
    ):
        return "too_long", f"{length} characters, over the limit of {field.max_length}"
    if field.type is FieldType.TEXT and field.min_length and length < field.min_length:
        return "too_short", f"{length} characters, under the minimum of {field.min_length}"
    if field.type is FieldType.URL and not _absolute_url(values[0]):
        return "shape", "a url answer is an absolute http(s) URL"
    if field.type is FieldType.DATE and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", values[0]):
        return "shape", "a date answer is YYYY-MM-DD"
    if field.type is FieldType.FILE:
        path = Path(values[0])
        if not path.is_absolute():
            return "shape", "a file answer is an absolute path"
        if not path.is_file():
            return "no_file", f"no file at {path}"
    return None


def known(fields: list[Field], answers: dict[str, Answer]) -> dict[str, Answer]:
    """The answers to fields of the form."""
    ids = {f.id for f in fields}
    return {i: a for i, a in answers.items() if i in ids}


def as_listed(fields: list[Field], answers: dict[str, Answer]) -> dict[str, Answer]:
    """Each choice as its field spells the option, where only whitespace or Unicode form tells
    them apart: a stray space in a page's option is not the answer's to copy (ADR 0005)."""
    by_id = {f.id: f for f in fields}

    def spelled(field: Field, value: str) -> str:
        sets = [o for s in field.option_sets or [] for o in s.options]
        options = [*(field.options or []), *sets]
        if value in options:
            return value
        return next((o for o in options if _norm(o) == _norm(value)), value)

    out = dict(answers)
    for i, answer in answers.items():
        field = by_id.get(i)
        if not field or field.type not in (FieldType.CHOICE, FieldType.MULTI_CHOICE):
            continue
        if isinstance(answer, str):
            out[i] = spelled(field, answer)
        elif isinstance(answer, list) and all(isinstance(v, str) for v in answer):
            out[i] = [spelled(field, v) for v in answer]
    return out


def problems(fields: list[Field], answers: dict[str, Answer]) -> list[Problem]:
    """What refuses answers to the form's fields before typing: missing required answers, more
    than one alternative answered, answers to follow-ups the answers keep hidden, bad values."""
    by_id = {f.id: f for f in fields}
    form = Form(fields, known(fields, answers))
    out = [
        Problem(f.id, "missing", f"no answer for required {f.id} ({f.label})")
        for f in fields
        if form.required(f) and f.prefilled is None and not f.unfillable and f.id not in answers
    ]
    for key, members in form.groups.items():
        given = [m.id for m in members if m.id in answers]
        if len(given) > 1:
            out.append(Problem(key, "both", f"{key}: one of {', '.join(given)}, not both"))
        elif form.group_required(key) and not given and all(m.prefilled is None for m in members):
            labels = " or ".join(dict.fromkeys(m.label for m in members))
            out.append(Problem(key, "missing", f"no answer for required {key} ({labels})"))
    out += [
        Problem(i, "not_asked", f"{i}: not asked, it shows when {describe(f.shown_when, by_id)}")
        for i in answers
        if (f := by_id.get(i)) and f.shown_when and not form.shown(f)
    ]
    out += [
        Problem(i, "unfillable", f"{i}: cannot be filled ({f.unfillable})")
        for i in answers
        if (f := by_id.get(i)) and f.unfillable
    ]
    out += [
        Problem(i, broken[0], f"{i}: {broken[1]}")
        for i, a in answers.items()
        if i in by_id and not by_id[i].unfillable and (broken := _answer_problem(by_id[i], a, form))
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
