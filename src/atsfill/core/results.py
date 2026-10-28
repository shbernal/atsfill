from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Status(StrEnum):
    READY = "ready"
    REFUSED = "refused"
    SUBMITTED = "submitted"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class FieldType(StrEnum):
    TEXT = "text"
    DATE = "date"  # answered YYYY-MM-DD
    CHOICE = "choice"
    MULTI_CHOICE = "multi_choice"
    TYPEAHEAD = "typeahead"
    FILE = "file"
    PHONE = "phone"
    URL = "url"  # the system declares it a URL; answered as an absolute http(s) URL


type Answer = str | list[str]


@dataclass(frozen=True)
class Is:
    """Holds when the field's answer is one of `values` (one of them ticked, for a
    multi_choice), or, without `values`, when the field is answered. Against values, it never
    holds for an unanswered field, negated or not."""

    field_id: str
    values: list[str] | None = None
    negated: bool = False


@dataclass(frozen=True)
class AnyOf:
    any_of: list[Condition]


@dataclass(frozen=True)
class AllOf:
    all_of: list[Condition]


type Condition = Is | AnyOf | AllOf


@dataclass(frozen=True)
class OptionSet:
    """A dependent list's options while `when` holds; `[]` leaves nothing to choose."""

    when: Condition
    options: list[str]


@dataclass(frozen=True)
class Field:
    id: str
    label: str
    type: FieldType
    required: bool
    options: list[str] | None = None
    max_length: int | None = None
    accept: list[str] | None = None
    prefilled: Answer | None = None  # the account profile's value; it answers a required field
    default: Answer | None = None  # the page's own starting value; conditions read it, not required
    unfillable: str | None = None  # why atsfill cannot fill it, a reason (ADR 0014)
    shown_when: Condition | None = None  # a follow-up: shown only when it holds (ADR 0005)
    required_when: Condition | None = None  # required once shown when it holds, if not always
    one_of: str | None = None  # an alternative: one of the fields with this key answers (ADR 0005)
    option_sets: list[OptionSet] | None = None  # a dependent list's; `options` where none holds
    min_length: int | None = None  # a text the system refuses shorter


@dataclass(frozen=True)
class Check:
    field_id: str
    answer: Answer | None
    read_back: Answer | None
    match: bool


@dataclass(frozen=True)
class SideEffect:
    step: str  # load | fill | upload | submit | sign_up | profile
    reaches: str  # hiring_system | employer
    effect: str


@dataclass
class Result:
    status: Status
    url: str
    system: str | None = None
    reason: str | None = None  # `code` or `code: detail`, the code in snake_case
    fields: list[Field] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    side_effects: list[SideEffect] = field(default_factory=list)
    evidence: str | None = None
    saved: list[str] = field(default_factory=list)  # the profile steps a `profile` run saved
    unloaded: list[str] = field(default_factory=list)  # steps a profile read could not load yet
    notice: str | None = None  # telemetry's, the first time it is on (ADR 0016)


@dataclass
class SessionReport:
    user: str
    adapter: str
    scope: str | None  # the account scope, None for an account that covers the whole system
    session: str  # signed_in | signed_out | missing
    path: Path
    status: str | None = None  # a login's outcome: signed_in | refused | blocked | unsupported
    reason: str | None = None  # why a login stored nothing, `code` or `code: detail`
    password: str | None = None  # after `--create`: stored | deleted, or kept


@dataclass
class AdapterStatus:
    name: str
    submit_allowed: list[str]  # the users whose config lets it submit
    side_effects: list[SideEffect]
    side_effects_muted: list[str]  # the users who muted them
    scope: str | None = None  # supported | in_scope | out_of_scope, None when not in the registry


@dataclass
class StatusReport:
    version: str
    data_dir: Path
    config: Path
    browser: str  # the installed build
    browser_pinned: str
    secret_store: str  # the backend generated passwords go to, `file` without one
    users: list[str]
    adapters: list[AdapterStatus]
    sessions: list[SessionReport]  # every stored one, of every user
