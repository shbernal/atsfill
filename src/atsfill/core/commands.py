from pathlib import Path

from atsfill.core.results import Answer, Result, StatusReport


def read(url: str, *, data_dir: Path | None = None) -> Result:
    raise NotImplementedError


def fill(url: str, answers: dict[str, Answer], *, data_dir: Path | None = None) -> Result:
    raise NotImplementedError


def send(
    url: str,
    answers: dict[str, Answer],
    *,
    resend: bool = False,
    data_dir: Path | None = None,
) -> Result:
    raise NotImplementedError


def status(*, data_dir: Path | None = None) -> StatusReport:
    raise NotImplementedError


def login(adapter: str, *, data_dir: Path | None = None) -> None:
    raise NotImplementedError


def logout(adapter: str, *, data_dir: Path | None = None) -> None:
    raise NotImplementedError
