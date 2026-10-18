"""A hiring system of one hand-made page, tests/pages/form.html, driven through every outcome."""

from urllib.parse import urlsplit

from atsfill.core import browser
from atsfill.core.browser import Page, Request
from atsfill.core.results import Answer, Field, FieldType, SideEffect

FIELDS = [
    Field("name", "Name", FieldType.TEXT, True),
    Field("level", "Level", FieldType.CHOICE, False, ["Native", "Fluent"]),
    Field("langs", "Languages", FieldType.MULTI_CHOICE, False, ["French", "English"]),
    Field("cv", "Resume", FieldType.FILE, False),
]


class Fake:
    name = "fake"
    side_effects = [SideEffect("fill", "hiring_system", "answers are saved as typed")]

    def __init__(self) -> None:
        self.fill_raises: Exception | None = None
        self.typo = False  # types something other than the answer
        self.submit_raises: Exception | None = None  # after the click
        self.evidence = True

    def detect(self, url: str, page: Page | None) -> bool:
        if page is None:
            return urlsplit(url).query == "fake"
        return page.locator("meta[name=hiring-system][content=fake]").count() == 1

    def posting(self, url: str) -> str:
        return url.split("?")[0]

    def read_api(self, url: str) -> list[Field] | None:
        return FIELDS if urlsplit(url).query == "api" else None

    def read_form(self, page: Page) -> list[Field]:
        return FIELDS

    def fill(self, page: Page, answers: dict[str, Answer]) -> None:
        if self.fill_raises:
            raise self.fill_raises
        for field_id, answer in answers.items():
            target = page.locator(f'[name="{field_id}"]')
            match field_id:
                case "name":
                    target.fill(f"{answer}x" if self.typo else str(answer))
                case "level":
                    target.select_option(answer)
                case "langs":
                    for value in answer:
                        target.and_(page.locator(f'[value="{value}"]')).check()
                case "cv":
                    target.set_input_files(answer)

    def read_back(self, page: Page) -> dict[str, Answer]:
        return page.evaluate("""() => {
            const f = document.forms[0];
            return {
                name: f.elements.name.value,
                level: f.level.value,
                langs: [...f.querySelectorAll("[name=langs]:checked")].map(e => e.value),
                cv: f.cv.files[0]?.name ?? "",
            };
        }""")

    def submit(self, page: Page) -> str | None:
        browser.click(page.get_by_role("button", name="Submit"))
        if self.submit_raises:
            raise self.submit_raises
        page.wait_for_url("**?sent=1")
        return page.url if self.evidence else None


class FakeAccount(Fake):
    """The same form on a system that needs an account, signed in by tests/pages/login.html."""

    name = "fakeaccount"

    def __init__(self, server: str) -> None:
        super().__init__()
        self.server = server
        self.login_url = f"{server}/login.html"

    def detect(self, url: str, page: Page | None) -> bool:
        return urlsplit(url).query == "account"

    def signed_in(self, request: Request) -> bool:
        return request.get(f"{self.server}/me").ok
