"""Writes the docs site's run history (ADR 0016) from PostHog's `run` events: docs/run-history.md
and one compact SVG per hiring system, docs/run-history/<system>.svg. Run by the docs deploy with
POSTHOG_READ_KEY; standard library only, so it runs in the docs group's environment.

`--from <file>` reads a recorded query result instead of querying PostHog."""

import json
import os
import subprocess
import sys
import tomllib
import urllib.request
from dataclasses import dataclass
from html import escape
from pathlib import Path

ROOT = Path(__file__).parents[1]
REGISTRY = ROOT / "src/atsfill/registry.toml"
PAGE = ROOT / "docs/run-history.md"
SVGS = ROOT / "docs/run-history"
QUERY_URL = "https://eu.posthog.com/api/projects/324467/query/"
PYPI_URL = "https://pypi.org/pypi/atsfill/json"
MAIN = "origin/main"
WINDOW_DAYS = 30
ROW_LIMIT = 50_000  # HogQL's cap per query
HTTP_TIMEOUT_SECONDS = 30
COMMANDS = ("fill", "send")  # `read` is sent, not drawn
DRAWN = ("pass", "fail")
MAX_MARKS = 30  # per run history, newest first

QUERY = f"""
select properties.system, properties.command, properties.verdict, properties.posting,
       properties.version, properties.source, properties.commit, toString(timestamp)
from events
where event = 'run' and timestamp > now() - interval {WINDOW_DAYS} day
order by timestamp
limit {ROW_LIMIT}
"""


@dataclass(frozen=True)
class Run:
    system: str
    command: str
    verdict: str
    posting: str
    version: str
    source: str  # dev | user
    commit: str | None
    timestamp: str  # ISO 8601, UTC


def query(key: str, hogql: str = QUERY) -> list[list]:
    """The window's `run` events, oldest first, as PostHog's query API returns its rows."""
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": hogql}}).encode()
    request = urllib.request.Request(
        QUERY_URL,
        body,
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.load(response)["results"]


def runs(rows: list[list]) -> list[Run]:
    return [Run(*row) for row in rows if all(row[:6])]


def systems() -> list[str]:
    """Every hiring system with an adapter, by the name its events carry."""
    sources = tomllib.loads(REGISTRY.read_text()).values()
    return sorted({s["adapter"] for s in sources if "adapter" in s})


def releases() -> set[str]:
    with urllib.request.urlopen(PYPI_URL, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return set(json.load(response)["releases"])


def on_main() -> set[str]:
    out = subprocess.run(["git", "rev-list", MAIN], capture_output=True, text=True, cwd=ROOT)
    return set(out.stdout.split())


def known(events: list[Run], systems: set[str], releases: set[str], commits: set[str]) -> list[Run]:
    """The runs the run history trusts: a known system and command, a user run on a release, a
    dev run on a commit of `main`. Anyone holding the project key can write events."""
    return [
        r
        for r in events
        if r.system in systems
        and r.command in ("read", *COMMANDS)
        and (r.commit in commits if r.source == "dev" else r.source == "user")
        and (r.source == "dev" or r.version in releases)
    ]


def marks(events: list[Run], system: str, command: str) -> list[Run]:
    """One per posting and version, its latest run, newest first; only `pass` and `fail`."""
    latest: dict[tuple[str, str], Run] = {}
    for r in sorted(events, key=lambda r: r.timestamp):
        if r.system == system and r.command == command:
            latest[(r.posting, r.version)] = r
    drawn = [r for r in latest.values() if r.verdict in DRAWN]
    return sorted(drawn, key=lambda r: r.timestamp, reverse=True)[:MAX_MARKS]


GREEN, RED, MUTED = "#9ece6a", "#f7768e", "#8b90a8"
LABEL_WIDTH, COMMAND_WIDTH, STEP, RADIUS, ROW_HEIGHT, PAD = 112, 36, 11, 4, 16, 4


def _mark(r: Run, x: int, y: int) -> str:
    color = GREEN if r.verdict == "pass" else RED
    hollow = f'fill="none" stroke="{color}" stroke-width="1.5"'
    fill = f'fill="{color}"' if r.source == "user" else hollow
    dev = f" · dev {r.commit[:7]}" if r.commit else ""
    title = f"{r.verdict} · {r.version}{dev} · {r.timestamp[:10]}"
    return f'<circle cx="{x}" cy="{y}" r="{RADIUS}" {fill}><title>{escape(title)}</title></circle>'


def svg(system: str, events: list[Run], label: bool = True) -> str:
    """A system's fill and send run histories, one row each, after its name with `label`."""
    x0 = LABEL_WIDTH if label else 0
    width = x0 + COMMAND_WIDTH + MAX_MARKS * STEP + PAD
    height = PAD * 2 + ROW_HEIGHT * len(COMMANDS)
    font = 'font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="11"'
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"'
        f' viewBox="0 0 {width} {height}" role="img" aria-label="{system} run history">',
        f"<title>{system} run history</title>",
    ]
    if label:
        y = PAD + ROW_HEIGHT
        parts.append(f'<text x="0" y="{y}" {font} font-weight="600" fill="{MUTED}">{system}</text>')
    for i, command in enumerate(COMMANDS):
        y = PAD + ROW_HEIGHT * i + ROW_HEIGHT // 2
        parts.append(f'<text x="{x0}" y="{y + 4}" {font} fill="{MUTED}">{command}</text>')
        start = x0 + COMMAND_WIDTH + RADIUS
        drawn = marks(events, system, command)
        if not drawn:
            end = start + (MAX_MARKS - 1) * STEP
            parts.append(
                f'<line x1="{start}" y1="{y}" x2="{end}" y2="{y}" stroke="{MUTED}"'
                ' stroke-width="1" stroke-dasharray="2 4" opacity="0.6"/>'
            )
        parts += [_mark(r, start + n * STEP, y) for n, r in enumerate(drawn)]
    return "\n".join([*parts, "</svg>", ""])


def page(names: list[str], events: list[Run]) -> str:
    return "\n".join(
        [
            "# Run history",
            "",
            f"Whether each hiring system's adapter works today, from the last {WINDOW_DAYS} days"
            " of [anonymous runs](telemetry.md). One mark per posting and atsfill version, at its"
            " latest run, newest first: green when the form filled (`fill`) or the application"
            " went through (`send`), red when it failed. Filled marks are users' runs, hollow"
            " ones the maintainer's. Refusals from the user's own input and skipped postings are"
            " not drawn. Hover a mark for its version and date.",
            "",
            "The run history is best-effort: anyone can send an event.",
            "",
            "| Hiring system | Run history |",
            "|---|---|",
            *(f"| [{n}](adapters/{n}.md) | {_inline(svg(n, events, False))} |" for n in names),
            "",
        ]
    )


def _inline(svg: str) -> str:
    """On one line, for a table cell; inline, so its marks' titles show on hover."""
    return svg.replace("\n", "")


def write(names: list[str], events: list[Run]) -> None:
    SVGS.mkdir(exist_ok=True)
    for name in names:
        SVGS.joinpath(f"{name}.svg").write_text(svg(name, events))
    PAGE.write_text(page(names, events))


def main(argv: list[str]) -> None:
    if argv[:1] == ["--from"]:
        rows = json.loads(Path(argv[1]).read_text())
    else:
        rows = query(os.environ["POSTHOG_READ_KEY"])
    names = systems()
    write(names, known(runs(rows), set(names), releases(), on_main()))


if __name__ == "__main__":
    main(sys.argv[1:])
