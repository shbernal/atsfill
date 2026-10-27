import re
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Protocol, Self, runtime_checkable

from atsfill.core.browser import Page, Request
from atsfill.core.results import Answer, Field, SideEffect


class Blocked(Exception):
    """The system stopped the command: a captcha, a low reCAPTCHA score, an OTP, a login
    wall.

    The message is the result's reason, `code` or `code: detail`.
    """


class Unsupported(Exception):
    """What stops the form before any fill: a flow the adapter does not handle, or a fact about
    the posting (`posting_gone`, `external_step`). The message is `code: detail`.

    A field atsfill cannot fill is not raised: `browser_read` returns it with `unfillable` set.
    """


class Gap(StrEnum):
    """The code of a known gap (ADR 0014): wanted, not handled yet."""

    RENDERING = "unsupported_rendering"
    FLOW = "unsupported_flow"


def known_gap(adapter: Adapter, gap: str, detail: str) -> str:
    """The reason for one of the adapter's declared known gaps: `<code>: <gap id>: <detail>`."""
    return f"{adapter.known_gaps[gap]}: {gap}: {detail}"


def out_of_scope(field_id: str, what: str) -> str:
    """The reason for a field that needs the user live (ADR 0014)."""
    return f"out_of_scope: {field_id} is {what}"


class Refused(Exception):
    """The system turned down a sign-in or a sign-up for a reason it states:
    `email_in_use`, `email_domain_rejected`, `password_rule`, `wrong_password`.

    The message is the result's reason, `code` or `code: detail`.
    """


class NotTaken(Exception):
    """The page would not take an answer the user gave: no suggestion or option holds it.

    The message is the detail.
    """


class Adapter(Protocol):
    """What an adapter knows: its system's page, nothing about when a send is allowed."""

    name: str
    side_effects: list[SideEffect]
    known_gaps: dict[str, Gap]  # by id, each listed under "Known gaps" in the adapter's doc

    def detect(self, url: str, page: Page | None) -> bool:
        """Claims the form from its URL (`page` is None), else from the loaded page."""
        ...

    def posting(self, url: str) -> str:
        """The posting's key for the double-send guard: every URL of one posting gives the same."""
        ...

    def browser_read(self, page: Page) -> list[Field]:
        """Every field, one atsfill cannot fill included with its reason in `unfillable`."""
        ...

    def fill(self, page: Page, answers: dict[str, Answer]) -> None: ...

    def read_back(self, page: Page) -> dict[str, Answer]: ...

    def submit(self, page: Page) -> str | None:
        """Clicks submit; returns the evidence (a URL or a network response), None without.

        Raises `Blocked` when the system refuses the send.
        """
        ...


@runtime_checkable
class Filtered(Protocol):
    """An adapter whose system shows a cookie banner the blocker's lists miss: its own
    uBlock Origin cosmetic filters hide it (ADR 0004)."""

    filters: list[str]


@runtime_checkable
class NeedsAccount(Protocol):
    """An adapter whose system needs a signed-in session, one per user and account scope
    (ADR 0015)."""

    name: str
    per_employer: bool  # each employer keeps its own accounts, so a scope is always given

    def account_scope(self, url: str) -> str | None:
        """The account scope of a posting's URL: None where an account covers the whole system,
        the employer where each keeps its own, the same for every URL of that employer."""
        ...

    def login_url(self, scope: str | None) -> str: ...

    def signed_in(self, request: Request, scope: str | None) -> bool:
        """Whether the cookies `request` carries are signed in, from one request to the system."""
        ...


@runtime_checkable
class SignsIn(NeedsAccount, Protocol):
    """An adapter atsfill can sign in through with an email and a password (ADR 0015)."""

    def sign_in(self, page: Page, email: str, password: str) -> None:
        """Signs in on the page `login_url` loaded. Raises `Blocked` for what the system waits
        for (`email_link`, `email_otp`, `sms_otp`, `captcha`, the OTP command's reasons),
        `Refused` for what it turns down, `Unsupported` for a page it does not know."""
        ...


ADDRESS = ("street", "city", "state", "zip", "country")
PHONE = re.compile(r"\+[1-9][0-9]{6,14}")  # E.164
COUNTRY = re.compile(r"[A-Z]{2}")  # ISO 3166 alpha-2


@dataclass(frozen=True)
class SignUpDetails:
    """What a sign-up asks of the user besides the email and the password, from the file
    `login --create --details` takes (ADR 0015). atsfill stores none of it."""

    name: str | None = None
    phone: str | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    country: str | None = None

    @classmethod
    def parse(cls, data: object) -> Self:
        """From `{"name", "phone", "address": {"street", "city", "state", "zip", "country"}}`,
        every key optional. Raises `ValueError` naming what is wrong."""
        if not isinstance(data, dict):
            raise ValueError("sign-up details: expected a JSON object")
        address = data.get("address", {})
        if not isinstance(address, dict):
            raise ValueError("sign-up details: address: expected a JSON object")
        if unknown := (data.keys() - {"name", "phone", "address"}) | (address.keys() - {*ADDRESS}):
            raise ValueError(f"sign-up details: unknown keys {', '.join(sorted(unknown))}")
        flat = {k: v for k, v in data.items() if k != "address"} | address
        for key, value in flat.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"sign-up details: {key}: expected a non-empty string")
        if "phone" in flat and not PHONE.fullmatch(flat["phone"]):
            raise ValueError("sign-up details: phone: expected E.164, as +15551234567")
        if "country" in flat and not COUNTRY.fullmatch(flat["country"]):
            raise ValueError("sign-up details: country: expected ISO 3166 alpha-2, as US")
        return cls(**flat)

    def missing(self, asks: set[str]) -> list[str]:
        """The details `asks` names that these lack, in the file's order."""
        return [f.name for f in fields(self) if f.name in asks and getattr(self, f.name) is None]


@runtime_checkable
class CreatesAccount(SignsIn, Protocol):
    """An adapter atsfill can create an account through, where the system's sign-up allows it.
    Account creation is a side effect it declares (ADR 0008)."""

    asks: set[str]  # the `SignUpDetails` fields its sign-up requires

    def sign_up(self, page: Page, email: str, password: str, details: SignUpDetails) -> None:
        """Creates the account from the page `login_url` loaded, signed in once it returns.
        Raises as `sign_in` does; `Refused` names the rule a `password_rule` page showed. The
        details in `asks` are given; optional consents the sign-up offers stay declined."""
        ...


@runtime_checkable
class FinishesSignUp(CreatesAccount, Protocol):
    """An adapter whose system can keep an account half made: signed up, but a step the
    sign-up shows after it left unfinished, which every sign-in then shows again."""

    def finish_sign_up(self, page: Page, email: str, password: str, details: SignUpDetails) -> None:
        """Signs in as `sign_in` does and, where the system shows the unfinished step, gives it
        `details` as `sign_up` would."""
        ...


@runtime_checkable
class ReadsByRequest(Adapter, Protocol):
    """An adapter with a request read: a form's fields listed without a browser (ADR 0011)."""

    def request_read(self, url: str) -> list[Field] | None:
        """The same fields as `browser_read`; None when it cannot for this URL."""
        ...


@runtime_checkable
class ListsShownFields(Adapter, Protocol):
    """An adapter whose form can show fields after an answer: once filled, it says which the
    page shows, so that one `browser_read` did not list stops the fill (ADR 0005)."""

    def shown_fields(self, page: Page) -> list[str]:
        """The ids of the fields the filled page shows, each as `browser_read` would name it."""
        ...
