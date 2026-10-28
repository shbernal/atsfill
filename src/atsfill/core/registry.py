"""The committed registry of freehire sources (ADR 0012)."""

import datetime
import tomllib
from dataclasses import dataclass, fields
from datetime import date
from functools import cache
from importlib.resources import files
from urllib.parse import urlsplit

KINDS = ("ats", "board", "aggregator", "custom")
ACCOUNTS = ("none", "to_send", "to_read")  # also the backlog order (ADR 0013)
HOSTING = ("hosted", "embedded")
VENDORS = ("DataDome", "Cloudflare", "Akamai", "PerimeterX", "hCaptcha", "unknown")
STEPS = ("posting", "form", "upload")
SCOPES = ("out_of_scope", "in_scope", "supported")


@dataclass(frozen=True)
class Scope:
    """Where a source stands (ADR 0016). `supported`: a dev dry run passed at `commit`, never
    demoted. `in_scope`: wanted, its `reason` the known gap a round stopped at, if one did.
    `out_of_scope`: never filled, it needs the user live (ADR 0014). A `reason` names fields
    and steps, never postings."""

    value: str = "in_scope"
    date: datetime.date | None = None
    commit: str | None = None
    reason: str | None = None


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
    hosts: tuple[str, ...] = ()  # a source without an adapter: where its postings are
    share: float | None = None
    links_to: tuple[str, ...] = ()  # an aggregator's: the sources its postings link to
    block: Block | None = None
    scope: Scope = Scope()


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
    scope = dict(entry.get("scope", {"is": "in_scope"}))
    _one_of(name, "scope.is", scope.get("is"), SCOPES)
    return Source(
        name,
        entry["kind"],
        entry["account"],
        tuple(entry.get("hosting", [])),
        entry.get("adapter"),
        tuple(entry.get("hosts", [])),
        entry.get("share"),
        tuple(entry.get("links_to", [])),
        block,
        Scope(scope.pop("is"), **scope),
    )


def parse(text: str) -> dict[str, Source]:
    return {name: _source(name, entry) for name, entry in tomllib.loads(text).items()}


@cache
def sources() -> dict[str, Source]:
    return parse(files("atsfill").joinpath("registry.toml").read_text())


def _toml(value: object) -> str:
    return str(value) if isinstance(value, date) else f'"{value}"'


def with_scope(text: str, source: str, scope: Scope) -> str:
    """The registry file's text with `source`'s scope set, the rest untouched."""
    lines = text.splitlines()
    start = lines.index(f"[{source}]")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    while lines[end - 1] == "":
        end -= 1
    entry = [x for x in lines[start:end] if not x.startswith("scope =")]
    pairs = ", ".join(
        f"{'is' if f.name == 'value' else f.name} = {_toml(getattr(scope, f.name))}"
        for f in fields(scope)
        if getattr(scope, f.name) is not None
    )
    entry.append(f"scope = {{ {pairs} }}")
    return "\n".join(lines[:start] + entry + lines[end:]) + "\n"


def for_adapter(name: str) -> Source | None:
    return next((s for s in sources().values() if s.adapter == name), None)


def for_url(url: str) -> Source | None:
    """The source whose `hosts` a URL is on, a subdomain included."""
    host = urlsplit(url).hostname or ""
    on = lambda h: host == h or host.endswith(f".{h}")  # noqa: E731
    return next((s for s in sources().values() if any(map(on, s.hosts))), None)


def _backlog_key(s: Source) -> tuple:
    stopped = s.block is not None or s.scope.reason is not None
    return (stopped, ACCOUNTS.index(s.account), -(s.share or 0), s.name)


def _row(s: Source) -> str:
    share = f"{s.share:.1%}" if s.share is not None else ""
    block = f"{s.block.vendor} at {s.block.step}" if s.block else ""
    v = s.scope.value.replace("_", " ")
    if s.scope.reason:
        v += f": {s.scope.reason}"
    if s.adapter:
        v = f"[{v}]({s.adapter}.md)"
    kind = f"{s.kind} → {', '.join(s.links_to)}" if s.links_to else s.kind
    cells = [s.name, kind, share, s.account, ", ".join(s.hosting), block, v]
    return "| " + " | ".join(cells) + " |"


def backlog(registry: dict[str, Source] | None = None) -> str:
    """`docs/adapters/README.md`: the systems in backlog order (ADR 0013)."""
    registry = sources() if registry is None else registry
    columns = ("Source", "Kind", "Share", "Account", "Hosting", "Block", "Scope")
    head = f"| {' | '.join(columns)} |\n" + "|---" * len(columns) + "|"
    wanted = lambda s: s.kind in ("ats", "board") and s.scope.value != "out_of_scope"  # noqa: E731
    adapters = sorted((s for s in registry.values() if wanted(s)), key=_backlog_key)
    others = sorted((s for s in registry.values() if not wanted(s)), key=_backlog_key)
    text = [
        "# Hiring systems",
        "",
        "Generated from [src/atsfill/registry.toml](../../src/atsfill/registry.toml) by"
        " `uv run python -m scripts.backlog`. The registry is described in"
        " [ADR 0012](../adr/0012-systems-registry.md), the order in"
        " [ADR 0013](../adr/0013-survey-and-adapter-rounds.md): no account first, blocked and"
        " stopped by a known gap last, then by share of postings.",
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
COLORS = {"supported": "9ece6a", "in_scope": "e0af68", "out_of_scope": "565f89"}


def _badge(s: Source) -> str:
    message = " ".join(str(x) for x in (s.scope.value.replace("_", " "), s.scope.date) if x)
    color = COLORS[s.scope.value]
    escape = lambda t: t.replace("-", "--").replace("_", "__").replace(" ", "%20")  # noqa: E731
    url = f"https://img.shields.io/badge/{escape(s.adapter or s.name)}-{escape(message)}-{color}"
    return f"![{s.adapter}: {message}]({url})"


def with_badges(readme: str, registry: dict[str, Source] | None = None) -> str:
    """The README with one badge per adapter between its badge markers."""
    registry = sources() if registry is None else registry
    start, end = readme.index(BADGES[0]) + len(BADGES[0]), readme.index(BADGES[1])
    badges = " ".join(_badge(s) for s in sorted(registry.values(), key=_backlog_key) if s.adapter)
    return f"{readme[:start]}\n{badges}\n{readme[end:]}"
