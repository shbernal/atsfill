"""Whether a field shows, and whether it is required, from the answers alone (ADR 0005)."""

from atsfill.core.results import AllOf, Answer, AnyOf, Condition, Field, Is


def _parents(condition: Condition) -> list[str]:
    match condition:
        case Is():
            return [condition.field_id]
        case AnyOf():
            return [p for c in condition.any_of for p in _parents(c)]
        case AllOf():
            return [p for c in condition.all_of for p in _parents(c)]


def faults(fields: list[Field]) -> list[str]:
    """What makes the fields' conditions unusable: a field they name that is not listed, or a
    field shown by its own answer, through any chain."""
    by_id = {f.id: f for f in fields}
    out = [
        f"{f.id}: its condition names no field {p}"
        for f in fields
        for c in (f.shown_when, f.required_when)
        if c
        for p in _parents(c)
        if p not in by_id
    ]

    def loops(field_id: str, seen: tuple[str, ...]) -> bool:
        if field_id in seen:
            return True
        field = by_id.get(field_id)
        parents = _parents(field.shown_when) if field and field.shown_when else []
        return any(loops(p, (*seen, field_id)) for p in parents)

    out += [f"{f.id}: shown by its own answer" for f in fields if loops(f.id, ())]
    return out


class Form:
    """The fields under one set of answers: an answer to a field that stays hidden counts as
    none."""

    def __init__(self, fields: list[Field], answers: dict[str, Answer]) -> None:
        self.by_id = {f.id: f for f in fields}
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
        """Required as the form asks it: shown, and always required or required by an answer."""
        if not self.shown(field):
            return False
        when = field.required_when
        return field.required or (when is not None and self.holds(when))

    def _value(self, field_id: str) -> Answer | None:
        field = self.by_id[field_id]
        value = self.answers.get(field_id)
        return value if value not in (None, "", []) and self.shown(field) else None


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
