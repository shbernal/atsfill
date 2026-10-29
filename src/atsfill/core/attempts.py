"""The double-send guard: `attempts.jsonl` in the user's directory (ADRs 0006, 0015)."""

import json
from datetime import UTC, datetime
from pathlib import Path

from atsfill.core.results import Status

PENDING = "clicked"  # appended before the click; a later line records the outcome
OPENED = "opened"  # sent by opening the form (`SendsToOpen`); its own submit is still to come


def last(user_dir: Path, posting: str) -> str | None:
    path = user_dir / "attempts.jsonl"
    if not path.exists():
        return None
    outcomes = [
        line["status"]
        for line in map(json.loads, path.read_text().splitlines())
        if line["posting"] == posting
    ]
    return outcomes[-1] if outcomes else None


def blocks_send(user_dir: Path, posting: str) -> bool:
    """A last attempt that was clicked without an outcome counts as `unknown`."""
    return last(user_dir, posting) in (Status.SUBMITTED, Status.UNKNOWN, PENDING)


def record(user_dir: Path, posting: str, status: str, **extra: str | None) -> None:
    line = {"at": datetime.now(UTC).isoformat(), "posting": posting, "status": status, **extra}
    with (user_dir / "attempts.jsonl").open("a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
