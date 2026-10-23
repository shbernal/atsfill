import threading
import time

import pytest

from atsfill.core import browser
from atsfill.core.browser import OutOfTime, Overlaid


@pytest.fixture(scope="module")
def page(server, tmp_path_factory):
    with browser.open_page(tmp_path_factory.mktemp("data"), seconds=600) as page:
        page.goto(f"{server}/overlaid.html")
        yield page


def test_click_reaches_a_free_target(page):
    browser.click(page.locator("#free"))


def test_click_reports_what_covers_the_target(page):
    with pytest.raises(Overlaid, match=r'div\.banner\.sheet "Saving your answers"'):
        browser.click(page.locator("#overlaid"))


def test_click_reaches_a_target_in_a_shadow_root(page):
    browser.click(page.locator("#shadowed"))


def test_click_reaches_a_label_whose_box_centre_is_beside_its_text(page):
    browser.click(page.locator("#wrapped"))


def test_click_reaches_a_target_in_a_cross_origin_frame(page):
    browser.click(page.frame_locator("#open-frame").locator("#inner"))


def test_click_reports_what_covers_a_cross_origin_frame(page):
    with pytest.raises(Overlaid, match=r"div#frame-overlay"):
        browser.click(page.frame_locator("#overlaid-frame").locator("#inner"))


def test_a_second_command_waits_on_the_lock(tmp_path):
    held, release = threading.Event(), threading.Event()

    def hold():
        with browser.lock(tmp_path, time.monotonic() + 10):
            held.set()
            release.wait()

    threading.Thread(target=hold).start()
    held.wait()
    try:
        with pytest.raises(OutOfTime), browser.lock(tmp_path, time.monotonic() + 0.3):
            pass
    finally:
        release.set()
    with browser.lock(tmp_path, time.monotonic() + 1):
        pass
