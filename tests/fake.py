"""A hiring system of one hand-made page, tests/pages/form.html, driven through every outcome."""

from urllib.parse import parse_qs, urlsplit

from atsfill.core import browser
from atsfill.core.adapter import Gap, Refused
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
    known_gaps = {"ranking": Gap.RENDERING}
    side_effects = [SideEffect("fill", "hiring_system", "answers are saved as typed")]

    def __init__(self) -> None:
        self.fill_raises: Exception | None = None
        self.typo = False  # types something other than the answer
        self.submit_raises: Exception | None = None  # after the click
        self.evidence = True
        self.fields = FIELDS

    def detect(self, url: str, page: Page | None) -> bool:
        if page is None:
            return urlsplit(url).query == "fake"
        return page.locator("meta[name=hiring-system][content=fake]").count() == 1

    def posting(self, url: str) -> str:
        return url.split("?")[0]

    def read_api(self, url: str) -> list[Field] | None:
        return self.fields if urlsplit(url).query == "api" else None

    def read_form(self, page: Page) -> list[Field]:
        return self.fields

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
    per_employer = False

    def __init__(self, server: str) -> None:
        super().__init__()
        self.server = server

    def detect(self, url: str, page: Page | None) -> bool:
        return "account" in parse_qs(urlsplit(url).query, keep_blank_values=True)

    def account_scope(self, url: str) -> str | None:
        return None

    def login_url(self, scope: str | None) -> str:
        return f"{self.server}/login.html"

    def signed_in(self, request: Request, scope: str | None) -> bool:
        return request.get(f"{self.server}/me").ok


class FakeEmployerAccount(FakeAccount):
    """The same, with an account per employer: `?account&employer=<name>`, in any case."""

    per_employer = True

    def account_scope(self, url: str) -> str | None:
        employer = parse_qs(urlsplit(url).query).get("employer")
        return employer[0].lower() if employer else None


class FakeSignIn(FakeAccount):
    """The same, signed in by atsfill: `password` is the account's, `stops` what the system
    raises instead of signing in."""

    password = "correct horse battery staple"

    def __init__(self, server: str) -> None:
        super().__init__(server)
        self.stops: Exception | None = None
        self.sign_ins = 0

    def login_url(self, scope: str | None) -> str:
        return f"{self.server}/button.html"

    def sign_in(self, page: Page, email: str, password: str) -> None:
        self.sign_ins += 1
        if self.stops:
            raise self.stops
        if password != self.password:
            raise Refused("wrong_password")
        page.evaluate("document.cookie = 'session=ok; path=/'")
