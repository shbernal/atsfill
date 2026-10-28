"""Anonymous telemetry (ADR 0016): one `run` event per `read`, `fill` and `send`, built from
the command's result, and for a failed run a `report` event with its URL and scrubbed page, at
`detailed` or by `atsfill report`. Never in the way: any failure is dropped and the result goes
back as it came, but for the notices."""

import hashlib
import json
import logging
import os
import subprocess
import sys
import uuid
from collections.abc import Iterable
from contextlib import suppress
from functools import cache
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from posthog import Posthog

from atsfill.adapters import adapters
from atsfill.core import paths, registry, snapshot
from atsfill.core.results import ReportResult, Result, Status

PROJECT_KEY = "phc_xmrdiXySbbZCdUzdbdNUtjMco3rLL2Ko5j4hFrX9fUQ8"  # ingestion only, safe to ship
HOST = "https://eu.i.posthog.com"
REQUEST_TIMEOUT_SECONDS = 3
MAX_RETRIES = 1
FLUSH_TIMEOUT_SECONDS = 2  # the SDK's shutdown() waits without one
GIT_TIMEOUT_SECONDS = 2
ROOT = Path(__file__).parents[3]  # the checkout, for an editable install
NOTICE_FILE = "telemetry-notice"  # in the data directory, once the notice was shown
FAILED_RUN = "failed-run.json"  # in the data directory, the last failed run's report event
DOCS = "https://github.com/shbernal/atsfill/blob/main/docs/telemetry.md"
NOTICE = (
    "telemetry: atsfill sends an anonymous event per read, fill and send, no answers or URLs;"
    ' set telemetry = "off" in {config} or DO_NOT_TRACK=1 to stop it:'
    " " + DOCS
)
REPORT = (
    "run `atsfill report` to send this run's URL and page, scrubbed of your answers,"
    " to the maintainer: " + DOCS
)
# Reason codes that say nothing about the adapter: the user's input or a fact about the posting
EXCLUDED = (
    "invalid_answers",
    "answer_not_taken",
    "not_qualified",
    "already_sent",
    "submit_not_enabled",
    "out_of_scope",
    "posting_gone",
    "external_step",
)

logging.getLogger("posthog").disabled = True


def _code(reason: str | None) -> str | None:
    return reason.partition(":")[0] if reason else None


def verdict(result: Result) -> str:
    """What the run says about its adapter: `pass`, `fail`, `excluded` or `unsupported`."""
    if result.status in (Status.READY, Status.SUBMITTED):
        return "pass"
    code = _code(result.reason)
    if code in EXCLUDED:
        return "excluded"
    return "unsupported" if code == "no_adapter" else "fail"


def posting_hash(url: str) -> str:
    """The URL hashed without its fragment and `utm_` parameters, scheme and host lowercased."""
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.startswith("utm_")])
    normal = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))
    return hashlib.sha256(normal.encode()).hexdigest()


def _posting(result: Result) -> str:
    """The posting's key where its adapter gives one, as the double-send guard keys it, so
    every URL of a posting hashes the same; else its URL."""
    adapter = next((a for a in adapters() if a.name == result.system), None)
    return adapter.posting(result.url) if adapter else result.url


def _system(result: Result) -> str:
    if result.system:
        return result.system
    source = registry.for_url(result.url)
    return source.name if source else "unknown"


def _in_tests_or_ci() -> bool:
    return "pytest" in sys.modules or any(
        os.environ.get(v) for v in ("PYTEST_VERSION", "PYTEST_CURRENT_TEST", "CI")
    )


def level() -> str:
    """`off` under DO_NOT_TRACK, the test suite and CI, whatever the config says."""
    if os.environ.get("DO_NOT_TRACK", "") not in ("", "0") or _in_tests_or_ci():
        return "off"
    return paths.telemetry()


