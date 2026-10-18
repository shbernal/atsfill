import time

import pytest

from atsfill import core
from atsfill.core import browser
from atsfill.core.browser import OutOfTime
from atsfill.core.results import Status

# Apart from test_browser.py: one sync Playwright runs per thread at a time.


def test_budget_interrupts_the_browser(server, tmp_path):
    start = time.monotonic()
    with pytest.raises(OutOfTime), browser.open_page(tmp_path, seconds=15) as page:
        page.goto(f"{server}/button.html")
        page.wait_for_timeout(60_000)
    assert time.monotonic() - start < 30


def test_out_of_time_before_the_click_is_blocked(server, tmp_path, monkeypatch):
    def slow(*args, **kwargs):
        raise OutOfTime("time budget of 180s ran out")

    monkeypatch.setattr("atsfill.core.commands._on_page", slow)
    result = core.fill(f"{server}/button.html", {}, data_dir=tmp_path)
    assert result.status is Status.BLOCKED
    assert result.reason and result.reason.startswith("time_budget")
