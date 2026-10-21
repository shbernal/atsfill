"""The committed registry of freehire sources (ADR 0012)."""

import tomllib
from dataclasses import dataclass, fields
from datetime import date
from functools import cache
from importlib.resources import files

from packaging.version import Version

KINDS = ("ats", "board", "aggregator", "custom")
ACCOUNTS = ("none", "to_send", "to_read")  # also the backlog order (ADR 0013)
HOSTING = ("hosted", "embedded")
VENDORS = ("DataDome", "Cloudflare", "Akamai", "PerimeterX", "hCaptcha", "unknown")
STEPS = ("posting", "form", "upload")
VERIFICATIONS = ("mapped", "dry_run_verified", "send_verified")
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
class Shortfall:
    """A round that ended without an adapter passing the adapter gate (ADR 0013): still wanted.
    `reason` names the known gap that stopped it, never postings."""

    date: date
    version: str
    commit: str
    by: str
    reason: str


@dataclass(frozen=True)
class OutOfScope:
    """A source atsfill will never fill: it needs the user live (ADR 0014)."""

    reason: str
    date: date


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
class Source:
    name: str
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
    unsupported: Shortfall | None = None
    out_of_scope: OutOfScope | None = None

    @property
    def verification(self) -> str | None:
        """The furthest verification reached; each one implies the ones before it."""
        return next((s for s in reversed(VERIFICATIONS) if getattr(self, s)), None)


