"""Whether a field shows, and whether it is required, from the answers alone (ADR 0005)."""

from atsfill.core.results import AllOf, Answer, AnyOf, Condition, Field, Is, OptionSet


def _parents(condition: Condition) -> list[str]:
    match condition:
        case Is():
            return [condition.field_id]
        case AnyOf():
            return [p for c in condition.any_of for p in _parents(c)]
        case AllOf():
            return [p for c in condition.all_of for p in _parents(c)]


def alternatives(fields: list[Field]) -> dict[str, list[Field]]:
    """The fields of each `one_of` group, by its key."""
    out: dict[str, list[Field]] = {}
    for f in fields:
        if f.one_of:
            out.setdefault(f.one_of, []).append(f)
    return out


def faults(fields: list[Field]) -> list[str]:
    """What makes the fields' conditions unusable: a field they name that is not listed, a
    field shown by its own answer through any chain, options that follow their own field, a
    group of alternatives with one member or members disagreeing on `required`."""
    by_id = {f.id: f for f in fields}
    out = [
        f"{f.id}: its condition names no field {p}"
        for f in fields
        for c in (f.shown_when, f.required_when, *(s.when for s in f.option_sets or []))
        if c
        for p in _parents(c)
        if p not in by_id
    ]
    out += [
        f"{f.id}: its options follow its own answer"
        for f in fields
        if any(f.id in _parents(s.when) for s in f.option_sets or [])
    ]

    def loops(field_id: str, seen: tuple[str, ...]) -> bool:
        if field_id in seen:
            return True
        field = by_id.get(field_id)
        parents = _parents(field.shown_when) if field and field.shown_when else []
        return any(loops(p, (*seen, field_id)) for p in parents)

    out += [f"{f.id}: shown by its own answer" for f in fields if loops(f.id, ())]
    for key, members in alternatives(fields).items():
        if len(members) == 1:
            out.append(f"{key}: one alternative, {members[0].id}")
        elif len({m.required for m in members}) > 1:
            out.append(f"{key}: alternatives disagree on required")
    return out


class Form:
    """The fields under one set of answers. A field's value is its answer, else what the account
    prefilled, else the page's default; a field that stays hidden has none."""

    def __init__(self, fields: list[Field], answers: dict[str, Answer]) -> None:
        self.by_id = {f.id: f for f in fields}
        self.groups = alternatives(fields)
        self.answers = answers

    def holds(self, condition: Condition) -> bool:
        match condition:
            case Is():
                value = self._value(condition.field_id)
                if condition.values is None:
                    return (value is not None) != condition.negated
                if value is None:
                    return False
                picks = [value] if isinstance(value, str) else value
                return any(p in condition.values for p in picks) != condition.negated
            case AnyOf():
                return any(self.holds(c) for c in condition.any_of)
            case AllOf():
                return all(self.holds(c) for c in condition.all_of)

    def shown(self, field: Field) -> bool:
        return field.shown_when is None or self.holds(field.shown_when)

    def required(self, field: Field) -> bool:
        """Required as the form asks it: shown, and always required or required by an answer.
        An alternative never is on its own: its group is (`group_required`)."""
        return field.one_of is None and self.asked(field)

    def group_required(self, key: str) -> bool:
        return any(self.asked(m) for m in self.groups[key])

    def asked(self, field: Field) -> bool:
        """The field's own flag and conditions, whatever its group."""
        if not self.shown(field):
            return False
        when = field.required_when
        return field.required or (when is not None and self.holds(when))

    def option_set(self, field: Field) -> OptionSet | None:
        """The first of a dependent list's sets that holds under the answers."""
        return next((s for s in field.option_sets or [] if self.holds(s.when)), None)

    def options(self, field: Field) -> list[str] | None:
        held = self.option_set(field)
        return field.options if held is None else held.options

    def _value(self, field_id: str) -> Answer | None:
        field = self.by_id[field_id]
        if not self.shown(field):
            return None
        for value in (self.answers.get(field_id), field.prefilled, field.default):
            if value not in (None, "", []):
                return value
        return None


def describe(condition: Condition, by_id: dict[str, Field]) -> str:
    """The condition in words, each field by its label: `"Visa" is "Yes"`."""
    match condition:
        case Is():
            label = by_id[condition.field_id].label if condition.field_id in by_id else ""
            name = f'"{label or condition.field_id}"'
            if condition.values is None:
                return f"{name} is {'not ' if condition.negated else ''}answered"
            values = " or ".join(f'"{v}"' for v in condition.values)
            return f"{name} is {'not ' if condition.negated else ''}{values}"
        case AnyOf():
            return " or ".join(f"({describe(c, by_id)})" for c in condition.any_of)
        case AllOf():
            return " and ".join(f"({describe(c, by_id)})" for c in condition.all_of)
