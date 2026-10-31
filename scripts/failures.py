"""Failures (ADR 0016): a posting whose latest `fill` or `send` failed within the run history's
window, grouped by hiring system, command and reason code. A failure ends when the posting passes
again on another build, or leaves the window. One whose posting passed before is a regression:
the same build on both sides points at the hiring system, a newer one at the code.

    uv run python -m scripts.failures --sync                       # the docs deploy, triage
    uv run python -m scripts.failures <system> <command> [reason]  # locally, never in CI

`--sync` keeps one `failure` issue per hiring system, command and reason code, with no posting
hash, through `gh`; standard library only, so it runs in the docs group's environment. An issue
closed by a fix reopens only when a build containing the fix fails the same way. The local form
prints the postings behind one, with their URLs, using POSTHOG_READ_KEY from `.env`.
`--from <file>` reads a recorded query result instead of querying PostHog."""

import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from scripts import run_history
from scripts.run_history import COMMANDS, DRAWN, MAIN, ROOT, WINDOW_DAYS, Run

LABEL = "failure"
TRIAGE_LABELS = {
    "agent:working": "An agent is working on this issue right now",
    "agent:done": "An agent opened a pull request for this issue",
    "agent:skip": (
        "An agent found this is not the code's to fix: "
        "captcha, SMS OTP, rate limit, maintainer's choice"
    ),
    "agent:stuck": "An agent tried and could not fix this, or its run was interrupted twice",
    "cannot-reproduce": "Re-run and no longer fails, or the posting is gone",
}
ISSUE_LIMIT = 200
FREEHIRE_PAGE = 100  # freehire's cap per search
FREEHIRE_PAGES = 50  # per source, before the rest is printed as unresolved
NO_REASON = "no_reason"

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
class Failure:
    system: str
    command: str
    posting: str
    fails: tuple[Run, ...]  # the failing runs since the posting last passed, oldest first
    passed: Run | None = None  # the last pass before them: a regression
    ended: Run | None = None  # the pass, on another build, that ended it

    @property
    def reason(self) -> str:
        return self.fails[-1].reason or NO_REASON

    @property
    def title(self) -> str:
        return title(self.system, self.command, self.reason)

    @property
    def points_at(self) -> str:
        if not self.passed:
            return "never passed"
        same = build(self.passed) == build(self.fails[0])
        return "the hiring system" if same else "the code"


def failures(events: list[Run]) -> list[Failure]:
    """Every failure in the window, ended or not, oldest posting run first."""
    postings: dict[tuple[str, str, str], list[Run]] = {}
    for r in sorted(events, key=lambda r: r.timestamp):
        if r.command in COMMANDS and r.verdict in DRAWN:
            postings.setdefault((r.system, r.command, r.posting), []).append(r)
    found = []
    for (system, command, posting), runs in postings.items():
        passed, current = None, None
        for r in runs:
            if r.verdict == "fail":
                fails = (*current.fails, r) if current else (r,)
                current = Failure(system, command, posting, fails, passed)
            elif current is None:
                passed = r
            elif build(r) not in {build(f) for f in current.fails}:  # else a flake
                found.append(replace(current, ended=r))
                current, passed = None, r
        if current:
            found.append(current)
    return found


def title(system: str, command: str, reason: str) -> str:
    return f"Failure: {system} {command} {reason}"


def body(active: list[Failure]) -> str:
    f = active[0]
    rows = Counter(
        (build(a.fails[-1]), build(a.passed) if a.passed else "never", a.points_at) for a in active
    )
    return "\n".join(
        [
            f"`{f.system}` `{f.command}` fails with `{f.reason}` on {len(active)}"
            f" posting{'s' * (len(active) > 1)} in the last {WINDOW_DAYS} days.",
            "",
            "| Fails on | Passed before on | Postings | Points at |",
            "|---|---|---|---|",
            *(f"| {b} | {p} | {n} | {at} |" for (b, p, at), n in rows.most_common()),
            "",
            "The postings, with their URLs, locally:",
            "",
            f"    uv run python -m scripts.failures {f.system} {f.command} {f.reason}",
            "",
        ]
    )


def ending(ended: list[Failure]) -> str:
    builds = sorted({build(f.ended) for f in ended if f.ended})
    if builds:
        return f"Ended: the postings pass again, on {', '.join(builds)}."
    return f"Ended: no posting fails this way, on a build that counts, in {WINDOW_DAYS} days."


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    state: str = "OPEN"
    closed_at: str = ""  # ISO 8601, UTC
    fix: str | None = None  # the commit that last closed it


@dataclass(frozen=True)
class Action:
    kind: str  # open | edit | close | reopen
    title: str
    text: str  # the body, or the closing comment
    number: int | None = None
    comment: str = field(default="", compare=False)  # a reopen's


def counts(f: Failure, issue: Issue | None, contains: Callable[[Run, str], bool]) -> bool:
    """Whether a failure counts toward its issue: once a fix closed it, only on a build that
    contains the fix; once closed, only after it closed."""
    if issue is None:
        return True
    return any(
        (issue.fix is None or contains(r, issue.fix))
        and (issue.state == "OPEN" or r.timestamp > issue.closed_at)
        for r in f.fails
    )


