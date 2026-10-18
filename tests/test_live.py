"""Dry runs on real postings, named in .env: `pytest -m live`. Never a send."""

import json
import os
from pathlib import Path

import pytest

from atsfill import core
from atsfill.adapters.ashby import Ashby
from atsfill.adapters.greenhouse import Greenhouse
from atsfill.core import browser
from atsfill.core.results import Status

pytestmark = pytest.mark.live


def env(name: str) -> str:
    if not (value := os.environ.get(name)):
        pytest.skip(f"{name} is not set in .env")
    return value


@pytest.mark.parametrize("adapter", [Ashby, Greenhouse])
def test_reads_the_same_fields_over_http_and_from_the_page(adapter, tmp_path):
    """Same ids, types and required flags; the API carries no length limit or file types."""
    url = env(f"ATSFILL_LIVE_{adapter.__name__.upper()}_URL")
    over_http = adapter().read_api(url)
    assert over_http is not None
    on_page = adapter()
    with browser.open_page(tmp_path) as page:
        page.goto(url)
        on_page.detect(url, page)
        from_page = on_page.read_form(page)
    shape = lambda fields: sorted((f.id, f.type, f.required) for f in fields)  # noqa: E731
    assert shape(from_page) == shape(over_http)


@pytest.mark.parametrize("system", ["ashby", "teamtailor", "greenhouse"])
def test_dry_run_is_ready(system, tmp_path):
    url = env(f"ATSFILL_LIVE_{system.upper()}_URL")
    answers = json.loads(Path(env(f"ATSFILL_LIVE_{system.upper()}_ANSWERS")).read_text())
    result = core.fill(url, answers, data_dir=tmp_path)
    assert (result.status, result.system) == (Status.READY, system), result.reason
