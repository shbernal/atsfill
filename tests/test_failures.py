from scripts import run_history
from scripts.failures import Issue, actions, body, failures, title

DEV = "a" * 40
FIX = "f" * 40
TITLE = title("ashby", "fill", "form_errors")


def row(verdict, at, posting="p1", version="0.0.4", source="user", commit=None, **kw):
    command, reason = kw.get("command", "fill"), kw.get("reason", "form_errors")
    reason = reason if verdict == "fail" else None
    return [
        "ashby", command, verdict, posting, version, source, commit, f"2026-10-{at}T10:00:00Z",
        reason,
    ]  # fmt: skip


def found(*rows):
    return failures(run_history.runs(list(rows)))


def fixed_from(version):
    """A `contains` for which releases from `version` on contain the fix."""
    return lambda r, commit: commit == FIX and r.version >= version


def never(r, commit):
    return False


def test_a_failure_without_a_pass_before_it_never_passed():
    [f] = found(row("fail", 20), row("excluded", 21), row("fail", 22))
    assert f.points_at == "never passed" and len(f.fails) == 2 and f.ended is None


def test_a_pass_before_it_makes_it_a_regression_pointing_at_the_system_or_the_code():
    [f] = found(row("pass", 20), row("fail", 21))
    assert f.points_at == "the hiring system"
    [f] = found(row("pass", 20, version="0.0.3"), row("fail", 21))
    assert f.points_at == "the code"
    dev = {"source": "dev"}
    [f] = found(row("pass", 20, commit=DEV, **dev), row("fail", 21, commit="b" * 40, **dev))
    assert f.points_at == "the code"


def test_a_pass_on_another_build_ends_it_a_pass_on_a_failing_build_does_not():
    [f] = found(row("fail", 20), row("pass", 21))
    assert f.ended is None
    [f] = found(row("fail", 20), row("pass", 21, version="0.0.5"))
    assert f.ended and f.ended.version == "0.0.5"


def test_its_reason_is_its_latest_failing_run_s_and_read_is_not_counted():
    [f] = found(row("fail", 20), row("fail", 21, reason="captcha"))
    assert f.reason == "captcha"
    assert found(row("fail", 20, command="read")) == []


def test_an_issue_per_system_command_and_reason_with_versions_and_count_never_hashes():
    fs = found(
        row("fail", 20, posting="secret-1"),
        row("pass", 20, posting="secret-2", version="0.0.3"),
        row("fail", 21, posting="secret-2"),
        row("fail", 21, posting="secret-3", reason="captcha"),
        row("fail", 21, posting="secret-4", command="send"),
    )
    opened = actions(fs, [], never)
    assert [(a.kind, a.title) for a in opened] == [
        ("open", "Failure: ashby fill captcha"),
        ("open", TITLE),
        ("open", "Failure: ashby send form_errors"),
    ]
    text = opened[1].text
    assert "on 2 postings" in text and "| 0.0.4 | never | 1 | never passed |" in text
    assert "| 0.0.4 | 0.0.3 | 1 | the code |" in text
    assert "scripts.failures ashby fill form_errors" in text
    assert "secret" not in "".join(a.text + a.title for a in opened)


def test_an_open_issue_is_edited_only_when_its_body_changes_and_closed_once_it_ends():
    fs = found(row("fail", 20))
    same = Issue(7, TITLE, body(fs))
    assert actions(fs, [same], never) == []
    [edit] = actions(fs, [Issue(7, TITLE, "earlier")], never)
    assert (edit.kind, edit.number) == ("edit", 7)
    [close] = actions(found(row("fail", 20), row("pass", 22, version="0.0.5")), [same], never)
    assert close.kind == "close" and "pass again, on 0.0.5" in close.text
    [left] = actions([], [same], never)
    assert left.kind == "close" and "no posting fails this way" in left.text


def test_after_a_fix_only_a_build_containing_it_reopens_the_issue():
    closed = Issue(7, TITLE, "", "CLOSED", "2026-10-22T00:00:00Z", FIX)
    contains = fixed_from("0.0.5")
    assert actions(found(row("fail", 23)), [closed], contains) == []
    [reopen] = actions(found(row("fail", 23, version="0.0.5")), [closed], contains)
    assert (reopen.kind, reopen.number) == ("reopen", 7) and "on 0.0.5" in reopen.comment
    reopened = Issue(7, TITLE, "", fix=FIX)
    [close] = actions(found(row("fail", 23)), [reopened], contains)
    assert close.kind == "close"


def test_a_closed_issue_without_a_fix_reopens_on_a_failure_after_it_closed():
    closed = Issue(7, TITLE, "", "CLOSED", "2026-10-22T00:00:00Z")
    assert actions(found(row("fail", 21)), [closed], never) == []
    [reopen] = actions(found(row("fail", 23)), [closed], never)
    assert reopen.kind == "reopen"
    [edit] = actions(found(row("fail", 23)), [Issue(8, TITLE, ""), closed], never)
    assert (edit.kind, edit.number) == ("edit", 8)
