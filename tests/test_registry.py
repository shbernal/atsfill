from datetime import date
from pathlib import Path

import pytest

from atsfill.adapters import adapters
from atsfill.core import registry
from scripts import backlog

ROOT = Path(__file__).parents[1]


def test_every_adapter_is_in_the_registry():
    assert all(registry.for_adapter(a.name) for a in adapters())


def test_a_source_without_a_scope_is_in_scope():
    (system,) = registry.parse('[x]\nkind = "ats"\naccount = "none"\n').values()
    assert system.scope == registry.Scope("in_scope")


@pytest.mark.parametrize(
    "entry, error",
    [
        ('kind = "form"\naccount = "none"', "kind = 'form'"),
        ('kind = "ats"\naccount = "none"\nhosted = true', "unknown keys hosted"),
        (
            'kind = "ats"\naccount = "none"\nblock = { vendor = "Imperva", step = "form" }',
            "block.vendor",
        ),
        ('kind = "ats"\naccount = "none"\nlinks_to = ["lever"]', "only an aggregator"),
        ('kind = "ats"\naccount = "none"\nscope = { is = "verified" }', "scope.is"),
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
    assert committed == registry.backlog(), "run `uv run python -m scripts.backlog`"


def test_a_scope_is_written_into_its_entry_only():
    text = '[a]\nkind = "ats"\naccount = "none"\n\n[b]\nkind = "ats"\naccount = "none"\n'
    scope = registry.Scope("supported", date(2026, 10, 19), "abc1234")
    once = registry.with_scope(text, "a", scope)
    twice = registry.with_scope(once, "a", scope)
    assert once == twice
    systems = registry.parse(twice)
    assert (systems["a"].scope, systems["b"].scope) == (scope, registry.Scope())


def test_the_committed_carriers_are_current():
    committed = (ROOT / "overrides/partials/carriers.html").read_text()
    assert committed == backlog.carriers(), "run `uv run python -m scripts.backlog`"


def test_the_committed_run_histories_are_current():
    readme = (ROOT / "README.md").read_text()
    assert readme == registry.with_run_histories(readme), "run `uv run python -m scripts.backlog`"


def test_a_system_stopped_by_a_known_gap_goes_last_with_its_reason():
    systems = registry.parse("""
[stopped]
kind = "ats"
account = "none"
share = 0.5
adapter = "stopped"
scope = { is = "in_scope", date = 2026-10-21, reason = "x" }

[open]
kind = "ats"
account = "to_read"
share = 0.1
""")
    assert systems["stopped"].scope == registry.Scope("in_scope", date(2026, 10, 21), reason="x")
    rows = [line for line in registry.backlog(systems).splitlines() if line.startswith("| ")]
    assert [r.split(" | ")[0][2:] for r in rows[1:]] == ["open", "stopped"]
    assert "[in scope: x](stopped.md)" in rows[-1]


def test_an_out_of_scope_system_joins_the_side_table_with_its_reason():
    systems = registry.parse("""
[video]
kind = "ats"
account = "none"
share = 0.5
scope = { is = "out_of_scope", date = 2026-10-22, reason = "every form records a video" }

[open]
kind = "ats"
account = "none"
share = 0.1
""")
    assert systems["video"].scope == registry.Scope(
        "out_of_scope", date(2026, 10, 22), reason="every form records a video"
    )
    main, side = registry.backlog(systems).split("counted and not adapted:")
    assert "| open |" in main and "| video |" not in main
    assert "| video |" in side and "out of scope: every form records a video" in side
