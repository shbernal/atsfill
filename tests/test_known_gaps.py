"""Each adapter's declared known gaps against the "Known gaps" section of its doc (ADR 0014)."""

import re
from pathlib import Path

import pytest

from atsfill.adapters import adapters
from atsfill.core.adapter import Adapter

DOCS = Path(__file__).parent.parent / "docs/adapters"
# A row of the section's table: | `gap id` | what | code | postings | follow-up |
ROW = re.compile(r"^\| `([a-z0-9_]+)` \|[^|]*\| `(unsupported_\w+)` \|", re.M)


def _documented(adapter: Adapter) -> dict[str, str]:
    doc = (DOCS / f"{adapter.name}.md").read_text()
    section = re.search(r"^## Known gaps\n(.*?)(?=^## |\Z)", doc, re.M | re.S)
    return dict(ROW.findall(section[1])) if section else {}


@pytest.mark.parametrize("adapter", adapters(), ids=lambda a: a.name)
def test_the_doc_lists_every_known_gap_and_no_other(adapter):
    assert _documented(adapter) == {k: str(v) for k, v in adapter.known_gaps.items()}