@cache
def origin() -> tuple[str, str | None]:
    """`dev` and HEAD for an editable install (HEAD only on a clean tree), else `user`."""
    try:
        direct = json.loads(distribution("atsfill").read_text("direct_url.json") or "{}")
    except PackageNotFoundError, json.JSONDecodeError:
        direct = {}
    if not direct.get("dir_info", {}).get("editable"):
        return "user", None
    try:
        git = lambda *a: subprocess.run(  # noqa: E731
            ["git", *a], capture_output=True, text=True, cwd=ROOT, timeout=GIT_TIMEOUT_SECONDS
        ).stdout.strip()
        return "dev", None if git("status", "--porcelain") else git("rev-parse", "HEAD") or None
    except OSError, subprocess.SubprocessError:
        return "dev", None


def event(command: str, result: Result) -> dict:
    source, commit = origin()
    properties = {
        "system": _system(result),
        "command": command,
        "verdict": verdict(result),
        "status": result.status.value,
        "reason": _code(result.reason),
        "version": version("atsfill"),
        "posting": posting_hash(_posting(result)),
        "source": source,
        "commit": commit,
    }
    return {k: v for k, v in properties.items() if v is not None}


def _capture(events: list[tuple[str, dict]]) -> None:
    """Sends the events; raises when the SDK reports a batch it could not send."""
    failed: list[Exception] = []
    client = Posthog(
        PROJECT_KEY,
        host=HOST,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
        disable_geoip=True,
        on_error=lambda e, _: failed.append(e),
    )
    for name, properties in events:
        properties = {**properties, "$process_person_profile": False}
        client.capture(name, distinct_id=str(uuid.uuid4()), properties=properties)
    client.flush(timeout_seconds=FLUSH_TIMEOUT_SECONDS)
    if failed:
        raise failed[0]


def _notice(data: Path) -> str | None:
    """The notice, the first time telemetry is on for this data directory."""
    marker = data / NOTICE_FILE
    if marker.exists():
        return None
    marker.touch()
    return NOTICE.format(config=paths.config_file())


def _report(
    run: dict, result: Result, captured: tuple[str, str] | None, secrets: Iterable[str]
) -> dict:
    """The `report` event: the run's, plus its URL, its reason's detail and its page, each
    scrubbed of the secrets."""
    detail = (result.reason or "").partition(":")[2].strip()
    report = {
        **run,
        "url": result.url,
        "detail": snapshot.scrub(detail, secrets) or None,
        "snapshot": snapshot.kept(captured, secrets) if captured else None,
    }
    return {k: v for k, v in report.items() if v is not None}


def sent(
    command: str,
    result: Result,
    data: Path,
    captured: tuple[str, str] | None = None,
    secrets: Iterable[str] = (),
) -> Result:
    """The result, its event sent at `basic` and `detailed`. A failed run is kept for `atsfill
    report`, and its report sent along at `detailed`, else named in the result."""
    with suppress(Exception):  # never in the way
        if (at := level()) == "off":
            return result
        result.notice = _notice(data)
        run = event(command, result)
        events = [("run", run | {"url": result.url} if at == "detailed" else run)]
        if verdict(result) == "fail":
            report = _report(run, result, captured, secrets)
            (data / FAILED_RUN).write_text(json.dumps({"event": report, "reported": False}))
            if at == "detailed":
                events.append(("report", report))
            else:
                result.report = REPORT
        _capture(events)
        if len(events) > 1:
            _mark_reported(data)
    return result


def _mark_reported(data: Path) -> None:
    path = data / FAILED_RUN
    path.write_text(json.dumps(json.loads(path.read_text()) | {"reported": True}))


def report(data: Path) -> ReportResult:
    """Sends the last failed run's report, once, whatever the level but `off`."""
    if level() == "off":
        return ReportResult("refused", "telemetry_off: telemetry is off, nothing is sent")
    path = data / FAILED_RUN
    if not path.exists():
        return ReportResult("refused", "no_failed_run: no failed run to report")
    kept = json.loads(path.read_text())
    report, url = kept["event"], kept["event"]["url"]
    if kept["reported"]:
        return ReportResult("refused", "already_reported: the last failed run was sent", url)
    try:
        _capture([("report", report)])
    except Exception as e:
        return ReportResult("blocked", f"not_sent: {type(e).__name__}: {e}", url)
    _mark_reported(data)
    return ReportResult("sent", url=url)
