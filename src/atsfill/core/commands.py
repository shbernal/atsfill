from collections.abc import Callable, Generator
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path

from atsfill.adapters import adapters
from atsfill.core import accounts, attempts, browser, matching, paths, registry
from atsfill.core.adapter import (
    Adapter,
    Blocked,
    NeedsAccount,
    NotTaken,
    ReadsOverHttp,
    Unsupported,
)
from atsfill.core.browser import Covered, OutOfTime, Page, open_page
from atsfill.core.results import (
    AdapterStatus,
    Answer,
    Result,
    SessionReport,
    Status,
    StatusReport,
)

type Sessions = dict[str, Path]


def _detect(candidates: list[Adapter], url: str, page: Page | None = None) -> Adapter | None:
    return next((a for a in candidates if a.detect(url, page)), None)


def _login_required(url: str, adapter: Adapter) -> Result:
    reason = f"login_required: run `atsfill login {adapter.name}`"
    return Result(Status.BLOCKED, url, adapter.name, reason=reason)


@contextmanager
def _opened(
    data: Path, url: str, sessions: Sessions | None, detected: list[str]
) -> Generator[tuple[Page, Adapter] | Result]:
    """The loaded page and the adapter that claims it by its URL, else by the URL a redirect
    lands on, else by its page; or the result that ends the command first. An adapter that
    needs an account gets its session, and only that adapter's (ADR 0007). The adapter's name
    goes in `detected` once known. The adapters are fresh: none keeps state from another
    posting (a survey or the adapter gate runs many commands in one process)."""
    candidates = adapters()
    adapter = _detect(candidates, url)
    if adapter:
        detected.append(adapter.name)
    session = None
    if isinstance(adapter, NeedsAccount):
        session = (sessions or {}).get(adapter.name) or accounts.session_file(data, adapter.name)
        if not session.exists():
            yield _login_required(url, adapter)
            return
    with open_page(data, session) as page:
        try:
            page.goto(url)
        except browser.Timeout:
            if adapter is None:
                raise
            # A page that never finishes loading (a redirect to an employer's career page)
            # is left to the adapter that claimed its URL.
        if adapter is None and (
            adapter := _detect(candidates, page.url) or _detect(candidates, url, page)
        ):
            detected.append(adapter.name)
        if adapter is None:
            yield _no_adapter(url)
        elif isinstance(adapter, NeedsAccount) and not (
            session and adapter.signed_in(page.request)
        ):
            yield _login_required(url, adapter)
        else:
            yield page, adapter


def _result(adapter: Adapter, url: str) -> Result:
    shown = [] if paths.acknowledged(adapter.name) else adapter.side_effects
    return Result(Status.READY, url, adapter.name, side_effects=shown)


def _unfillable(result: Result) -> bool:
    """Stops the form on a required field atsfill cannot fill; an optional one stays listed,
    left empty (ADR 0014)."""
    field = next(
        (f for f in result.fields if f.unfillable and f.required and not f.prefilled), None
    )
    if field:
        result.status, result.reason = Status.UNSUPPORTED, field.unfillable
    return field is not None


def _no_adapter(url: str) -> Result:
    return Result(Status.UNSUPPORTED, url, reason="no_adapter: no adapter for this hiring system")


@contextmanager
def _stops(result: Result) -> Generator[None]:
    """Turns an adapter's `Unsupported`, `NotTaken` or `Blocked` into the result's status."""
    try:
        yield
    except Unsupported as e:
        result.status, result.reason = Status.UNSUPPORTED, str(e)
    except NotTaken as e:
        result.status, result.reason = Status.REFUSED, f"answer_not_taken: {e}"
    except Blocked as e:
        result.status, result.reason = Status.BLOCKED, str(e)
    except Covered as e:
        result.status, result.reason = Status.BLOCKED, f"covered: {e}"


def _in_budget(url: str, command: Callable[[list[str]], Result]) -> Result:
    """Runs a command to a status whatever happens: past the click it reports `unknown` itself,
    before it an error the adapter did not expect means the page is not one it handles. The
    result keeps the adapter detected before the stop."""
    detected: list[str] = []
    try:
        return command(detected)
    except OutOfTime as e:
        return Result(Status.BLOCKED, url, (detected or [None])[-1], f"time_budget: {e}")
    except Exception as e:
        reason = f"adapter_error: {type(e).__name__}: {e}"
        return Result(Status.UNSUPPORTED, url, (detected or [None])[-1], reason)


def _fill(page: Page, adapter: Adapter, url: str, answers: dict[str, Answer]) -> Result:
    """Fills and reads back; `ready` when every answer reads back as given."""
    result = _result(adapter, url)
    with _stops(result):
        result.fields = adapter.read_form(page)
        if _unfillable(result):
            return result
        if problems := matching.problems(result.fields, answers):
            result.status, result.reason = Status.REFUSED, "invalid_answers: " + "; ".join(problems)
            return result
        adapter.fill(page, answers)
        result.checks = matching.check(result.fields, answers, adapter.read_back(page))
        if mismatched := [c.field_id for c in result.checks if not c.match]:
            result.status, result.reason = Status.REFUSED, f"mismatch: {', '.join(mismatched)}"
    return result


