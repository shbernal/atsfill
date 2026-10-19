"""Whether an adapter is done (ADR 0013): one line per criterion, then PASS or FAIL.

    uv run python -m scripts.gate <system> [--fresh]

On PASS it records `dry_run_verified` in the registry. Never sends. Companies and postings
are printed as hashes, so the output can go in a public pull request.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date
from importlib.metadata import version
from pathlib import Path

from atsfill.adapters import ADAPTERS
from atsfill.core import browser, registry
from atsfill.core.adapter import Adapter
from scripts import survey

ROOT = Path(__file__).parents[1]
REGISTRY = ROOT / "src/atsfill/registry.toml"
BACKLOG = ROOT / "docs/adapters/README.md"
COMPANIES = 3
CHECKS = [
    ["uv", "run", "pytest", "-q"],
    ["uv", "run", "ruff", "check"],
    ["uv", "run", "ruff", "format", "--check"],
    ["uv", "run", "ty", "check"],
]

results: list[bool] = []


def line(ok: bool, criterion: str, detail: str = "") -> None:
    results.append(ok)
    print(f"{'ok  ' if ok else 'FAIL'}  {criterion}" + (f": {detail}" if detail else ""))


def ready_rows(system: str) -> list[dict]:
    """The latest survey row of each URL, kept when it is a `ready` dry run on `system`."""
    latest: dict[str, dict] = {}
    for path in sorted((ROOT / ".tmp/corpus").glob("*.jsonl")):
        for text in path.read_text().splitlines():
            row = json.loads(text)
            latest[row["url"]] = row
    return [r for r in latest.values() if r.get("system") == system and r["status"] == "ready"]


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


def exact_version() -> str:
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT
    ).stdout.strip()
    return f"{version('atsfill')}+g{sha}" if sha else version("atsfill")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("system", help="the adapter's name")
    p.add_argument("--fresh", action="store_true", help="survey the system's source again first")
    p.add_argument("--by", default="gate", help="the verifier recorded on PASS")
    args = p.parse_args()
    name = args.system
    print(f"gate {name}, atsfill {exact_version()}, browser {browser.version()}, {date.today()}")

    adapter = next((a for a in ADAPTERS if a.name == name), None)
    line(adapter is not None, "adapter registered in atsfill.adapters.ADAPTERS")
    system = registry.for_adapter(name)
    line(system is not None, "registry entry", system.source if system else "none names it")
    pages = ROOT / "tests/pages" / name
    line(pages.is_dir() and any(pages.iterdir()), "fixture page", str(pages.relative_to(ROOT)))
    doc = ROOT / "docs/adapters" / f"{name}.md"
    line(doc.exists(), "adapter doc", str(doc.relative_to(ROOT)))

    for command in CHECKS:
        run = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
        tail = (run.stdout + run.stderr).strip().splitlines()[-1:] or [""]
        line(run.returncode == 0, " ".join(command[2:]), tail[0])

    if args.fresh and system:
        survey.run(survey.freehire(system.source, COMPANIES + 2), system.source, echo=False)
    rows = ready_rows(name)
    companies = {r.get("company") or r["url"]: r for r in rows}
    detail = ", ".join(f"{handle(c)} on {r['date']}" for c, r in companies.items()) or "none"
    line(len(companies) >= COMPANIES, f"dry run ready on >= {COMPANIES} companies", detail)

    if adapter:
        urls = [r["url"] for r in list(companies.values())[:COMPANIES]]
        over_http = [u for u in urls if adapter.read_api(u) is not None]
        if not over_http:
            line(
                True,
                "read_api parity",
                "not applicable: read_api returned nothing for these postings",
            )
        for url in over_http:
            problem = parity(adapter, url, ROOT / ".tmp/corpus/data")
            line(problem is None, "read_api parity", f"{handle(url)}: {problem or 'same fields'}")

    passed = all(results)
    if passed and system:
        v = registry.Verification(date.today(), exact_version(), args.by)
        REGISTRY.write_text(
            registry.with_state(REGISTRY.read_text(), system.source, "dry_run_verified", v)
        )
        registry.systems.cache_clear()
        BACKLOG.write_text(registry.backlog())
        print(f"recorded dry_run_verified {v.date} {v.version} by {v.by} in the registry")
    print("PASS" if passed else "FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
