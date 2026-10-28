"""Regressions (ADR 0016): a posting whose `fill` or `send` passed, then failed, within the run
history's window. The same build on both sides points at the hiring system, a newer one at the
code. A regression ends when the posting passes again on another build, or leaves the window.

    uv run python -m scripts.regressions --sync               # the docs deploy
    uv run python -m scripts.regressions <system> <command>   # locally, never in CI

`--sync` keeps one open `regression` issue per hiring system and command, with no posting hash,
through `gh`; standard library only, so it runs in the docs group's environment. The local form
prints the postings behind one, with their URLs, using POSTHOG_READ_KEY from `.env`.
`--from <file>` reads a recorded query result instead of querying PostHog."""

import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from scripts import run_history
from scripts.run_history import COMMANDS, DRAWN, ROOT, WINDOW_DAYS, Run

LABEL = "regression"
ISSUE_LIMIT = 200
FREEHIRE_PAGE = 100  # freehire's cap per search
FREEHIRE_PAGES = 50  # per source, before the rest is printed as unresolved

URLS = f"""
select properties.posting, any(properties.url)
from events
where event in ('run', 'report') and properties.system = '{{system}}'
  and properties.url != '' and timestamp > now() - interval {WINDOW_DAYS} day
group by properties.posting
"""


def build(r: Run) -> str:
    return f"{r.version} (dev {r.commit[:7]})" if r.commit else r.version


@dataclass(frozen=True)
class Regression:
    system: str
    command: str
    posting: str
    passed: Run  # the last pass before the failure
    failed: Run  # the first failure after it
    ended: Run | None = None  # the pass, on another build, that ended it

    @property
    def points_at(self) -> str:
        return "the hiring system" if build(self.passed) == build(self.failed) else "the code"


def regressions(events: list[Run]) -> list[Regression]:
    """Every regression in the window, ended or not, oldest posting run first."""
    postings: dict[tuple[str, str, str], list[Run]] = {}
    for r in sorted(events, key=lambda r: r.timestamp):
        if r.command in COMMANDS and r.verdict in DRAWN:
            postings.setdefault((r.system, r.command, r.posting), []).append(r)
    found = []
    for (system, command, posting), runs in postings.items():
        passed, current = None, None
        for r in runs:
            if r.verdict == "fail":
                if current is None and passed:
                    current = Regression(system, command, posting, passed, r)
            elif current is None:
                passed = r
            elif build(r) != build(current.failed):  # a pass on the failing build is a flake
                found.append(replace(current, ended=r))
                current, passed = None, r
        if current:
            found.append(current)
    return found


def title(system: str, command: str) -> str:
    return f"Regression: {system} {command}"


def body(active: list[Regression]) -> str:
    system, command = active[0].system, active[0].command
    pairs = Counter((build(r.passed), build(r.failed), r.points_at) for r in active)
    return "\n".join(
        [
            f"`{system}` `{command}`: {len(active)} posting{'s' * (len(active) > 1)} passed,"
            f" then failed, in the last {WINDOW_DAYS} days.",
            "",
            "| Passed on | Failed on | Postings | Points at |",
            "|---|---|---|---|",
            *(f"| {p} | {f} | {n} | {at} |" for (p, f, at), n in pairs.most_common()),
            "",
            "The postings, with their URLs, locally:",
            "",
            f"    uv run python -m scripts.regressions {system} {command}",
            "",
        ]
    )


def ending(ended: list[Regression]) -> str:
    if not ended:
        return f"Ended: the failing runs left the {WINDOW_DAYS}-day window."
    builds = sorted({build(r.ended) for r in ended if r.ended})
    return f"Ended: the postings pass again, on {', '.join(builds)}."


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str


@dataclass(frozen=True)
class Action:
    kind: str  # open | edit | close
    title: str
    text: str  # the body, or the closing comment
    number: int | None = None


