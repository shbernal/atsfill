"""The committed registry of hiring systems (ADR 0012)."""

import tomllib
from dataclasses import dataclass, fields
from datetime import date
from functools import cache
from importlib.resources import files

KINDS = ("ats", "board", "aggregator", "custom")
ACCOUNTS = ("none", "to_send", "to_read")  # also the backlog order (ADR 0013)
HOSTING = ("hosted", "embedded")
VENDORS = ("DataDome", "Cloudflare", "Akamai", "PerimeterX", "hCaptcha", "unknown")
STEPS = ("posting", "form", "upload")
STATES = ("mapped", "dry_run_verified", "send_verified")
CAUSES = ("system", "code", "unknown")
RELEASES = ("this_release", "older", "newer")


@dataclass(frozen=True)
class Verification:
    date: date
    version: str  # the release, compared with the installed one
    by: str
    commit: str | None = None  # the exact code


@dataclass(frozen=True)
class Broken:
    """A send that worked fails now (ADR 0012); `reason` names fields and steps, never postings."""

    date: date
    version: str
    commit: str
    by: str
    reason: str
    cause: str = "unknown"


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
    links_to: tuple[str, ...] = ()  # an aggregator's: the sources its postings link to
    block: Block | None = None
    mapped: Verification | None = None
    dry_run_verified: Verification | None = None
    send_verified: Verification | None = None  # the last known good, kept through a break
    send_broken: Broken | None = None

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
    if entry.get("links_to") and entry["kind"] != "aggregator":
        raise ValueError(f"{source}: links_to on a {entry['kind']}, only an aggregator links out")
    block = entry.get("block")
    if block is not None:
        _one_of(source, "block.vendor", block.get("vendor"), VENDORS)
        _one_of(source, "block.step", block.get("step"), STEPS)
        block = Block(**block)
    states = {s: Verification(**entry[s]) for s in STATES if s in entry}
    broken = entry.get("send_broken")
    if broken is not None:
        _one_of(source, "send_broken.cause", broken.get("cause", "unknown"), CAUSES)
        broken = Broken(**broken)
    return System(
        source,
        entry["kind"],
        entry["account"],
        tuple(entry.get("hosting", [])),
        entry.get("adapter"),
        entry.get("share"),
        tuple(entry.get("links_to", [])),
        block,
        **states,
        send_broken=broken,
    )


def parse(text: str) -> dict[str, System]:
    return {source: _system(source, entry) for source, entry in tomllib.loads(text).items()}


@cache
def systems() -> dict[str, System]:
    return parse(files("atsfill").joinpath("registry.toml").read_text())


def _toml(value: object) -> str:
    return str(value) if isinstance(value, date) else f'"{value}"'


def with_state(text: str, source: str, key: str, value: Verification | Broken) -> str:
    """The registry file's text with `key` of `source` set to `value`, the rest untouched.
    Writing `send_verified` ends a `send_broken`."""
    lines = text.splitlines()
    start = lines.index(f"[{source}]")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    while lines[end - 1] == "":
        end -= 1
    gone = {key, "send_broken"} if key == "send_verified" else {key}
    entry = lines[start:end]
    at = next((i for i, x in enumerate(entry) if x.split(" =")[0] in gone), len(entry))
    entry = [x for x in entry if x.split(" =")[0] not in gone]
    pairs = ", ".join(
        f"{f.name} = {_toml(getattr(value, f.name))}"
        for f in fields(value)
        if getattr(value, f.name) is not None
    )
    entry.insert(min(at, len(entry)), f"{key} = {{ {pairs} }}")
    return "\n".join(lines[:start] + entry + lines[end:]) + "\n"


def release(verified: str, installed: str) -> str:
    """Whether a verified release is the installed one, `older` or `newer`."""
    as_tuple = lambda v: tuple(int(n) for n in v.split("+")[0].split("."))  # noqa: E731
    a, b = as_tuple(verified), as_tuple(installed)
    return "this_release" if a == b else "older" if a < b else "newer"


def for_adapter(name: str) -> System | None:
    return next((s for s in systems().values() if s.adapter == name), None)


def _backlog_key(s: System) -> tuple:
    return (s.block is not None, ACCOUNTS.index(s.account), -(s.share or 0), s.source)


def _row(s: System) -> str:
    share = f"{s.share:.1%}" if s.share is not None else ""
    block = f"{s.block.vendor} at {s.block.step}" if s.block else ""
    state = "send_broken" if s.send_broken else s.state or ""
    if s.adapter:
        state = f"[{state}]({s.adapter}.md)" if state else f"[adapter]({s.adapter}.md)"
    kind = f"{s.kind} → {', '.join(s.links_to)}" if s.links_to else s.kind
    cells = [s.source, kind, share, s.account, ", ".join(s.hosting), block, state]
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
        " `uv run python -m scripts.backlog`. The registry is described in"
        " [ADR 0012](../adr/0012-systems-registry.md), the order in"
        " [ADR 0013](../adr/0013-survey-and-adapter-rounds.md): no account first, blocked last,"
        " then by share of postings.",
        "",
        f"The registry covers {sum(s.share or 0 for s in registry.values()):.1%} of postings.",
        "",
        head,
        *map(_row, adapters),
    ]
    if others:
        text += ["", "Aggregators and company-built forms, counted and not adapted:", "", head]
        text += map(_row, others)
    return "\n".join(text) + "\n"


BADGES = ("<!-- badges -->", "<!-- /badges -->")
COLORS = {"send_verified": "9ece6a", "dry_run_verified": "7aa2f7", "mapped": "a9b1d6"}


def _badge(s: System) -> str:
    if s.send_broken:
        message, color = f"send broken {s.send_broken.date}", "f7768e"
    elif s.state:
        v: Verification = getattr(s, s.state)
        message, color = f"{s.state.replace('_', ' ')} {v.date}", COLORS[s.state]
    else:
        message, color = "unverified", "565f89"
    escape = lambda t: t.replace("-", "--").replace("_", "__").replace(" ", "%20")  # noqa: E731
    url = f"https://img.shields.io/badge/{escape(s.adapter or s.source)}-{escape(message)}-{color}"
    return f"![{s.adapter}: {message}]({url})"


def with_badges(readme: str, registry: dict[str, System] | None = None) -> str:
    """The README with one badge per adapter between its badge markers."""
    registry = systems() if registry is None else registry
    start, end = readme.index(BADGES[0]) + len(BADGES[0]), readme.index(BADGES[1])
    badges = " ".join(_badge(s) for s in sorted(registry.values(), key=_backlog_key) if s.adapter)
    return f"{readme[:start]}\n{badges}\n{readme[end:]}"
