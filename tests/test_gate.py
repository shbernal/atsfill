import sys
from dataclasses import replace
from datetime import date

import pytest

from atsfill.core import browser, registry
from scripts import gate


def test_skipped_postings_are_listed_apart_from_not_ready():
    rows = [
        {"status": "ready"},
        {"status": "unsupported", "label": "unhandled", "reason": "unhandled_rendering: q is a x"},
        {"status": "unsupported", "label": "unclear", "reason": "posting_gone"},
        {"status": "unsupported", "label": "out_of_scope", "reason": "out_of_scope: v is a video"},
        {"status": "unsupported", "label": "unclear", "reason": "posting_gone"},
    ]
    assert gate.stops(rows) == [
        "not ready (1): unsupported, unhandled, unhandled_rendering: q is a x",
        "skipped (2): posting_gone",
        "skipped (1): out_of_scope: v is a video",
    ]


@pytest.fixture
def recorded(monkeypatch):
    """The adapter gate on a send-broken `acme`, on a tree with uncommitted changes, and the
    records it makes."""
    broken = registry.Broken(date(2026, 10, 19), "0.0.2", "abc1234", "user", "resume empty")
    source = registry.Source("acme", "ats", "none", ("hosted",), "acme", send_broken=broken)
    made: list = []
    monkeypatch.setattr(gate.registry, "for_adapter", lambda name: source)
    monkeypatch.setattr(gate.survey, "commit", lambda: None)
    monkeypatch.setattr(gate.browser, "version", lambda: "test")
    monkeypatch.setattr(gate, "record", lambda *args: made.append(args))
    return broken, made


def test_a_cause_alone_settles_the_recorded_break(recorded, monkeypatch):
    broken, made = recorded
    monkeypatch.setattr(sys, "argv", ["gate", "acme", "--cause", "system"])
    gate.main()
    assert made == [("acme", "send_broken", replace(broken, cause="system"))]


def test_a_cause_alone_needs_a_recorded_break(recorded, monkeypatch):
    _, made = recorded
    monkeypatch.setattr(gate.registry, "for_adapter", lambda name: None)
    monkeypatch.setattr(sys, "argv", ["gate", "acme", "--cause", "code"])
    with pytest.raises(SystemExit, match="--cause"):
        gate.main()
    assert made == []


def test_a_row_counts_at_a_commit_with_the_same_code(monkeypatch):
    changed = {"same": False, "other": True}
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda cmd, **_: type("Run", (), {"returncode": int(changed[cmd[3]])}),
    )
    gate.same_code.cache_clear()
    assert gate.same_code("head", "head")
    assert gate.same_code("same", "head")
    assert not gate.same_code("other", "head")
    assert not gate.same_code(None, "head")


def test_an_embedded_form_is_claimed_once_its_page_is_open(tmp_path, monkeypatch):
    embed = "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=1234"
    pages = {
        "https://www.example.com/careers/1": f'<iframe id="grnhse_iframe" src="{embed}"></iframe>',
        "https://www.example.com/careers/2": "<p>Our openings</p>",
    }
    monkeypatch.setattr("atsfill.adapters.greenhouse.EMBED_TIMEOUT_MS", 500)
    with browser.open_page(tmp_path) as page:
        page.context.route(
            "https://www.example.com/**",
            lambda route: route.fulfill(content_type="text/html", body=pages[route.request.url]),
        )
        keep = gate.claims("greenhouse", page)
        assert keep("https://job-boards.greenhouse.io/acme/jobs/1234")  # by its URL, unopened
        assert not keep("https://jobs.ashbyhq.com/acme/1234")  # another adapter's
        assert keep("https://www.example.com/careers/1")
        assert not keep("https://www.example.com/careers/2")
    assert not gate.claims("greenhouse", None)("https://www.example.com/careers/1")
