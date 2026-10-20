"""Reads and dry-runs postings with the test persona, one row per URL in .tmp/corpus/ (ADR 0013).

    uv run python -m scripts.survey URL...
    uv run python -m scripts.survey --source greenhouse --companies 5
    uv run python -m scripts.survey --session wttj=PATH URL...

Never sends. A row records the commit it ran at, or null on a tree with uncommitted changes.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import date
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from atsfill import core
from atsfill.adapters import ADAPTERS
from atsfill.cli import session as session_arg
from atsfill.core import browser, paths
from atsfill.core.browser import Page, Response
from atsfill.core.commands import Sessions
from atsfill.core.results import Status
from tests import persona

ROOT = Path(__file__).parents[1]
CORPUS = ROOT / ".tmp/corpus"
DELAY_S = 5.0
IDLE_MS = 15_000

# A challenge or block page, by a header or the page source: a block on its own.
CHALLENGES = [
    ("DataDome", re.compile(r"captcha-delivery\.com", re.IGNORECASE)),
    ("Cloudflare", re.compile(r"cf-mitigated: challenge|cf_chl_opt|<title>Just a moment")),
    ("PerimeterX", re.compile(r"px-captcha|captcha\.px-cdn\.net|Press & Hold", re.IGNORECASE)),
    ("Akamai", re.compile(r"errors\.edgesuite\.net", re.IGNORECASE)),
]
# A vendor's protection on any page it serves: it names the vendor of a 403 or a 429 only.
PROTECTIONS = [
    ("DataDome", re.compile(r"x-datadome|datadome=", re.IGNORECASE)),
    ("Cloudflare", re.compile(r"cf-ray|/cdn-cgi/challenge-platform")),
    ("PerimeterX", re.compile(r"_px\w*=|perimeterx", re.IGNORECASE)),
    ("Akamai", re.compile(r"akamaighost|_abck=", re.IGNORECASE)),
]
NOT_A_BLOCK = ("covered", "time_budget")  # `blocked` statuses that are atsfill's own limits
LOGIN_PATH = re.compile(r"/(log-?in|sign-?in|auth|authenticate|sso)\b", re.IGNORECASE)


@dataclass
class Probe:
    """What the page itself shows, before atsfill reads it."""

    final_url: str
    label: str | None = None  # login_wall | blocked | embedded | redirect
    signal: str | None = None
    vendor: str | None = None


def _vendor(text: str, signals: list[tuple[str, re.Pattern]]) -> tuple[str, str] | None:
    for vendor, pattern in signals:
        if m := pattern.search(text):
            return vendor, m.group(0)
    return None


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def probe(page: Page, url: str, response: Response | None) -> Probe:
    """Labels the loaded page: a block, a login wall, a form in another site's frame, or a
    redirect to another host, in that order."""
    found = Probe(page.url)
    headers = response.all_headers() if response else {}
    cookies = "; ".join(f"{c.get('name')}={c.get('value')}" for c in page.context.cookies())
    text = "\n".join([*(f"{k}: {v}" for k, v in headers.items()), cookies, page.content()])
    status = response.status if response else None
    if hit := _vendor(text, CHALLENGES):
        found.label, found.vendor, found.signal = "blocked", *hit
    elif status in (403, 429):
        vendor, signal = _vendor(text, PROTECTIONS) or ("unknown", "")
        found.label, found.vendor = "blocked", vendor
        found.signal = f"http {status}" + (f", {signal}" if signal else "")
    elif (
        LOGIN_PATH.search(urlsplit(page.url).path)
        or page.locator("input[type=password]:visible").count()
    ):
        found.label, found.signal = "login_wall", "password field or login URL"
    elif frames := [
        f.url for f in page.frames[1:] if _host(f.url) not in ("", _host(page.url)) and _form(f)
    ]:
        found.label, found.signal = "embedded", frames[0]
    elif _host(page.url) != _host(url):
        found.label, found.signal = "redirect", _host(page.url)
    return found


def _form(frame) -> bool:
    try:
        return frame.locator("form, input:not([type=hidden]), textarea").count() > 0
    except browser.BrowserError:
        return False


def _evidence(page: Page, response: Response | None, key: str) -> Path:
    path = CORPUS / "evidence" / key
    path.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=path / "page.png", full_page=True)
    (path / "page.html").write_text(page.content())
    headers = {"status": response.status, **response.all_headers()} if response else {}
    (path / "headers.json").write_text(json.dumps(headers, indent=2))
    return path


def commit() -> str | None:
    """HEAD, when the tree has no uncommitted changes."""
    git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, cwd=ROOT).stdout  # noqa: E731
    return None if git("status", "--porcelain").strip() else git("rev-parse", "HEAD").strip()


def survey(
    url: str, company: str, source: str | None, data: Path, cv: Path, sessions: Sessions
) -> dict:
    key = hashlib.sha1(url.encode()).hexdigest()[:12]
    session = next(
        (sessions[a.name] for a in ADAPTERS if a.name in sessions and a.detect(url, None)), None
    )
    with browser.open_page(data, session) as page:
        response = page.goto(url)
        with suppress(browser.Timeout):  # a page that keeps polling is still labelled
            page.wait_for_load_state("networkidle", timeout=IDLE_MS)
        found = probe(page, url, response)
        evidence = _evidence(page, response, key)
    read = core.read(url, data_dir=data, sessions=sessions)
    result = read
    if read.status is Status.READY:
        answers = persona.answers(read.fields, cv, cv.with_name("photo.png"))
        result = core.fill(url, answers, data_dir=data, sessions=sessions)
    step = None
    if found.label == "blocked":
        step = "posting"
    elif result.status is Status.BLOCKED and (result.reason or "").startswith("login_required"):
        found.label, found.signal = "login_wall", result.reason
    elif result.status is Status.BLOCKED and not (result.reason or "").startswith(NOT_A_BLOCK):
        step = "upload" if "upload" in (result.reason or "") else "form"
        found.label, found.vendor, found.signal = "blocked", "unknown", result.reason
    if result.status is not Status.READY and found.label is None:
        found.label = "unclear"
    return {
        "source": source,
        "company": company,
        "url": url,
        "final_url": found.final_url,
        "system": result.system,
        "status": result.status,
        "reason": result.reason,
        "fields": len(read.fields),
        **asdict(found),
        "step": step,
        "date": date.today().isoformat(),
        "commit": commit(),
        "atsfill": version("atsfill"),
        "camoufox": version("camoufox"),
        "browser": browser.version(),
        "evidence": str(evidence.relative_to(ROOT)),
    }


def freehire(
    source: str,
    companies: int,
    keep: Callable[[str], bool] = lambda url: True,
    exclude: set[str] = set(),  # noqa: B006
) -> list[tuple[str, str]]:
    """One posting, with its company, from each of `companies` companies on a freehire source,
    sampling until enough postings pass `keep` or the source runs out; `exclude`d companies
    are skipped."""
    urls: dict[str, str] = {}
    offset, page_size = 0, 50
    while len(urls) < companies:
        run = subprocess.run(
            ["freehire", "--json", "search", "", "--facet", f"source={source}",
             "--limit", str(page_size), "--offset", str(offset)],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
        if "ignor" in run.stderr.lower():
            sys.exit(f"freehire ignored a filter: {run.stderr.strip()}")
        jobs = json.loads(run.stdout) or []
        if not jobs:
            break
        for job in jobs:
            if job["company_slug"] not in exclude and keep(job["url"]):
                urls.setdefault(job["company_slug"], job["url"])
        offset += page_size
    return [(url, company) for company, url in urls.items()][:companies]


def run(
    postings: list[tuple[str, str]],
    source: str | None,
    data_dir: Path = CORPUS / "data",
    delay: float = DELAY_S,
    echo: bool = True,
    sessions: Sessions | None = None,
    out: str | None = None,
) -> list[dict]:
    """Surveys each (URL, company), appending its row to .tmp/corpus/<out or source>.jsonl."""
    CORPUS.mkdir(parents=True, exist_ok=True)
    cv = persona.resume(CORPUS / "resume.pdf")
    persona.photo(CORPUS / "photo.png")
    data = paths.data_dir(data_dir)
    path, rows = CORPUS / f"{out or source or 'urls'}.jsonl", []
    for n, (url, company) in enumerate(postings):
        if n:
            time.sleep(delay)
        try:
            row = survey(url, company, source, data, cv, sessions or {})
        except Exception as e:  # one posting's failure is a row, not the end of the survey
            row = {"source": source, "company": company, "url": url, "label": "unclear",
                   "reason": f"survey_error: {type(e).__name__}: {e}",
                   "date": date.today().isoformat()}  # fmt: skip
        with path.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")
        if echo:
            print(
                json.dumps({k: row.get(k) for k in ("url", "system", "status", "label", "reason")})
            )
        rows.append(row)
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("urls", nargs="*")
    p.add_argument("--source", help="a freehire source to sample")
    p.add_argument("--companies", type=int, default=5, help="distinct companies to sample")
    p.add_argument("--data-dir", type=Path, default=CORPUS / "data")
    p.add_argument("--delay", type=float, default=DELAY_S, help="seconds between postings")
    p.add_argument(
        "--session", type=session_arg, action="append", default=[], metavar="ADAPTER=PATH",
        help="a stored session for an adapter that needs an account",
    )  # fmt: skip
    args = p.parse_args()
    if not args.urls and not args.source:
        p.error("give URLs or --source")
    postings = [(u, _host(u)) for u in args.urls] or freehire(args.source, args.companies)
    run(postings, args.source, args.data_dir, args.delay, sessions=dict(args.session))


if __name__ == "__main__":
    main()
