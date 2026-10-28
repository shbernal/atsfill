"""The docs site serves docs/ only: a relative link out of it 404s there."""

import re
from pathlib import Path

import pytest

DOCS = (Path(__file__).parent.parent / "docs").resolve()
LINK = re.compile(r"\]\(([^)#\s]+)")


@pytest.mark.parametrize("page", sorted(DOCS.rglob("*.md")), ids=lambda p: str(p.relative_to(DOCS)))
def test_no_relative_link_leaves_docs(page):
    relative = [t for t in LINK.findall(page.read_text()) if ":" not in t]
    outside = [t for t in relative if not (page.parent / t).resolve().is_relative_to(DOCS)]
    assert outside == [], "link to the repo on GitHub instead"
