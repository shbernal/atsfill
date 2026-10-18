from importlib.metadata import version
from pathlib import Path

from atsfill.adapters import ADAPTERS
from atsfill.core import attempts, browser, matching, paths
from atsfill.core.adapter import Adapter, Blocked, Unsupported
from atsfill.core.browser import Page, open_page
from atsfill.core.results import Answer, Result, Status, StatusReport


def _adapter(url: str, page: Page) -> Adapter | None:
    return next((a for a in ADAPTERS if a.detect(url, page)), None)


def _fill(page: Page, adapter: Adapter, url: str, answers: dict[str, Answer]) -> Result:
    """Fills and reads back; `ready` when every answer reads back as given."""
    result = Result(Status.READY, url, adapter.name, side_effects=adapter.side_effects)
    try:
        result.fields = adapter.read_form(page)
        if problems := matching.problems(result.fields, answers):
            result.status, result.reason = Status.REFUSED, "; ".join(problems)
            return result
        adapter.fill(page, answers)
        result.checks = matching.check(result.fields, answers, adapter.read_back(page))
    except Unsupported as e:
        result.status, result.reason = Status.UNSUPPORTED, str(e)
    except Blocked as e:
        result.status, result.reason = Status.BLOCKED, str(e)
    else:
        if mismatched := [c.field_id for c in result.checks if not c.match]:
            result.status, result.reason = Status.REFUSED, f"mismatch: {', '.join(mismatched)}"
    return result


type Sessions = dict[str, Path]


def _form_command(
    url: str,
    answers: dict[str, Answer],
    data_dir: Path | None,
    sessions: Sessions | None,
    send: bool,
    resend: bool,
) -> Result:
    data = paths.data_dir(data_dir)
    with open_page(data) as page:
        page.goto(url)
        adapter = _adapter(url, page)
        if adapter is None:
            return Result(Status.UNSUPPORTED, url, reason="no adapter for this hiring system")
        posting = adapter.posting(url)
        if send and not paths.submit_enabled(adapter.name):
            reason = f"submit is not enabled for {adapter.name} in {paths.config_file()}"
            return Result(Status.REFUSED, url, adapter.name, reason=reason)
        if send and not resend and attempts.blocks_send(data, posting):
            reason = f"already sent (last attempt {attempts.last(data, posting)}); pass --resend"
            return Result(Status.REFUSED, url, adapter.name, reason=reason)
        result = _fill(page, adapter, url, answers)
        if not send or result.status is not Status.READY:
            return result
        attempts.record(data, posting, attempts.PENDING, url=url)
        try:
            result.evidence = adapter.submit(page)
            result.status = Status.SUBMITTED if result.evidence else Status.UNKNOWN
        except Blocked as e:
            result.status, result.reason = Status.BLOCKED, str(e)
        except Exception as e:  # after the click, nothing tells a failed send from a sent one
            result.status, result.reason = Status.UNKNOWN, f"{type(e).__name__}: {e}"
        attempts.record(
            data, posting, result.status, evidence=result.evidence, reason=result.reason
        )
        return result


def read(url: str, *, data_dir: Path | None = None, sessions: Sessions | None = None) -> Result:
    raise NotImplementedError


def fill(
    url: str,
    answers: dict[str, Answer],
    *,
    data_dir: Path | None = None,
    sessions: Sessions | None = None,
) -> Result:
    return _form_command(url, answers, data_dir, sessions, send=False, resend=False)


def send(
    url: str,
    answers: dict[str, Answer],
    *,
    resend: bool = False,
    data_dir: Path | None = None,
    sessions: Sessions | None = None,
) -> Result:
    return _form_command(url, answers, data_dir, sessions, send=True, resend=resend)


def status(*, data_dir: Path | None = None) -> StatusReport:
    return StatusReport(
        version=version("atsfill"),
        data_dir=paths.data_dir(data_dir),
        config=paths.config_file(),
        browser=browser.version(),
        adapters=[],
    )


def login(adapter: str, *, data_dir: Path | None = None) -> None:
    raise NotImplementedError


def logout(adapter: str, *, data_dir: Path | None = None) -> None:
    raise NotImplementedError
