from scripts import run_history
from scripts.run_history import Run

MAIN = "a" * 40


def row(system="ashby", command="fill", verdict="pass", posting="p1", source="user", **kw):
    version, commit, at = kw.get("version", "0.0.4"), kw.get("commit"), kw.get("at", "2026-10-20")
    return [system, command, verdict, posting, version, source, commit, at]


def known(rows):
    return run_history.known(run_history.runs(rows), {"ashby"}, {"0.0.4"}, {MAIN})


def test_unknown_systems_commands_versions_and_commits_are_dropped():
    rows = [
        row(),
        row(system="acme"),
        row(command="probe"),
        row(version="9.9.9"),
        row(source="dev", commit=MAIN),
        row(source="dev", commit="b" * 40),
        row(source="dev"),
        row(source="other"),
        row(posting=None),
    ]
    assert [(r.source, r.commit) for r in known(rows)] == [("user", None), ("dev", MAIN)]


def test_a_mark_per_posting_and_version_at_its_latest_run_newest_first():
    rows = [
        row(posting="p1", verdict="fail", at="2026-10-20T10:00:00Z"),
        row(posting="p1", verdict="pass", at="2026-10-21T10:00:00Z"),
        row(posting="p2", verdict="fail", at="2026-10-22T10:00:00Z"),
        row(posting="p3", verdict="excluded", at="2026-10-23T10:00:00Z"),
        row(posting="p4", command="send", at="2026-10-24T10:00:00Z"),
    ]
    marks = run_history.marks(known(rows), "ashby", "fill")
    assert [(m.posting, m.verdict) for m in marks] == [("p2", "fail"), ("p1", "pass")]


def test_an_excluded_latest_run_hides_the_mark():
    rows = [row(verdict="fail"), row(verdict="excluded", at="2026-10-21T10:00:00Z")]
    assert run_history.marks(known(rows), "ashby", "fill") == []


def test_dev_marks_are_hollow_and_name_their_commit_hashes_never_drawn():
    dev = Run("ashby", "fill", "fail", "secret-hash", "0.0.4", "dev", MAIN, "2026-10-20T10:00:00Z")
    svg = run_history.svg("ashby", [dev])
    assert 'fill="none" stroke="#f7768e"' in svg
    assert f"dev {MAIN[:7]}" in svg and "0.0.4" in svg
    assert "secret-hash" not in svg


def test_a_system_with_no_runs_has_an_empty_run_history():
    page = run_history.page(["ashby", "lever"], [])
    assert "[lever](adapters/lever.md)" in page
    assert "<circle" not in run_history.svg("lever", [])


def test_the_legend_draws_each_verdict_filled_for_users_and_hollow_for_dev():
    legend = run_history.legend()
    assert legend.count("<circle") == 4
    for color in (run_history.GREEN, run_history.RED):
        assert f'fill="{color}"' in legend and f'fill="none" stroke="{color}"' in legend
