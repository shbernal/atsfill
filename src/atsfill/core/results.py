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
class Field:
    id: str
    label: str
    type: FieldType
    required: bool
    options: list[str] | None = None
    max_length: int | None = None
    accept: list[str] | None = None
    prefilled: bool = False  # holds a value before any answer, from the account profile
    unfillable: str | None = None  # why atsfill cannot fill it, a reason (ADR 0014)


@dataclass(frozen=True)
class Check:
    field_id: str
    answer: Answer | None
    read_back: Answer | None
    match: bool


@dataclass(frozen=True)
class SideEffect:
    step: str  # load | fill | upload | submit | sign_up
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
    warning: str | None = None  # `code: detail`, on a ready dry run or a send that went ahead


@dataclass
class SessionReport:
    user: str
    adapter: str
    scope: str | None  # the account scope, None for an account that covers the whole system
    session: str  # signed_in | signed_out | missing
    path: Path
    status: str | None = None  # a login's outcome: signed_in | refused | blocked | unsupported
    reason: str | None = None  # why a login stored nothing, `code` or `code: detail`
    password: str | None = None  # after `--create`, the generated one: stored | deleted


@dataclass
class AdapterStatus:
    name: str
    submit_allowed: list[str]  # the users whose config lets it submit
    side_effects: list[SideEffect]
    side_effects_muted: list[str]  # the users who muted them
    verified: str | None = None  # mapped | dry_run_verified | send_verified (ADR 0012)
    verified_version: str | None = None
    verified_release: str | None = None  # this_release | older | newer than the installed one
    send_broken: str | None = None  # since when and why, with the last verified send


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