def _one_of(name: str, key: str, value: object, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ValueError(f"{name}: {key} = {value!r}, expected one of {', '.join(allowed)}")


def _source(name: str, entry: dict) -> Source:
    known = {f.name for f in fields(Source)} - {"name"}
    if unknown := entry.keys() - known:
        raise ValueError(f"{name}: unknown keys {', '.join(sorted(unknown))}")
    _one_of(name, "kind", entry.get("kind"), KINDS)
    _one_of(name, "account", entry.get("account"), ACCOUNTS)
    for h in entry.get("hosting", []):
        _one_of(name, "hosting", h, HOSTING)
    if entry.get("links_to") and entry["kind"] != "aggregator":
        raise ValueError(f"{name}: links_to on a {entry['kind']}, only an aggregator links out")
    block = entry.get("block")
    if block is not None:
        _one_of(name, "block.vendor", block.get("vendor"), VENDORS)
        _one_of(name, "block.step", block.get("step"), STEPS)
        block = Block(**block)
    verifications = {s: Verification(**entry[s]) for s in VERIFICATIONS if s in entry}
    broken = entry.get("send_broken")
    if broken is not None:
        _one_of(name, "send_broken.cause", broken.get("cause", "unknown"), CAUSES)
        broken = Broken(**broken)
    unsupported = entry.get("unsupported")
    out_of_scope = entry.get("out_of_scope")
    return Source(
        name,
        entry["kind"],
        entry["account"],
        tuple(entry.get("hosting", [])),
        entry.get("adapter"),
        entry.get("share"),
        tuple(entry.get("links_to", [])),
        block,
        **verifications,
        send_broken=broken,
        unsupported=Shortfall(**unsupported) if unsupported is not None else None,
        out_of_scope=OutOfScope(**out_of_scope) if out_of_scope is not None else None,
    )


def parse(text: str) -> dict[str, Source]:
    return {name: _source(name, entry) for name, entry in tomllib.loads(text).items()}


@cache
def sources() -> dict[str, Source]:
    return parse(files("atsfill").joinpath("registry.toml").read_text())


def _toml(value: object) -> str:
    return str(value) if isinstance(value, date) else f'"{value}"'


def with_verification(
    text: str, source: str, key: str, value: Verification | Broken | Shortfall
) -> str:
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
    a, b = (Version(Version(v).public) for v in (verified, installed))
    return "this_release" if a == b else "older" if a < b else "newer"


def for_adapter(name: str) -> Source | None:
    return next((s for s in sources().values() if s.adapter == name), None)


def _backlog_key(s: Source) -> tuple:
    stopped = s.block is not None or s.unsupported is not None
    return (stopped, ACCOUNTS.index(s.account), -(s.share or 0), s.name)


def _row(s: Source) -> str:
    share = f"{s.share:.1%}" if s.share is not None else ""
    block = f"{s.block.vendor} at {s.block.step}" if s.block else ""
    v = "send_broken" if s.send_broken else "unsupported" if s.unsupported else s.verification or ""
    if s.out_of_scope:
        v = f"out of scope: {s.out_of_scope.reason}"
    if s.adapter:
        v = f"[{v}]({s.adapter}.md)" if v else f"[adapter]({s.adapter}.md)"
    kind = f"{s.kind} → {', '.join(s.links_to)}" if s.links_to else s.kind
    cells = [s.name, kind, share, s.account, ", ".join(s.hosting), block, v]
    return "| " + " | ".join(cells) + " |"


def backlog(registry: dict[str, Source] | None = None) -> str:
    """`docs/adapters/README.md`: the systems in backlog order (ADR 0013)."""
    registry = sources() if registry is None else registry
    columns = ("Source", "Kind", "Share", "Account", "Hosting", "Block", "Verification")
    head = f"| {' | '.join(columns)} |\n" + "|---" * len(columns) + "|"
    wanted = lambda s: s.kind in ("ats", "board") and not s.out_of_scope  # noqa: E731
    adapters = sorted((s for s in registry.values() if wanted(s)), key=_backlog_key)
    others = sorted((s for s in registry.values() if not wanted(s)), key=_backlog_key)
    text = [
        "# Hiring systems",
        "",
        "Generated from [src/atsfill/registry.toml](../../src/atsfill/registry.toml) by"
        " `uv run python -m scripts.backlog`. The registry is described in"
        " [ADR 0012](../adr/0012-systems-registry.md), the order in"
        " [ADR 0013](../adr/0013-survey-and-adapter-rounds.md): no account first, blocked and"
        " unsupported last, then by share of postings.",
        "",
        f"The registry covers {sum(s.share or 0 for s in registry.values()):.1%} of postings.",
        "",
        head,
        *map(_row, adapters),
    ]
    if others:
        text += [
            "",
            "Aggregators, employer-built forms and out-of-scope systems"
            " ([ADR 0014](../adr/0014-out-of-scope.md)), counted and not adapted:",
            "",
            head,
        ]
        text += map(_row, others)
    return "\n".join(text) + "\n"


BADGES = ("<!-- badges -->", "<!-- /badges -->")
COLORS = {"send_verified": "9ece6a", "dry_run_verified": "7aa2f7", "mapped": "a9b1d6"}


def _badge(s: Source) -> str:
    if s.send_broken:
        message, color = f"send broken {s.send_broken.date}", "f7768e"
    elif s.unsupported:
        message, color = f"unsupported {s.unsupported.date}", "e0af68"
    elif s.verification:
        v: Verification = getattr(s, s.verification)
        message, color = f"{s.verification.replace('_', ' ')} {v.date}", COLORS[s.verification]
    else:
        message, color = "unverified", "565f89"
    escape = lambda t: t.replace("-", "--").replace("_", "__").replace(" ", "%20")  # noqa: E731
    url = f"https://img.shields.io/badge/{escape(s.adapter or s.name)}-{escape(message)}-{color}"
    return f"![{s.adapter}: {message}]({url})"


def with_badges(readme: str, registry: dict[str, Source] | None = None) -> str:
    """The README with one badge per adapter between its badge markers."""
    registry = sources() if registry is None else registry
    start, end = readme.index(BADGES[0]) + len(BADGES[0]), readme.index(BADGES[1])
    badges = " ".join(_badge(s) for s in sorted(registry.values(), key=_backlog_key) if s.adapter)
    return f"{readme[:start]}\n{badges}\n{readme[end:]}"
