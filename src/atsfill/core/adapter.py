from typing import Protocol

from atsfill.core.browser import Page
from atsfill.core.results import Answer, Field, SideEffect


class Blocked(Exception):
    """The system stopped the command: a captcha, a score refusal, an emailed code, a login wall."""


class Unsupported(Exception):
    """A form shape the adapter does not handle."""


class Adapter(Protocol):
    """What an adapter knows: its system's page, nothing about when a send is allowed."""

    name: str
    side_effects: list[SideEffect]
    needs_session: bool

    def detect(self, url: str, page: Page) -> bool: ...

    def posting(self, url: str) -> str:
        """The posting's key for the double-send guard: every URL of one posting gives the same."""
        ...

    def read_api(self, url: str) -> list[Field] | None:
        """Lists the fields over HTTP, without a browser; None when it cannot (ADR 0011)."""
        ...

    def read_form(self, page: Page) -> list[Field]: ...

    def fill(self, page: Page, answers: dict[str, Answer]) -> None: ...

    def read_back(self, page: Page) -> dict[str, Answer]: ...

    def submit(self, page: Page) -> str | None:
        """Clicks submit; returns the evidence (a URL or a network response), None without.

        Raises `Blocked` when the system refuses the send.
        """
        ...
