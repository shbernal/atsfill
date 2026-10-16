from typing import Protocol

from atsfill.core.browser import Page
from atsfill.core.results import Answer, Field, SideEffect


class Adapter(Protocol):
    """What an adapter knows: its system's page, nothing about when a send is allowed."""

    name: str
    side_effects: list[SideEffect]
    needs_session: bool

    def detect(self, url: str, page: Page) -> bool: ...

    def read_api(self, url: str) -> list[Field] | None:
        """Lists the fields over HTTP, without a browser; None when it cannot (ADR 0011)."""
        ...

    def read_form(self, page: Page) -> list[Field]: ...

    def fill(self, page: Page, answers: dict[str, Answer]) -> None: ...

    def read_back(self, page: Page) -> dict[str, Answer]: ...

    def submit(self, page: Page) -> str | None:
        """Clicks submit; returns the evidence (a URL or a network response), None without."""
        ...
