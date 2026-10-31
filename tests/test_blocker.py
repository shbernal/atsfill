from atsfill.adapters import filters
from atsfill.core import browser


def test_the_first_page_a_browser_opens_has_its_cookie_banner_hidden(tmp_path):
    url = "https://careers.example.com/"
    body = """<button style="position: fixed; top: 0">Apply</button>
    <div id="onetrust-consent-sdk" style="position: fixed; inset: 0">We use cookies</div>"""
    with browser.open_page(tmp_path) as page:
        page.route(url, lambda route: route.fulfill(content_type="text/html", body=body))
        page.goto(url)
        browser.click(page.get_by_role("button", name="Apply"))


def test_a_launch_gives_the_blocker_the_adapters_filters(tmp_path):
    with browser.open_page(tmp_path):
        written = (browser.BLOCKER / browser.FILTERS).read_text().splitlines()
    assert written == filters() and "##career-cookie-consent" in written
