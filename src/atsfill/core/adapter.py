from typing import Protocol, runtime_checkable

from atsfill.core.browser import Page, Request
from atsfill.core.results import Answer, Field, SideEffect


class Blocked(Exception):
    """The system stopped the command: a captcha, a low reCAPTCHA score, an emailed code, a
    login wall.

    The message is the result's reason, `code` or `code: detail`.
    """


class Unsupported(Exception):
    """A rendering or a flow the adapter does not handle; the message is `code: detail`."""


class NotTaken(Exception):
    """The page would not take an answer the user gave: no suggestion or option holds it.

    The message is the detail.
    """


class Adapter(Protocol):
    """What an adapter knows: its system's page, nothing about when a send is allowed."""

    name: str
    side_effects: list[SideEffect]

    def detect(self, url: str, page: Page | None) -> bool:
        """Claims the form from its URL (`page` is None), else from the loaded page."""
        ...

    def posting(self, url: str) -> str:
        """The posting's key for the double-send guard: every URL of one posting gives the same."""
        ...

    def read_form(self, page: Page) -> list[Field]: ...

    def fill(self, page: Page, answers: dict[str, Answer]) -> None: ...

    def read_back(self, page: Page) -> dict[str, Answer]: ...

    def submit(self, page: Page) -> str | None:
        """Clicks submit; returns the evidence (a URL or a network response), None without.

        Raises `Blocked` when the system refuses the send.
        """
        ...


@runtime_checkable
class NeedsAccount(Protocol):
    """An adapter whose system needs a signed-in session (ADR 0007)."""

    login_url: str

    def signed_in(self, request: Request) -> bool:
        """Whether the cookies `request` carries are signed in, from one request to the system."""
        ...


@runtime_checkable
class ReadsOverHttp(Adapter, Protocol):
    """An adapter that can list a form's fields over HTTP, without a browser (ADR 0011)."""

    def read_api(self, url: str) -> list[Field] | None:
        """The same fields as `read_form`; None when it cannot for this URL."""
        ...