def _read_page(data: Path, url: str, sessions: Sessions | None, detected: list[str]) -> Result:
    with _opened(data, url, sessions, detected) as opened:
        if isinstance(opened, Result):
            return opened
        page, adapter = opened
        result = _result(adapter, url)
        with _stops(result):
            result.fields = adapter.read_form(page)
            _unfillable(result)
        return result


def _form_command(
    data: Path,
    url: str,
    answers: dict[str, Answer],
    sessions: Sessions | None,
    send: bool,
    resend: bool,
    detected: list[str],
) -> Result:
    with _opened(data, url, sessions, detected) as opened:
        if isinstance(opened, Result):
            return opened
        page, adapter = opened
        posting = adapter.posting(page.url)  # past any redirect a link goes through
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
        result.warning = _unverified(adapter.name)
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
    for adapter in adapters():
        if not isinstance(adapter, ReadsOverHttp):
            continue
        if (fields := adapter.read_api(url)) is not None:
            result = _result(adapter, url)
            result.fields = fields
            _unfillable(result)
            return result
    data = paths.data_dir(data_dir)
    return _in_budget(url, lambda seen: _read_page(data, url, sessions, seen))


def fill(
    url: str,
    answers: dict[str, Answer],
    *,
    data_dir: Path | None = None,
    sessions: Sessions | None = None,
) -> Result:
    data = paths.data_dir(data_dir)
    return _in_budget(
        url, lambda seen: _form_command(data, url, answers, sessions, False, False, seen)
    )


def send(
    url: str,
    answers: dict[str, Answer],
    *,
    resend: bool = False,
    data_dir: Path | None = None,
    sessions: Sessions | None = None,
) -> Result:
    data = paths.data_dir(data_dir)
    return _in_budget(
        url, lambda seen: _form_command(data, url, answers, sessions, True, resend, seen)
    )


def _unverified(adapter: str) -> str | None:
    """The warning a send carries when no send through the adapter was verified, or the last
    verified one fails now (ADR 0012)."""
    source = registry.for_adapter(adapter)
    if source and source.send_broken:
        return f"send_broken: {_broken(source)}"
    if source and source.send_verified:
        return None
    verification = source.verification if source else None
    return f"not_send_verified: {adapter} is {verification or 'not in the registry'}"


def _session_state(adapter: Adapter, data: Path) -> str | None:
    if not isinstance(adapter, NeedsAccount):
        return None
    return accounts.state(adapter, accounts.session_file(data, adapter.name))


def status(*, data_dir: Path | None = None) -> StatusReport:
    """Also asks each system that needs an account about its stored session."""
    data = paths.data_dir(data_dir)
    return StatusReport(
        version=version("atsfill"),
        data_dir=data,
        config=paths.config_file(),
        browser=browser.version(),
        browser_pinned=browser.BUILD,
        adapters=[_adapter_status(a, data) for a in adapters()],
    )


def _broken(source: registry.Source) -> str:
    broken, last = source.send_broken, source.send_verified
    assert broken
    since = f"send broken since {broken.date} ({broken.version}), {broken.reason}"
    return since + (f"; last verified {last.date} ({last.version})" if last else "")


def _adapter_status(adapter: Adapter, data: Path) -> AdapterStatus:
    source = registry.for_adapter(adapter.name)
    verified = source.verification if source else None
    v = getattr(source, verified) if source and verified else None
    return AdapterStatus(
        adapter.name,
        submit_enabled=paths.submit_enabled(adapter.name),
        side_effects=adapter.side_effects,
        side_effects_acknowledged=paths.acknowledged(adapter.name),
        verified=verified,
        verified_version=v.version if v else None,
        verified_release=registry.release(v.version, version("atsfill")) if v else None,
        send_broken=_broken(source) if source and source.send_broken else None,
        session=_session_state(adapter, data),
    )


def _account(name: str) -> NeedsAccount:
    adapter = next((a for a in adapters() if a.name == name), None)
    if not isinstance(adapter, NeedsAccount):
        raise ValueError(f"{name} is not an adapter that needs an account")
    return adapter


def login(adapter: str, *, data_dir: Path | None = None) -> SessionReport:
    """Opens a window on the system's login page and stores the session once signed in."""
    account, data = _account(adapter), paths.data_dir(data_dir)
    session = accounts.session_file(data, adapter)
    reason = accounts.sign_in(account, data, session)
    return SessionReport(adapter, "signed_in" if reason is None else "missing", session, reason)


def logout(adapter: str, *, data_dir: Path | None = None) -> SessionReport:
    _account(adapter)
    session = accounts.session_file(paths.data_dir(data_dir), adapter)
    session.unlink(missing_ok=True)
    return SessionReport(adapter, "missing", session)
