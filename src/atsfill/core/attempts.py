"""The double-send guard: `attempts.jsonl` in the data directory (ADR 0006)."""

import json
from datetime import UTC, datetime
from pathlib import Path

from atsfill.core.results import Status

PENDING = "clicked"  # appended before the click; a later line records the outcome


def last(data_dir: Path, posting: str) -> str | None:
    path = data_dir / "attempts.jsonl"
    if not path.exists():
        return None
    outcomes = [
        line["status"]
        for line in map(json.loads, path.read_text().splitlines())
        if line["posting"] == posting
    ]
    return outcomes[-1] if outcomes else None


def blocks_send(data_dir: Path, posting: str) -> bool:
    """A last attempt that was clicked without an outcome counts as `unknown`."""
    return last(data_dir, posting) in (Status.SUBMITTED, Status.UNKNOWN, PENDING)


def record(data_dir: Path, posting: str, status: str, **extra: str | None) -> None:
    line = {"at": datetime.now(UTC).isoformat(), "posting": posting, "status": status, **extra}
    with (data_dir / "attempts.jsonl").open("a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