def actions(
    found: list[Failure], issues: list[Issue], contains: Callable[[Run, str], bool]
) -> list[Action]:
    """What brings the `failure` issues in line with the window's failures. `issues` newest
    first; `contains(run, commit)` says whether the run's build contains the commit."""
    latest: dict[str, Issue] = {}
    for i in issues:
        if i.title not in latest or (i.state == "OPEN" and latest[i.title].state != "OPEN"):
            latest[i.title] = i
    active: dict[str, list[Failure]] = {}
    ended: dict[str, list[Failure]] = {}
    for f in found:
        if f.ended:
            ended.setdefault(f.title, []).append(f)
        elif counts(f, latest.get(f.title), contains):
            active.setdefault(f.title, []).append(f)
    out = []
    for t, fs in sorted(active.items()):
        text, issue = body(fs), latest.get(t)
        if issue is None:
            out.append(Action("open", t, text))
        elif issue.state != "OPEN":
            again = sorted({build(f.fails[-1]) for f in fs})
            note = f"Fails again, on {', '.join(again)}."
            out.append(Action("reopen", t, text, issue.number, note))
        elif issue.body.strip() != text.strip():
            out.append(Action("edit", t, text, issue.number))
    for t, issue in sorted(latest.items()):
        if issue.state == "OPEN" and t not in active:
            out.append(Action("close", t, ending(ended.get(t, [])), issue.number))
    return out


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT, check=True)
    return out.stdout.split()


def _fix(number: int) -> str | None:
    """The commit of the latest close that came from one, a merged pull request's."""
    jq = '.[] | select(.event == "closed" and .commit_id) | .commit_id'
    events = f"repos/{{owner}}/{{repo}}/issues/{number}/events"
    commits = _gh("api", events, "--paginate", "--jq", jq).split()
    return commits[-1] if commits else None


def _contains() -> Callable[[Run, str], bool]:
    """Whether a run's build contains a commit: a dev run's commit descends from it on `main`,
    a user run's release is tagged on a commit that does."""
    memo: dict[str, tuple[set[str], set[str]]] = {}

    def contains(r: Run, commit: str) -> bool:
        if commit not in memo:
            after = {commit, *_git("rev-list", "--ancestry-path", f"{commit}..{MAIN}")}
            tags = {t.removeprefix("v") for t in _git("tag", "--contains", commit)}
            memo[commit] = after, tags
        after, tags = memo[commit]
        return r.commit in after if r.source == "dev" else r.version in tags

    return contains


def sync(found: list[Failure]) -> None:
    _gh("label", "create", LABEL, "--force", "--color", "f7768e",
        "--description", "A posting fails, per system, command and reason (ADR 0016)")  # fmt: skip
    for name, description in TRIAGE_LABELS.items():
        _gh("label", "create", name, "--force", "--color", "8b90a8",
            "--description", description)  # fmt: skip
    listed = _gh("issue", "list", "--label", LABEL, "--state", "all", "--limit", str(ISSUE_LIMIT),
                 "--json", "number,title,body,state,closedAt,labels")  # fmt: skip
    listed = json.loads(listed)
    labels = {i["number"]: {n["name"] for n in i["labels"]} for i in listed}
    titles = {f.title for f in found}
    issues = [
        Issue(
            i["number"],
            i["title"],
            i["body"],
            i["state"],
            i["closedAt"] or "",
            _fix(i["number"]) if i["title"] in titles else None,
        )  # fmt: skip
        for i in listed
    ]
    for a in actions(found, issues, _contains()):
        if a.kind == "open":
            _gh("issue", "create", "--title", a.title, "--body", a.text, "--label", LABEL)
        elif a.kind == "edit":
            _gh("issue", "edit", str(a.number), "--body", a.text)
        elif a.kind == "reopen":
            _gh("issue", "reopen", str(a.number), "--comment", a.comment)
            stale = labels[a.number] & set(TRIAGE_LABELS)
            _gh("issue", "edit", str(a.number), "--body", a.text,
                *(("--remove-label", ",".join(sorted(stale))) if stale else ()))  # fmt: skip
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


def show(found: list[Failure], system: str, command: str, reason: str | None, key: str) -> None:
    active = [
        f
        for f in found
        if (f.system, f.command) == (system, command) and reason in (None, f.reason) and not f.ended
    ]
    if not active:
        print(f"No failure on {system} {command} in the last {WINDOW_DAYS} days.")
        return
    urls = dict(run_history.query(key, URLS.format(system=system)))
    missing = {f.posting for f in active} - urls.keys()
    urls |= _freehire(system, missing) if missing else {}
    for f in active:
        url = urls.get(f.posting, f"unresolved, usually closed ({f.posting[:12]})")
        passed = f"passed on {build(f.passed)}, " if f.passed else ""
        print(f"{url}\n  {f.reason}: {passed}fails on {build(f.fails[-1])}: {f.points_at}")


def main(argv: list[str]) -> None:
    rows = None
    if argv[:1] == ["--from"]:
        rows, argv = json.loads(Path(argv[1]).read_text()), argv[2:]
    if argv == ["--sync"]:
        key = os.environ.get("POSTHOG_READ_KEY", "")
    elif len(argv) in (2, 3) and argv[0] in run_history.systems() and argv[1] in COMMANDS:
        key = _env_key()
    else:
        sys.exit(__doc__)
    if rows is None:
        rows = run_history.query(key or sys.exit("POSTHOG_READ_KEY is not set"))
    names = set(run_history.systems())
    events = run_history.known(
        run_history.runs(rows), names, run_history.releases(), run_history.on_main()
    )
    found = failures(events)
    if argv == ["--sync"]:
        sync(found)
    else:
        show(found, argv[0], argv[1], argv[2] if len(argv) == 3 else None, key)


if __name__ == "__main__":
    main(sys.argv[1:])
