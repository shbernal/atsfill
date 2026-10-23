"""The main checkout, where the gitignored `.tmp/` lives, also from a git worktree."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _main() -> Path:
    git = ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]
    out = subprocess.run(git, capture_output=True, text=True, cwd=ROOT).stdout.strip()
    return Path(out).parent if out else ROOT


MAIN = _main()
