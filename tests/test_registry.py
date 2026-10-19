from pathlib import Path

import pytest

from atsfill.adapters import ADAPTERS
from atsfill.core import registry

ROOT = Path(__file__).parents[1]


def test_every_adapter_is_in_the_registry():
    assert all(registry.for_adapter(a.name) for a in ADAPTERS)


def test_the_furthest_state_wins():
    (system,) = registry.parse("""
[x]
kind = "ats"
account = "none"
mapped = { date = 2026-10-01, version = "0.0.1", by = "agent" }
dry_run_verified = { date = 2026-10-02, version = "0.0.1", by = "agent" }
""").values()
    assert system.state == "dry_run_verified"


@pytest.mark.parametrize(
    "entry, error",
    [
        ('kind = "form"\naccount = "none"', "kind = 'form'"),
        ('kind = "ats"\naccount = "none"\nhosted = true', "unknown keys hosted"),
        (
            'kind = "ats"\naccount = "none"\nblock = { vendor = "Imperva", step = "form" }',
            "block.vendor",
        ),
    ],
)
def test_an_invalid_entry_is_refused(entry, error):
    with pytest.raises(ValueError, match=error):
        registry.parse(f"[x]\n{entry}\n")


def test_the_backlog_puts_no_account_first_and_blocked_last():
    block = (
        'block = { vendor = "DataDome", signal = "x-datadome", step = "posting",'
        ' date = 2026-10-01, version = "0.0.2", camoufox = "0.5.8", browser = "156" }'
    )
    systems = registry.parse(f"""
[blocked]
kind = "ats"
account = "none"
share = 0.5
{block}

[read]
kind = "ats"
account = "to_read"
share = 0.3

[small]
kind = "ats"
account = "none"
share = 0.01

[big]
kind = "ats"
account = "none"
share = 0.1

[linkedout]
kind = "aggregator"
account = "none"
""")
    rows = [line.split(" | ")[0][2:] for line in registry.backlog(systems).splitlines()]
    sources = [r for r in rows if r in systems]
    assert sources == ["big", "small", "read", "blocked", "linkedout"]


def test_the_committed_backlog_is_current():
    committed = (ROOT / "docs/adapters/README.md").read_text()
    assert committed == registry.backlog(), "run `uv run scripts/backlog.py`"
