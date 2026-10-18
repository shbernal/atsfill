from collections.abc import Callable, Generator
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path

from atsfill.adapters import ADAPTERS
from atsfill.core import attempts, browser, matching, paths
from atsfill.core.adapter import Adapter, Blocked, Unsupported
from atsfill.core.browser import OutOfTime, Page, open_page
from atsfill.core.results import AdapterState, Answer, Result, Status, StatusReport

type Sessions = dict[str, Path]


def _detect(url: str, page: Page | None = None) -> Adapter | None:
    return next((a for a in ADAPTERS if a.detect(url, page)), None)


def _load(page: Page, url: str) -> Adapter | None:
    """Loads the URL; the adapter that claims it by its URL, else by its page."""
    adapter = _detect(url)
    page.goto(url)
    return adapter or _detect(url, page)


def _result(adapter: Adapter, url: str) -> Result:
    shown = [] if paths.acknowledged(adapter.name) else adapter.side_effects
    return Result(Status.READY, url, adapter.name, side_effects=shown)


def _no_adapter(url: str) -> Result:
    return Result(Status.UNSUPPORTED, url, reason="no_adapter: no adapter for this hiring system")


@contextmanager
def _stops(result: Result) -> Generator[None]:
    """Turns an adapter's `Unsupported` or `Blocked` into the result's status."""
    try:
        yield
    except Unsupported as e:
        result.status, result.reason = Status.UNSUPPORTED, str(e)
    except Blocked as e:
        result.status, result.reason = Status.BLOCKED, str(e)


def _in_budget(url: str, command: Callable[[], Result]) -> Result:
    try:
        return command()
    except OutOfTime as e:  # past the click, the send reports `unknown` itself
        return Result(Status.BLOCKED, url, reason=f"time_budget: {e}")


def _fill(page: Page, adapter: Adapter, url: str, answers: dict[str, Answer]) -> Result:
    """Fills and reads back; `ready` when every answer reads back as given."""
    result = _result(adapter, url)
    with _stops(result):
        result.fields = adapter.read_form(page)
        if problems := matching.problems(result.fields, answers):
            result.status, result.reason = Status.REFUSED, "invalid_answers: " + "; ".join(problems)
            return result
        adapter.fill(page, answers)
        result.checks = matching.check(result.fields, answers, adapter.read_back(page))
        if mismatched := [c.field_id for c in result.checks if not c.match]:
            result.status, result.reason = Status.REFUSED, f"mismatch: {', '.join(mismatched)}"
    return result


def _read_page(data: Path, url: str) -> Result:
    with open_page(data) as page:
        adapter = _load(page, url)
        if adapter is None:
            return _no_adapter(url)
        result = _result(adapter, url)
        with _stops(result):
            result.fields = adapter.read_form(page)
        return result


def _form_command(
    data: Path, url: str, answers: dict[str, Answer], send: bool, resend: bool
) -> Result:
    with open_page(data) as page:
        adapter = _load(page, url)
        if adapter is None:
            return _no_adapter(url)
        posting = adapter.posting(url)
        if send and not paths.submit_enabled(adapter.name):
            reason = f"submit_not_enabled: add {adapter.name} to `submit` in {paths.config_file()}"
            return Result(Status.REFUSED, url, adapter.name, reason=reason)
        if send and not resend and attempts.blocks_send(data, posting):
            last = attempts.last(data, posting)
            reason = f"already_sent: last attempt ended {last}; pass --resend"
            return Result(Status.REFUSED, url, adapter.name, reason=reason)
        result = _fill(page, adapter, url, answers)
        if not send or result.status is not Status.READY:
            return result
        attempts.record(data, posting, attempts.PENDING, url=url)
        try:
            result.evidence = adapter.submit(page)
            if result.evidence:
                result.status = Status.SUBMITTED
            else:
                result.status, result.reason = Status.UNKNOWN, "no_evidence"
        except Blocked as e:
            result.status, result.reason = Status.BLOCKED, str(e)
        except Exception as e:  # after the click, nothing tells a failed send from a sent one
            result.status = Status.UNKNOWN
            result.reason = f"error_after_click: {type(e).__name__}: {e}"
        attempts.record(
            data, posting, result.status, evidence=result.evidence, reason=result.reason
        )
        return result


def read(url: str, *, data_dir: Path | None = None, sessions: Sessions | None = None) -> Result:
    """Lists the form's fields, over HTTP when an adapter can (ADR 0011), else from the page."""
    for adapter in ADAPTERS:
        if (fields := adapter.read_api(url)) is not None:
            result = _result(adapter, url)
            result.fields = fields
            return result
    data = paths.data_dir(data_dir)
    return _in_budget(url, lambda: _read_page(data, url))


def fill(
    url: str,
    answers: dict[str, Answer],
    *,
    data_dir: Path | None = None,
    sessions: Sessions | None = None,
) -> Result:
    data = paths.data_dir(data_dir)
    return _in_budget(url, lambda: _form_command(data, url, answers, send=False, resend=False))


def send(
    url: str,
    answers: dict[str, Answer],
    *,
    resend: bool = False,
    data_dir: Path | None = None,
    sessions: Sessions | None = None,
) -> Result:
    data = paths.data_dir(data_dir)
    return _in_budget(url, lambda: _form_command(data, url, answers, send=True, resend=resend))


def status(*, data_dir: Path | None = None) -> StatusReport:
    return StatusReport(
        version=version("atsfill"),
        data_dir=paths.data_dir(data_dir),
        config=paths.config_file(),
        browser=browser.version(),
        adapters=[
            AdapterState(
                a.name,
                submit_enabled=paths.submit_enabled(a.name),
                side_effects=a.side_effects,
                side_effects_acknowledged=paths.acknowledged(a.name),
            )
            for a in ADAPTERS
        ],
    )


def login(adapter: str, *, data_dir: Path | None = None) -> None:
    raise NotImplementedError


def logout(adapter: str, *, data_dir: Path | None = None) -> None:
    raise NotImplementedError
