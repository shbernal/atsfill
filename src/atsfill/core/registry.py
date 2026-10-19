"""The committed registry of hiring systems (ADR 0012)."""

import tomllib
from dataclasses import dataclass, fields
from datetime import date
from functools import cache
from importlib.resources import files

KINDS = ("ats", "board", "aggregator", "custom")
ACCOUNTS = ("none", "to_send", "to_read")  # also the backlog order (ADR 0013)
HOSTING = ("hosted", "embedded")
VENDORS = ("DataDome", "Cloudflare", "Akamai", "PerimeterX", "unknown")
STEPS = ("posting", "form", "upload")
STATES = ("mapped", "dry_run_verified", "send_verified")


@dataclass(frozen=True)
class Verification:
    date: date
    version: str
    by: str


@dataclass(frozen=True)
class Block:
    vendor: str
    signal: str
    step: str
    date: date
    version: str
    camoufox: str
    browser: str


@dataclass(frozen=True)
class System:
    source: str
    kind: str
    account: str
    hosting: tuple[str, ...]
    adapter: str | None = None
    share: float | None = None
    block: Block | None = None
    mapped: Verification | None = None
    dry_run_verified: Verification | None = None
    send_verified: Verification | None = None

    @property
    def state(self) -> str | None:
        """The furthest state reached; each one implies the ones before it."""
        return next((s for s in reversed(STATES) if getattr(self, s)), None)


def _one_of(source: str, key: str, value: object, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ValueError(f"{source}: {key} = {value!r}, expected one of {', '.join(allowed)}")


def _system(source: str, entry: dict) -> System:
    known = {f.name for f in fields(System)} - {"source"}
    if unknown := entry.keys() - known:
        raise ValueError(f"{source}: unknown keys {', '.join(sorted(unknown))}")
    _one_of(source, "kind", entry.get("kind"), KINDS)
    _one_of(source, "account", entry.get("account"), ACCOUNTS)
    for h in entry.get("hosting", []):
        _one_of(source, "hosting", h, HOSTING)
    block = entry.get("block")
    if block is not None:
        _one_of(source, "block.vendor", block.get("vendor"), VENDORS)
        _one_of(source, "block.step", block.get("step"), STEPS)
        block = Block(**block)
    states = {s: Verification(**entry[s]) for s in STATES if s in entry}
    return System(
        source,
        entry["kind"],
        entry["account"],
        tuple(entry.get("hosting", [])),
        entry.get("adapter"),
        entry.get("share"),
        block,
        **states,
    )


def parse(text: str) -> dict[str, System]:
    return {source: _system(source, entry) for source, entry in tomllib.loads(text).items()}


@cache
def systems() -> dict[str, System]:
    return parse(files("atsfill").joinpath("registry.toml").read_text())


def for_adapter(name: str) -> System | None:
    return next((s for s in systems().values() if s.adapter == name), None)


def _backlog_key(s: System) -> tuple:
    return (s.block is not None, ACCOUNTS.index(s.account), -(s.share or 0), s.source)


def _row(s: System) -> str:
    share = f"{s.share:.1%}" if s.share is not None else ""
    block = f"{s.block.vendor} at {s.block.step}" if s.block else ""
    state = s.state or ""
    if s.adapter:
        state = f"[{state}]({s.adapter}.md)" if state else f"[adapter]({s.adapter}.md)"
    cells = [s.source, s.kind, share, s.account, ", ".join(s.hosting), block, state]
    return "| " + " | ".join(cells) + " |"


def backlog(registry: dict[str, System] | None = None) -> str:
    """`docs/adapters/README.md`: the systems in backlog order (ADR 0013)."""
    registry = systems() if registry is None else registry
    head = "| Source | Kind | Share | Account | Hosting | Block | State |\n" + "|---" * 7 + "|"
    adapters = sorted(
        (s for s in registry.values() if s.kind in ("ats", "board")), key=_backlog_key
    )
    others = sorted(
        (s for s in registry.values() if s.kind not in ("ats", "board")), key=_backlog_key
    )
    text = [
        "# Hiring systems",
        "",
        "Generated from [src/atsfill/registry.toml](../../src/atsfill/registry.toml) by"
        " `uv run scripts/backlog.py`. The registry is described in"
        " [ADR 0012](../adr/0012-systems-registry.md), the order in"
        " [ADR 0013](../adr/0013-survey-and-adapter-rounds.md): no account first, blocked last,"
        " then by share of postings.",
        "",
        head,
        *map(_row, adapters),
    ]
    if others:
        text += ["", "Aggregators and company-built forms, counted and not adapted:", "", head]
        text += map(_row, others)
    return "\n".join(text) + "\n"
