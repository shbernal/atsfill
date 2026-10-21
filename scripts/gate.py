"""Whether an adapter is done (ADR 0013): one line per criterion, then PASS or FAIL.

    uv run python -m scripts.gate <system> [--fresh] [--employers N] [--session ADAPTER=PATH]
    uv run python -m scripts.gate <system> --broken "<reason>" [--cause system|code|unknown]

On PASS it records `dry_run_verified` in the registry; `--broken` records `send_broken` (ADR
0012). Either is its own commit, so the commit it names is the code's. Never sends. Employers
and postings are printed as hashes, so the output can go in a public pull request.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import date
from importlib.metadata import version
from pathlib import Path

from atsfill.adapters import ADAPTERS
from atsfill.cli import session as session_arg
from atsfill.core import browser, registry
from atsfill.core.adapter import ReadsOverHttp
from scripts import survey

ROOT = Path(__file__).parents[1]
REGISTRY = ROOT / "src/atsfill/registry.toml"
GENERATED = [REGISTRY, ROOT / "docs/adapters/README.md", ROOT / "README.md"]
EMPLOYERS = 5
# A skipped posting (ADR 0013): a fact about the posting or out of scope, not the adapter's job
SKIPPED = ("posting_gone", "external_step", "out_of_scope")
CHECKS = [
    ["uv", "run", "pytest", "-q"],
    ["uv", "run", "ruff", "check"],
    ["uv", "run", "ruff", "format", "--check"],
    ["uv", "run", "ty", "check"],
]
URL = re.compile(r"https?://\S+")
LABEL = re.compile(r" \((?:[^()]|\([^()]*\))*\)")  # a field's label, which may name the employer

results: list[bool] = []


def line(ok: bool, criterion: str, detail: str = "") -> None:
    results.append(ok)
    print(f"{'ok  ' if ok else 'FAIL'}  {criterion}" + (f": {detail}" if detail else ""))


def rows(paths: list[Path], commit: str | None = None) -> list[dict]:
    """The latest survey row of each URL in these files, of that commit if one is given."""
    latest: dict[str, dict] = {}
    for path in paths:
        for text in path.read_text().splitlines() if path.exists() else []:
            row = json.loads(text)
            if commit is None or row.get("commit") == commit:
                latest[row["url"]] = row
    return list(latest.values())


def parity(adapter: ReadsOverHttp, url: str, data: Path) -> str | None:
    """None when the fields read over HTTP and from the page match; else what differs."""
    over_http = adapter.read_api(url) or []
    with browser.open_page(data) as page:
        page.goto(url)
        adapter.detect(url, page)
        from_page = adapter.read_form(page)
    shape = lambda fields: {(f.id, f.type, f.required) for f in fields}  # noqa: E731
    if diff := shape(over_http) ^ shape(from_page):
        return "differ on " + ", ".join(sorted(f[0] for f in diff))
    return None


def stops(counted: list[dict]) -> list[str]:
    """The rows that were not ready, by status, label and reason: the adapter's, then the
    skipped postings apart."""
    stopped = [r for r in counted if r["status"] != "ready"]
    skipped = lambda r: survey.code(r.get("reason")) in SKIPPED  # noqa: E731
    others = Counter(
        (r["status"], r.get("label"), public(r.get("reason"))) for r in stopped if not skipped(r)
    )
    passed = Counter(public(r.get("reason")) for r in stopped if skipped(r))
    return [
        *(
            f"not ready ({n}): {s}, {label}, {reason}"
            for (s, label, reason), n in others.most_common()
        ),
        *(f"skipped ({n}): {reason}" for reason, n in passed.most_common()),
    ]


def handle(value: str) -> str:
    """An employer or a posting, named without naming it: the output goes in a public PR."""
    return hashlib.sha1(value.encode()).hexdigest()[:8]


def public(reason: str | None) -> str:
    """A reason with field ids only: no label, and URLs as hashes."""
    return URL.sub(lambda m: f"<{handle(m.group(0))}>", LABEL.sub("", reason or ""))


def record(source: str, key: str, value: registry.Verification | registry.Broken) -> None:
    """Writes `key` into the registry and the files generated from it, as its own commit."""
    REGISTRY.write_text(registry.with_verification(REGISTRY.read_text(), source, key, value))
    registry.sources.cache_clear()
    subprocess.run(["uv", "run", "python", "-m", "scripts.backlog"], check=True, cwd=ROOT)
    message = f"Record {source} as {key.replace('_', ' ')}"
    subprocess.run(["git", "commit", "-qm", message, "--", *GENERATED], check=True, cwd=ROOT)
    print(f"recorded {key} {value.date} {value.version} ({value.commit}) by {value.by}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("system", help="the adapter's name")
    p.add_argument("--fresh", action="store_true", help="survey new employers first")
    p.add_argument("--employers", type=int, default=2 * EMPLOYERS, help="employers to sample")
    p.add_argument(
        "--session", type=session_arg, action="append", default=[], metavar="ADAPTER=PATH",
        help="a stored session, the dry runs fill under it",
    )  # fmt: skip
    p.add_argument("--broken", metavar="REASON", help="record that sending is broken")
    p.add_argument("--cause", choices=registry.CAUSES, default="unknown")
    p.add_argument("--by", default="adapter_gate", help="who records it")
    args = p.parse_args()
    name = args.system
    head = survey.commit()
    short = head[:7] if head else None
    print(f"gate {name}, atsfill {version('atsfill')} ({short or 'uncommitted'}),"
          f" browser {browser.version()}, {date.today()}")  # fmt: skip

    adapter = next((a for a in ADAPTERS if a.name == name), None)
    source = registry.for_adapter(name)
    if args.broken:
        if not (source and short):
            sys.exit("--broken needs a registry entry and a tree with no uncommitted changes")
        broken = registry.Broken(
            date.today(), version("atsfill"), short, args.by, args.broken, args.cause
        )
        record(source.name, "send_broken", broken)
        return

    line(adapter is not None, "adapter registered in atsfill.adapters.ADAPTERS")
    line(source is not None, "registry entry", source.name if source else "none names it")
    pages = ROOT / "tests/pages" / name
    line(pages.is_dir() and any(pages.iterdir()), "fixture page", str(pages.relative_to(ROOT)))
    doc = ROOT / "docs/adapters" / f"{name}.md"
    line(doc.exists(), "adapter doc", str(doc.relative_to(ROOT)))

    for command in CHECKS:
        run = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
        tail = (run.stdout + run.stderr).strip().splitlines()[-1:] or [""]
        line(run.returncode == 0, " ".join(command[2:]), tail[0])

    criterion = f"dry run ready on >= {EMPLOYERS} employers outside the sample"
    if not (adapter and source):
        line(False, criterion, "no adapter or no registry entry")
    elif head is None:
        line(False, criterion, "uncommitted changes: only rows from a commit count")
    else:
        corpus = survey.CORPUS
        sample = {
            survey.employer(r)
            for r in rows([corpus / f"{source.name}.jsonl"])
            if survey.employer(r)
        }
        if args.fresh:
            postings = survey.freehire(
                source.name, args.employers, lambda u: adapter.detect(u, None), sample
            )
            survey.run(postings, source.name, echo=False, sessions=dict(args.session),
                       out=f"gate-{source.name}")  # fmt: skip
        counted = [
            r
            for r in rows(sorted(corpus.glob("*.jsonl")), head)
            if r.get("system") == name and survey.employer(r) not in sample
        ]
        ready = {survey.employer(r): r for r in counted if r["status"] == "ready"}
        detail = ", ".join(f"{handle(c)} on {r['date']}" for c, r in ready.items()) or "none"
        line(len(ready) >= EMPLOYERS, criterion, detail)
        for text in stops(counted):
            print(f"      {text}")
        if len(ready) < EMPLOYERS and source.send_verified and not source.send_broken:
            print(
                f"      {name} is send verified: if the stops above are a regression, record it"
                f' with `gate {name} --broken "<fields and steps>" --cause unknown`'
            )

        urls = [r["url"] for r in list(ready.values())[:EMPLOYERS]]
        if not isinstance(adapter, ReadsOverHttp):
            line(True, "read_api parity", "not applicable: no read_api")
        elif not urls:
            line(True, "read_api parity", "not applicable: no ready posting")
        elif not (over_http := [u for u in urls if adapter.read_api(u) is not None]):
            line(True, "read_api parity", "not applicable: read_api returned nothing for these")
        else:
            for url in over_http:
                problem = parity(adapter, url, corpus / "data")
                line(
                    problem is None, "read_api parity", f"{handle(url)}: {problem or 'same fields'}"
                )

    passed = all(results)
    if passed and source and short:
        v = registry.Verification(date.today(), version("atsfill"), args.by, short)
        record(source.name, "dry_run_verified", v)
    print("PASS" if passed else "FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
