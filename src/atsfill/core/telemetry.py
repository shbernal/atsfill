"""Anonymous telemetry (ADR 0016): one `run` event per `read`, `fill` and `send`, built from
the command's result. Never in the way: any failure is dropped and the result goes back as it
came, but for the first-run notice."""

import hashlib
import json
import logging
import os
import subprocess
import sys
import uuid
from contextlib import suppress
from functools import cache
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from posthog import Posthog

from atsfill.adapters import adapters
from atsfill.core import paths, registry
from atsfill.core.results import Result, Status

PROJECT_KEY = "phc_xmrdiXySbbZCdUzdbdNUtjMco3rLL2Ko5j4hFrX9fUQ8"  # ingestion only, safe to ship
HOST = "https://eu.i.posthog.com"
REQUEST_TIMEOUT_SECONDS = 3
MAX_RETRIES = 1
FLUSH_TIMEOUT_SECONDS = 2  # the SDK's shutdown() waits without one
GIT_TIMEOUT_SECONDS = 2
ROOT = Path(__file__).parents[3]  # the checkout, for an editable install
NOTICE_FILE = "telemetry-notice"  # in the data directory, once the notice was shown
NOTICE = (
    "telemetry: atsfill sends an anonymous event per read, fill and send, no answers or URLs;"
    ' set telemetry = "off" in {config} or DO_NOT_TRACK=1 to stop it:'
    " https://github.com/shbernal/atsfill/blob/main/docs/telemetry.md"
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


def _capture(properties: dict) -> None:
    client = Posthog(
        PROJECT_KEY,
        host=HOST,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
        disable_geoip=True,
    )
    properties = {**properties, "$process_person_profile": False}
    client.capture("run", distinct_id=str(uuid.uuid4()), properties=properties)
    client.flush(timeout_seconds=FLUSH_TIMEOUT_SECONDS)


def _notice(data: Path) -> str | None:
    """The notice, the first time telemetry is on for this data directory."""
    marker = data / NOTICE_FILE
    if marker.exists():
        return None
    marker.touch()
    return NOTICE.format(config=paths.config_file())


def sent(command: str, result: Result, data: Path) -> Result:
    """The result, its event sent at `basic` and `detailed`."""
    with suppress(Exception):  # never in the way
        if level() != "off":
            result.notice = _notice(data)
            _capture(event(command, result))
    return result
