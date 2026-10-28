from scripts import run_history
from scripts.regressions import Issue, actions, body, regressions, title

DEV = "a" * 40


def row(verdict, at, posting="p1", version="0.0.4", source="user", commit=None, command="fill"):
    return ["ashby", command, verdict, posting, version, source, commit, f"2026-10-{at}T10:00:00Z"]


def found(*rows):
    return regressions(run_history.runs(list(rows)))


def test_a_pass_then_a_fail_on_the_same_build_points_at_the_hiring_system():
    [r] = found(row("pass", 20), row("excluded", 21), row("fail", 22), row("fail", 23))
    assert (r.passed.timestamp[8:10], r.failed.timestamp[8:10]) == ("20", "22")
    assert r.points_at == "the hiring system" and r.ended is None


def test_a_newer_build_failing_points_at_the_code():
    [r] = found(row("pass", 20, version="0.0.3"), row("fail", 21))
    assert r.points_at == "the code"
    dev = {"source": "dev"}
    [r] = found(row("pass", 20, commit=DEV, **dev), row("fail", 21, commit="b" * 40, **dev))
    assert r.points_at == "the code"


def test_no_regression_without_a_pass_before_the_fail_or_across_postings_and_commands():
    assert found(row("fail", 20), row("pass", 21)) == []
    assert found(row("pass", 20), row("fail", 21, posting="p2")) == []
    assert found(row("pass", 20, command="read"), row("fail", 21, command="read")) == []
    assert found(row("pass", 20), row("fail", 21, command="send")) == []


def test_a_pass_on_another_build_ends_it_a_pass_on_the_failing_build_does_not():
    [r] = found(row("pass", 20), row("fail", 21), row("pass", 22))
    assert r.ended is None
    [r] = found(row("pass", 20), row("fail", 21), row("pass", 22, version="0.0.5"))
    assert r.ended and r.ended.version == "0.0.5"


def test_an_issue_per_system_and_command_with_versions_and_count_never_hashes():
    regs = found(
        row("pass", 20, posting="secret-1"),
        row("fail", 21, posting="secret-1"),
        row("pass", 20, posting="secret-2", version="0.0.3"),
        row("fail", 21, posting="secret-2"),
        row("pass", 20, posting="secret-3", command="send"),
        row("fail", 21, posting="secret-3", command="send"),
    )
    opened = actions(regs, [])
    assert [(a.kind, a.title) for a in opened] == [
        ("open", "Regression: ashby fill"),
        ("open", "Regression: ashby send"),
    ]
    text = opened[0].text
    assert "2 postings" in text and "| 0.0.4 | 0.0.4 | 1 | the hiring system |" in text
    assert "| 0.0.3 | 0.0.4 | 1 | the code |" in text
    assert "scripts.regressions ashby fill" in text
    assert "secret" not in "".join(a.text + a.title for a in opened)


def test_an_open_issue_is_edited_only_when_its_body_changes():
    regs = found(row("pass", 20), row("fail", 21))
    same = Issue(7, title("ashby", "fill"), body(regs))
    assert actions(regs, [same]) == []
    [edit] = actions(regs, [Issue(7, same.title, "1 posting, earlier")])
    assert (edit.kind, edit.number) == ("edit", 7)


def test_an_ended_regression_closes_its_issue_saying_why():
    issue = Issue(7, title("ashby", "fill"), "")
    regs = found(row("pass", 20), row("fail", 21), row("pass", 22, version="0.0.5"))
    [passed] = actions(regs, [issue])
    assert (passed.kind, passed.number) == ("close", 7) and "pass again, on 0.0.5" in passed.text
    [left] = actions([], [issue])
    assert left.kind == "close" and "left the 30-day window" in left.text
