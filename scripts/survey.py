"""Reads and dry-runs postings with the test persona, one row per URL in .tmp/corpus/ (ADR 0013).

    uv run python -m scripts.survey URL...
    uv run python -m scripts.survey --source greenhouse --companies 5

Never sends.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import date
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from atsfill import core
from atsfill.core import browser, paths
from atsfill.core.browser import Page, Response
from atsfill.core.results import Status
from tests import persona

ROOT = Path(__file__).parents[1]
CORPUS = ROOT / ".tmp/corpus"
DELAY_S = 5.0
IDLE_MS = 15_000

# Each vendor's block page, by a header, a cookie or the page source, checked in this order.
VENDORS = [
    ("DataDome", re.compile(r"x-datadome|datadome=|captcha-delivery\.com", re.IGNORECASE)),
    (
        "Cloudflare",
        re.compile(r"cf-mitigated|challenges\.cloudflare\.com|/cdn-cgi/challenge-platform|cf-chl"),
    ),
    ("PerimeterX", re.compile(r"_pxhd|px-captcha|captcha\.px-cdn\.net|perimeterx", re.IGNORECASE)),
    ("Akamai", re.compile(r"akamaighost|errors\.edgesuite\.net|_abck=", re.IGNORECASE)),
]
LOGIN_PATH = re.compile(r"/(log-?in|sign-?in|auth|authenticate|sso)\b", re.IGNORECASE)


@dataclass
class Probe:
    """What the page itself shows, before atsfill reads it."""

    final_url: str
    label: str | None = None  # login_wall | blocked | embedded | redirect
    signal: str | None = None
    vendor: str | None = None


def _vendor(text: str) -> tuple[str, str] | None:
    for vendor, pattern in VENDORS:
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
    if hit := _vendor(text):
        found.label, found.vendor, found.signal = "blocked", *hit
    elif response and response.status in (403, 429):
        found.label, found.vendor, found.signal = "blocked", "unknown", f"http {response.status}"
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


def survey(url: str, source: str | None, data: Path, cv: Path) -> dict:
    key = hashlib.sha1(url.encode()).hexdigest()[:12]
    with browser.open_page(data) as page:
        response = page.goto(url)
        with suppress(browser.Timeout):  # a page that keeps polling is still labelled
            page.wait_for_load_state("networkidle", timeout=IDLE_MS)
        found = probe(page, url, response)
        evidence = _evidence(page, response, key)
    read = core.read(url, data_dir=data)
    result = read
    if read.status is Status.READY:
        result = core.fill(url, persona.answers(read.fields, cv), data_dir=data)
    step = None
    if found.label == "blocked":
        step = "posting"
    elif result.status is Status.BLOCKED:
        step = "upload" if "upload" in (result.reason or "") else "form"
        found.label = found.label or "blocked"
        found.vendor = found.vendor or "unknown"
        found.signal = found.signal or result.reason
    if result.status is not Status.READY and found.label is None:
        found.label = "unclear"
    return {
        "source": source,
        "url": url,
        "final_url": found.final_url,
        "system": result.system,
        "status": result.status,
        "reason": result.reason,
        "fields": len(read.fields),
        **asdict(found),
        "step": step,
        "date": date.today().isoformat(),
        "atsfill": version("atsfill"),
        "camoufox": version("camoufox"),
        "browser": browser.version(),
        "evidence": str(evidence.relative_to(ROOT)),
    }


def freehire(source: str, companies: int) -> list[str]:
    """One posting each from `companies` different companies on a freehire source."""
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
            urls.setdefault(job["company_slug"], job["url"])
        offset += page_size
    return list(urls.values())[:companies]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("urls", nargs="*")
    p.add_argument("--source", help="a freehire source to sample")
    p.add_argument("--companies", type=int, default=5, help="distinct companies to sample")
    p.add_argument("--data-dir", type=Path, default=CORPUS / "data")
    p.add_argument("--delay", type=float, default=DELAY_S, help="seconds between postings")
    args = p.parse_args()
    if not args.urls and not args.source:
        p.error("give URLs or --source")
    urls = args.urls or freehire(args.source, args.companies)
    CORPUS.mkdir(parents=True, exist_ok=True)
    cv = persona.resume(CORPUS / "resume.pdf")
    data = paths.data_dir(args.data_dir)
    out = CORPUS / f"{args.source or 'urls'}.jsonl"
    for n, url in enumerate(urls):
        if n:
            time.sleep(args.delay)
        try:
            row = survey(url, args.source, data, cv)
        except Exception as e:  # one posting's failure is a row, not the end of the survey
            row = {"source": args.source, "url": url, "label": "unclear",
                   "reason": f"survey_error: {type(e).__name__}: {e}",
                   "date": date.today().isoformat()}  # fmt: skip
        with out.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")
        print(json.dumps({k: row.get(k) for k in ("url", "system", "status", "label", "reason")}))


if __name__ == "__main__":
    main()
