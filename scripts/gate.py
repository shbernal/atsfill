"""Whether an adapter is done (ADR 0013): one line per criterion, then PASS or FAIL.

    uv run python -m scripts.gate <system> [--fresh] [--companies N] [--session ADAPTER=PATH]
    uv run python -m scripts.gate <system> --broken "<reason>" [--cause system|code|unknown]

On PASS it records `dry_run_verified` in the registry; `--broken` records `send_broken` (ADR
0012). Either is its own commit, so the commit it names is the code's. Never sends. Companies
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
from atsfill.core.adapter import Adapter
from scripts import survey

ROOT = Path(__file__).parents[1]
REGISTRY = ROOT / "src/atsfill/registry.toml"
GENERATED = [REGISTRY, ROOT / "docs/adapters/README.md", ROOT / "README.md"]
COMPANIES = 5
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


def rows(paths: list[Path]) -> list[dict]:
    """The latest survey row of each URL in these files."""
    latest: dict[str, dict] = {}
    for path in paths:
        for text in path.read_text().splitlines() if path.exists() else []:
            row = json.loads(text)
            latest[row["url"]] = row
    return list(latest.values())


def parity(adapter: Adapter, url: str, data: Path) -> str | None:
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


def handle(value: str) -> str:
    """A company or a posting, named without naming it: the output goes in a public PR."""
    return hashlib.sha1(value.encode()).hexdigest()[:8]


def public(reason: str | None) -> str:
    """A reason with field ids only: no label, and URLs as hashes."""
    return URL.sub(lambda m: f"<{handle(m.group(0))}>", LABEL.sub("", reason or ""))


def record(source: str, key: str, value: registry.Verification | registry.Broken) -> None:
    """Writes `key` into the registry and the files generated from it, as its own commit."""
    REGISTRY.write_text(registry.with_state(REGISTRY.read_text(), source, key, value))
    registry.systems.cache_clear()
    subprocess.run(["uv", "run", "python", "-m", "scripts.backlog"], check=True, cwd=ROOT)
    message = f"Record {source} as {key.replace('_', ' ')}"
    subprocess.run(["git", "commit", "-qm", message, "--", *GENERATED], check=True, cwd=ROOT)
    print(f"recorded {key} {value.date} {value.version} ({value.commit}) by {value.by}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("system", help="the adapter's name")
    p.add_argument("--fresh", action="store_true", help="survey new companies first")
    p.add_argument("--companies", type=int, default=2 * COMPANIES, help="companies to sample")
    p.add_argument(
        "--session", type=session_arg, action="append", default=[], metavar="ADAPTER=PATH",
        help="a stored session, the dry runs fill under it",
    )  # fmt: skip
    p.add_argument("--broken", metavar="REASON", help="record that sending is broken")
    p.add_argument("--cause", choices=registry.CAUSES, default="unknown")
    p.add_argument("--by", default="gate", help="who records it")
    args = p.parse_args()
    name = args.system
    head = survey.commit()
    short = head[:7] if head else None
    print(f"gate {name}, atsfill {version('atsfill')} ({short or 'uncommitted'}),"
          f" browser {browser.version()}, {date.today()}")  # fmt: skip

    adapter = next((a for a in ADAPTERS if a.name == name), None)
    system = registry.for_adapter(name)
    if args.broken:
        if not (system and short):
            sys.exit("--broken needs a registry entry and a tree with no uncommitted changes")
        broken = registry.Broken(
            date.today(), version("atsfill"), short, args.by, args.broken, args.cause
        )
        record(system.source, "send_broken", broken)
        return

    line(adapter is not None, "adapter registered in atsfill.adapters.ADAPTERS")
    line(system is not None, "registry entry", system.source if system else "none names it")
    pages = ROOT / "tests/pages" / name
    line(pages.is_dir() and any(pages.iterdir()), "fixture page", str(pages.relative_to(ROOT)))
    doc = ROOT / "docs/adapters" / f"{name}.md"
    line(doc.exists(), "adapter doc", str(doc.relative_to(ROOT)))

    for command in CHECKS:
        run = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
        tail = (run.stdout + run.stderr).strip().splitlines()[-1:] or [""]
        line(run.returncode == 0, " ".join(command[2:]), tail[0])

    criterion = f"dry run ready on >= {COMPANIES} companies outside the inventory"
    if not (adapter and system):
        line(False, criterion, "no adapter or no registry entry")
    elif head is None:
        line(False, criterion, "uncommitted changes: only rows from a commit count")
    else:
        corpus = survey.CORPUS
        inventory = {
            r["company"] for r in rows([corpus / f"{system.source}.jsonl"]) if r.get("company")
        }
        if args.fresh:
            postings = survey.freehire(
                system.source, args.companies, lambda u: adapter.detect(u, None), inventory
            )
            survey.run(postings, system.source, echo=False, sessions=dict(args.session),
                       out=f"gate-{system.source}")  # fmt: skip
        counted = [
            r
            for r in rows(sorted(corpus.glob("*.jsonl")))
            if r.get("commit") == head
            and r.get("system") == name
            and r.get("company") not in inventory
        ]
        ready = {r["company"]: r for r in counted if r["status"] == "ready"}
        detail = ", ".join(f"{handle(c)} on {r['date']}" for c, r in ready.items()) or "none"
        line(len(ready) >= COMPANIES, criterion, detail)
        others = Counter(
            (r["status"], r.get("label"), public(r.get("reason")))
            for r in counted
            if r["status"] != "ready"
        )
        for (status, label, reason), n in others.most_common():
            print(f"      not ready ({n}): {status}, {label}, {reason}")
        if len(ready) < COMPANIES and system.send_verified and not system.send_broken:
            print(
                f"      {name} is send verified: if the stops above are a regression, record it"
                f' with `gate {name} --broken "<fields and steps>" --cause unknown`'
            )

        urls = [r["url"] for r in list(ready.values())[:COMPANIES]]
        over_http = [u for u in urls if adapter.read_api(u) is not None]
        if not urls:
            line(True, "read_api parity", "not applicable: no ready posting")
        elif not over_http:
            line(True, "read_api parity", "not applicable: read_api returned nothing for these")
        for url in over_http:
            problem = parity(adapter, url, corpus / "data")
            line(problem is None, "read_api parity", f"{handle(url)}: {problem or 'same fields'}")

    passed = all(results)
    if passed and system and short:
        v = registry.Verification(date.today(), version("atsfill"), args.by, short)
        record(system.source, "dry_run_verified", v)
    print("PASS" if passed else "FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
