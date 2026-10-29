"""A hiring system of one hand-made page, tests/pages/form.html, driven through every outcome."""

from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

from atsfill.core import browser, otp
from atsfill.core.adapter import Gap, ProfileIncomplete, Refused, SignedOut, SignUpDetails
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
        self.shows: list[str] = []  # what a fill shows besides the fields without a condition

    def detect(self, url: str, page: Page | None) -> bool:
        if page is None:
            return urlsplit(url).query == "fake"
        return page.locator("meta[name=hiring-system][content=fake]").count() == 1

    def posting(self, url: str) -> str:
        return url.split("?")[0]

    def request_read(self, url: str) -> list[Field] | None:
        return self.fields if urlsplit(url).query == "api" else None

    def browser_read(self, page: Page) -> list[Field]:
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

    def shown_fields(self, page: Page) -> list[str]:
        return [f.id for f in self.fields if not f.shown_when] + self.shows

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


class FakeOpens(Fake):
    """The same form on a system that sends the application to show it: the first open sends,
    later ones continue that application; `open_raises` is what opening raises instead."""

    def __init__(self) -> None:
        super().__init__()
        self.opens = 0
        self.open_raises: Exception | None = None

    def open(self, page: Page) -> str | None:
        if self.open_raises:
            raise self.open_raises
        self.opens += 1
        return "Apply created the application" if self.opens == 1 else None


class FakeOpensForm(Fake):
    """The same form, the posting at `?fake` and its form at form.html, which it opens itself."""

    def open_form(self, page: Page, url: str) -> None:
        page.goto(url.replace("button.html?fake", "form.html"))


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


class FakeSignUp(FakeSignIn):
    """The same, whose accounts atsfill creates: `refuses` is the refusal a sign-up meets."""

    side_effects = [
        *FakeSignIn.side_effects,
        SideEffect("sign_up", "hiring_system", "login --create creates an account"),
    ]

    def __init__(self, server: str) -> None:
        super().__init__(server)
        self.created: str | None = None  # the password the account was created with
        self.details: SignUpDetails | None = None

    asks: set[str] = set()

    def sign_up(self, page: Page, email: str, password: str, details: SignUpDetails) -> None:
        if self.stops:
            raise self.stops
        self.created = self.password = password
        self.details = details
        page.evaluate("document.cookie = 'session=ok; path=/'")

    def finish_sign_up(self, page: Page, email: str, password: str, details: SignUpDetails) -> None:
        self.sign_in(page, email, password)
        self.details = details


class FakeOtp(FakeSignIn):
    """The same, whose sign-in emails an OTP, typed into `inputs` on tests/pages/otp.html."""

    def __init__(self, server: str) -> None:
        super().__init__(server)
        self.inputs = "#otp"

    def login_url(self, scope: str | None) -> str:
        return f"{self.server}/otp.html"

    def sign_in(self, page: Page, email: str, password: str) -> None:
        otp.enter(page.locator(self.inputs), email, "the sign-in")
        page.locator("#verify").click()


class FakeOtpOnly(FakeAccount):
    """The same, whose accounts have no password: the sign-in emails an OTP, typed on
    tests/pages/otp.html, and an email without an account goes on to a register step."""

    side_effects = [
        *FakeAccount.side_effects,
        SideEffect("sign_up", "hiring_system", "login --create creates an account"),
    ]
    asks = {"name"}

    def __init__(self, server: str) -> None:
        super().__init__(server)
        self.accounts: dict[str, SignUpDetails] = {}
        self.sign_ins = 0
        self.ends_sessions = 0  # the reads whose page asks to sign in though the session holds

    def browser_read(self, page: Page) -> list[Field]:
        if self.ends_sessions:
            self.ends_sessions -= 1
            raise SignedOut("login_required: the page asks to sign in")
        page.evaluate("document.cookie = 'rotated=1; path=/'")  # as a page refreshing its tokens
        return super().browser_read(page)

    def login_url(self, scope: str | None) -> str:
        return f"{self.server}/otp.html"

    def sign_in_by_otp(self, page: Page, email: str) -> None:
        if email not in self.accounts:
            raise Refused("no_account")
        self.sign_ins += 1
        otp.enter(page.locator("#otp"), email, "the sign-in")
        page.locator("#verify").click()

    def sign_up_by_otp(self, page: Page, email: str, details: SignUpDetails) -> None:
        self.accounts.setdefault(email, details)
        self.sign_in_by_otp(page, email)


class FakeProfile(FakeAccount):
    """The same, whose account profile is a profile form of two steps on tests/pages/form.html:
    `about` (the name) and `level`, which loads only once `about` is saved. `saved` is what the
    system holds; `refuses` is what the page says to a save."""

    name = "fakeprofile"
    side_effects = [
        *FakeAccount.side_effects,
        SideEffect("profile", "hiring_system", "profile --answers saves the account profile"),
    ]
    STEPS = {"about": [FIELDS[0]], "level": [FIELDS[1]]}

    def __init__(self, server: str) -> None:
        super().__init__(server)
        self.saved: dict[str, dict[str, Answer]] = {}
        self.refuses: str | None = None
        self.step = ""

    def browser_read(self, page: Page) -> list[Field]:
        if missing := [s for s in self.STEPS if s not in self.saved]:
            raise ProfileIncomplete(f"no {', '.join(missing)}")
        return self.fields

    def profile_url(self, scope: str | None) -> str:
        return f"{self.server}/form.html?account&profile"

    def profile_steps(self, page: Page) -> list[str]:
        return list(self.STEPS)

    def open_step(self, page: Page, step: str) -> bool:
        if step == "level" and "about" not in self.saved:
            return False
        page.goto(f"{self.server}/form.html?account&step={step}")
        self.step = step
        for field_id, value in self.saved.get(step, {}).items():
            target = page.locator(f'[name="{field_id}"]')
            target.fill(str(value)) if field_id == "name" else target.select_option(value)
        return True

    def profile_read(self, page: Page) -> list[Field]:
        saved = self.saved.get(self.step, {})
        return [replace(f, prefilled=saved.get(f.id)) for f in self.STEPS[self.step]]

    def profile_fill(self, page: Page, answers: dict[str, Answer]) -> None:
        self.fill(page, answers)

    def profile_read_back(self, page: Page) -> dict[str, Answer]:
        values = self.read_back(page)
        return {f.id: values[f.id] for f in self.STEPS[self.step]}

    def save(self, page: Page) -> None:
        if self.refuses:
            raise Refused(self.refuses)
        self.saved[self.step] = self.profile_read_back(page)
