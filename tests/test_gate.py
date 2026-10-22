import sys
from dataclasses import replace
from datetime import date

import pytest

from atsfill.core import registry
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
