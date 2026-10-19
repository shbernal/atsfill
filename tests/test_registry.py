from datetime import date
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


def test_a_state_is_written_into_its_entry_only():
    text = '[a]\nkind = "ats"\naccount = "none"\n\n[b]\nkind = "ats"\naccount = "none"\n'
    v = registry.Verification(date(2026, 10, 19), "0.0.2", "gate", "abc1234")
    once = registry.with_state(text, "a", "dry_run_verified", v)
    twice = registry.with_state(once, "a", "dry_run_verified", v)
    assert once == twice
    systems = registry.parse(twice)
    assert (systems["a"].dry_run_verified, systems["b"].state) == (v, None)


def test_a_verified_send_ends_a_break():
    text = '[a]\nkind = "ats"\naccount = "none"\n'
    broken = registry.Broken(date(2026, 10, 19), "0.0.2", "abc1234", "user", "resume empty")
    text = registry.with_state(text, "a", "send_broken", broken)
    assert registry.parse(text)["a"].send_broken == broken
    v = registry.Verification(date(2026, 10, 20), "0.0.3", "user", "def5678")
    (system,) = registry.parse(registry.with_state(text, "a", "send_verified", v)).values()
    assert (system.send_verified, system.send_broken) == (v, None)


def test_a_break_names_a_known_cause():
    entry = (
        'send_broken = { date = 2026-10-19, version = "0.0.2", commit = "a", by = "u",'
        ' reason = "r", cause = "weather" }'
    )
    with pytest.raises(ValueError, match="send_broken.cause"):
        registry.parse(f'[x]\nkind = "ats"\naccount = "none"\n{entry}\n')


@pytest.mark.parametrize(
    "verified, release", [("0.0.2", "this_release"), ("0.0.1", "older"), ("0.1.0", "newer")]
)
def test_a_verified_release_against_the_installed_one(verified, release):
    assert registry.release(verified, "0.0.2") == release


def test_the_committed_badges_are_current():
    readme = (ROOT / "README.md").read_text()
    assert readme == registry.with_badges(readme), "run `uv run scripts/backlog.py`"
