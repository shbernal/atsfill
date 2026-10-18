"""Dry runs on real postings, named in .env: `pytest -m live`. Never a send."""

import json
import os
from pathlib import Path

import pytest

from atsfill import core
from atsfill.adapters.ashby import Ashby
from atsfill.core import browser
from atsfill.core.results import Status

pytestmark = pytest.mark.live


def env(name: str) -> str:
    if not (value := os.environ.get(name)):
        pytest.skip(f"{name} is not set in .env")
    return value


def test_ashby_reads_the_same_fields_over_http_and_from_the_page(tmp_path):
    url = env("ATSFILL_LIVE_ASHBY_URL")
    over_http = Ashby().read_api(url)
    assert over_http is not None
    adapter = Ashby()
    with browser.open_page(tmp_path) as page:
        page.goto(url)
        assert adapter.read_form(page) == over_http


def test_ashby_dry_run_is_ready(tmp_path):
    url = env("ATSFILL_LIVE_ASHBY_URL")
    answers = json.loads(Path(env("ATSFILL_LIVE_ASHBY_ANSWERS")).read_text())
    result = core.fill(url, answers, data_dir=tmp_path)
    assert result.status is Status.READY, result.reason