def actions(found: list[Regression], issues: list[Issue]) -> list[Action]:
    """What brings the open `regression` issues in line with the window's regressions."""
    active: dict[str, list[Regression]] = {}
    ended: dict[str, list[Regression]] = {}
    for r in found:
        (ended if r.ended else active).setdefault(title(r.system, r.command), []).append(r)
    open_ = {i.title: i for i in issues}
    out = []
    for t, regs in sorted(active.items()):
        text = body(regs)
        if t not in open_:
            out.append(Action("open", t, text))
        elif open_[t].body.strip() != text.strip():
            out.append(Action("edit", t, text, open_[t].number))
    for t, issue in sorted(open_.items()):
        if t not in active:
            out.append(Action("close", t, ending(ended.get(t, [])), issue.number))
    return out


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def sync(found: list[Regression]) -> None:
    _gh("label", "create", LABEL, "--force", "--color", "f7768e",
        "--description", "A posting passed, then failed (ADR 0016)")  # fmt: skip
    listed = _gh("issue", "list", "--label", LABEL, "--state", "open",
                 "--json", "number,title,body", "--limit", str(ISSUE_LIMIT))  # fmt: skip
    for a in actions(found, [Issue(**i) for i in json.loads(listed)]):
        if a.kind == "open":
            _gh("issue", "create", "--title", a.title, "--body", a.text, "--label", LABEL)
        elif a.kind == "edit":
            _gh("issue", "edit", str(a.number), "--body", a.text)
        else:
            _gh("issue", "close", str(a.number), "--comment", a.text)
        print(f"{a.kind} {a.title}")


def _env_key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "POSTHOG_READ_KEY":
            return value.strip().strip("\"'")
    sys.exit("POSTHOG_READ_KEY is not in .env")


def _freehire(system: str, hashes: set[str]) -> dict[str, str]:
    """The URLs of `hashes` among freehire's postings of the sources `system` handles."""
    from atsfill.adapters import adapters
    from atsfill.core import registry
    from atsfill.core.telemetry import posting_hash

    adapter = next(a for a in adapters() if a.name == system)
    found: dict[str, str] = {}
    for source in (s for s in registry.sources().values() if s.adapter == system):
        for page in range(FREEHIRE_PAGES):
            run = subprocess.run(
                ["freehire", "--json", "search", "", "--facet", f"source={source.name}",
                 "--limit", str(FREEHIRE_PAGE), "--offset", str(page * FREEHIRE_PAGE)],
                capture_output=True, text=True, check=True,
            )  # fmt: skip
            jobs = json.loads(run.stdout) or []
            for job in jobs:
                h = posting_hash(adapter.posting(job["url"]))
                if h in hashes:
                    found[h] = job["url"]
            if not jobs or hashes <= found.keys():
                return found
    return found


def show(found: list[Regression], system: str, command: str, key: str) -> None:
    active = [r for r in found if (r.system, r.command) == (system, command) and not r.ended]
    if not active:
        print(f"No regression on {system} {command} in the last {WINDOW_DAYS} days.")
        return
    urls = dict(run_history.query(key, URLS.format(system=system)))
    missing = {r.posting for r in active} - urls.keys()
    urls |= _freehire(system, missing) if missing else {}
    for r in active:
        url = urls.get(r.posting, f"unresolved, usually closed ({r.posting[:12]})")
        print(f"{url}\n  passed on {build(r.passed)}, failed on {build(r.failed)}: {r.points_at}")


def main(argv: list[str]) -> None:
    rows = None
    if argv[:1] == ["--from"]:
        rows, argv = json.loads(Path(argv[1]).read_text()), argv[2:]
    if argv == ["--sync"]:
        key = os.environ.get("POSTHOG_READ_KEY", "")
    elif len(argv) == 2 and argv[0] in run_history.systems() and argv[1] in COMMANDS:
        key = _env_key()
    else:
        sys.exit(__doc__)
    if rows is None:
        rows = run_history.query(key or sys.exit("POSTHOG_READ_KEY is not set"))
    names = set(run_history.systems())
    events = run_history.known(
        run_history.runs(rows), names, run_history.releases(), run_history.on_main()
    )
    found = regressions(events)
    if argv == ["--sync"]:
        sync(found)
    else:
        show(found, argv[0], argv[1], key)


if __name__ == "__main__":
    main(sys.argv[1:])
