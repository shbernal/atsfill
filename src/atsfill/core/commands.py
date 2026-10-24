import shlex
from collections.abc import Callable, Generator
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path

from atsfill.adapters import adapters
from atsfill.core import (
    accounts,
    attempts,
    browser,
    conditions,
    matching,
    passwords,
    paths,
    registry,
)
from atsfill.core.adapter import (
    Adapter,
    Blocked,
    CreatesAccount,
    ListsShownFields,
    NeedsAccount,
    NotTaken,
    ReadsOverHttp,
    Refused,
    SignsIn,
    Unsupported,
)
from atsfill.core.browser import OutOfTime, Overlaid, Page, open_page
from atsfill.core.results import (
    AdapterStatus,
    Answer,
    Field,
    Result,
    SessionReport,
    Status,
    StatusReport,
)

type Sessions = dict[str, Path]


def _detect(candidates: list[Adapter], url: str, page: Page | None = None) -> Adapter | None:
    return next((a for a in candidates if a.detect(url, page)), None)


def login_command(adapter: str, user: str, url: str | None) -> str:
    """The `login` command line for one account: its user, and a posting where it is scoped."""
    command = ["atsfill", "login", adapter]
    if user != paths.DEFAULT_USER:
        command += ["--user", user]
    if url:
        command += ["--url", url]
    return shlex.join(command)


def _login_required(url: str, adapter: NeedsAccount, user: str) -> Result:
    command = login_command(adapter.name, user, url if adapter.per_employer else None)
    return Result(Status.BLOCKED, url, adapter.name, reason=f"login_required: run `{command}`")


type Credentials = tuple[str, str]  # email, password
# Refusals the page states before any account exists: the generated password goes.
NO_ACCOUNT = {"email_in_use", "email_domain_rejected", "password_rule"}


def _email_link(reason: str | None, email: str, command: str) -> str | None:
    """Names the address a verification link went to and the command to run once clicked."""
    if reason and reason.split(":")[0] == "email_link":
        return f"email_link: click the link sent to {email}, then run `{command}`"
    return reason


def _credentials(
    adapter: NeedsAccount, user: str, mine: Path, scope: str | None, given: str | None = None
) -> Credentials | None:
    """The account's email and a password from its password source, where atsfill can sign in
    through the adapter; None otherwise."""
    if not isinstance(adapter, SignsIn):
        return None
    if not (email := accounts.email(mine, adapter.name, scope)):
        return None
    password = passwords.password(user, mine, adapter.name, scope, email, given)
    return (email, password) if password else None


@contextmanager
def _opened(
    data: Path, user: str, url: str, sessions: Sessions | None, detected: list[str]
) -> Generator[tuple[Page, Adapter] | Result]:
    """The loaded page and the adapter that claims it by its URL, else by the URL a redirect
    lands on, else by its page; or the result that ends the command first. An adapter that
    needs an account gets the user's session, and only that adapter's; signed out with a
    password source, atsfill signs in again (ADR 0015). The adapter's name goes in `detected`
    once known. The adapters are fresh: none keeps state from another
    posting (a survey or the adapter gate runs many commands in one process)."""
    candidates = adapters()
    adapter = _detect(candidates, url)
    if adapter:
        detected.append(adapter.name)
    mine = paths.user_dir(data, user)
    session, credentials = None, None
    if isinstance(adapter, NeedsAccount):
        scope = adapter.account_scope(url)
        stored = accounts.session_file(mine, adapter.name, scope)
        session = (sessions or {}).get(adapter.name) or stored
        if not session.exists():
            try:
                credentials = _credentials(adapter, user, mine, scope)
            except passwords.PasswordError as e:
                yield Result(Status.BLOCKED, url, adapter.name, f"password_source: {e}")
                return
            if credentials is None:
                yield _login_required(url, adapter, user)
                return
            session = None
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
            session and adapter.signed_in(page.request, adapter.account_scope(url))
        ):
            yield _sign_in_again(page, adapter, user, mine, url, credentials) or (page, adapter)
        else:
            yield page, adapter


def _sign_in_again(
    page: Page,
    adapter: NeedsAccount,
    user: str,
    mine: Path,
    url: str,
    credentials: Credentials | None,
) -> Result | None:
    """Signs in with the account's password source, stores the session and loads the posting
    again; the result that ends the command instead, `login_required` without a source."""
    result = Result(Status.BLOCKED, url, adapter.name)
    with _stops(result):
        scope = adapter.account_scope(url)
        try:
            credentials = credentials or _credentials(adapter, user, mine, scope)
        except passwords.PasswordError as e:
            result.reason = f"password_source: {e}"
            return result
        if credentials is None or not isinstance(adapter, SignsIn):
            return _login_required(url, adapter, user)
        session = accounts.session_file(mine, adapter.name, scope)
        accounts.sign_in(page, adapter, scope, *credentials, session)
        page.goto(url)
        return None
    command = login_command(adapter.name, user, url if adapter.per_employer else None)
    result.reason = _email_link(result.reason, credentials[0] if credentials else "", command)
    return result


def _result(adapter: Adapter, url: str, user: str) -> Result:
    shown = [] if paths.muted(adapter.name, user) else adapter.side_effects
    return Result(Status.READY, url, adapter.name, side_effects=shown)


def _unfillable(result: Result, answers: dict[str, Answer] | None = None) -> bool:
    """Stops the form on a required field atsfill cannot fill; an optional one stays listed,
    left empty (ADR 0014). A follow-up counts once the answers show it and require it, and
    without answers never."""
    form = conditions.Form(result.fields, answers or {})
    field = next(
        (
            f
            for f in result.fields
            if f.unfillable
            and f.prefilled is None
            and (form.required(f) if answers is not None else f.required and not f.shown_when)
        ),
        None,
    )
    if field:
        result.status, result.reason = Status.UNSUPPORTED, field.unfillable
    return field is not None


def _listed(result: Result, fields: list[Field]) -> None:
    """Sets the fields `read_form` or `read_api` listed; their conditions must name listed
    fields, never a loop."""
    if faults := conditions.faults(fields):
        raise ValueError("; ".join(faults))
    result.fields = fields


def _shown_as_listed(
    page: Page, adapter: Adapter, result: Result, answers: dict[str, Answer]
) -> None:
    """Stops a fill that showed a field `read_form` did not list, or a follow-up whose
    condition the answers leave unmet: that condition was read wrong (ADR 0005)."""
    if not isinstance(adapter, ListsShownFields):
        return
    by_id, form = {f.id: f for f in result.fields}, conditions.Form(result.fields, answers)
    shown = adapter.shown_fields(page)
    if new := [i for i in shown if i not in by_id]:
        result.status = Status.UNSUPPORTED
        result.reason = f"unhandled_rendering: an answer showed {', '.join(new)}, not listed"
    elif wrong := [i for i in shown if not form.shown(by_id[i])]:
        result.status = Status.REFUSED
        result.reason = f"mismatch: {', '.join(wrong)} shows though its condition is unmet"


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
    except Refused as e:
        result.status, result.reason = Status.REFUSED, str(e)
    except Blocked as e:
        result.status, result.reason = Status.BLOCKED, str(e)
    except Overlaid as e:
        result.status, result.reason = Status.BLOCKED, f"overlaid: {e}"


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


def _fill(page: Page, adapter: Adapter, url: str, answers: dict[str, Answer], user: str) -> Result:
    """Fills and reads back; `ready` when every answer reads back as given."""
    result = _result(adapter, url, user)
    with _stops(result):
        _listed(result, adapter.read_form(page))
        if _unfillable(result, answers):
            return result
        if problems := matching.problems(result.fields, answers):
            result.status, result.reason = Status.REFUSED, "invalid_answers: " + "; ".join(problems)
            return result
        adapter.fill(page, answers)
        _shown_as_listed(page, adapter, result, answers)
        if result.status is not Status.READY:
            return result
        result.checks = matching.check(result.fields, answers, adapter.read_back(page))
        if mismatched := [c.field_id for c in result.checks if not c.match]:
            result.status, result.reason = Status.REFUSED, f"mismatch: {', '.join(mismatched)}"
    return result


def _read_page(
    data: Path, user: str, url: str, sessions: Sessions | None, detected: list[str]
) -> Result:
    with _opened(data, user, url, sessions, detected) as opened:
        if isinstance(opened, Result):
            return opened
        page, adapter = opened
        result = _result(adapter, url, user)
        with _stops(result):
            _listed(result, adapter.read_form(page))
            _unfillable(result)
        return result


def _form_command(
    data: Path,
    user: str,
    url: str,
    answers: dict[str, Answer],
    sessions: Sessions | None,
    send: bool,
    resend: bool,
    detected: list[str],
) -> Result:
    mine = paths.user_dir(data, user)  # before the page takes the lock its first use needs
    with _opened(data, user, url, sessions, detected) as opened:
        if isinstance(opened, Result):
            return opened
        page, adapter = opened
        posting = adapter.posting(page.url)  # past any redirect a link goes through
        if send and not paths.submit_enabled(adapter.name, user):
            where = "" if user == paths.DEFAULT_USER else f" under [users.{user}]"
            reason = (
                f"submit_not_enabled: add {adapter.name} to `submit-allowed`{where}"
                f" in {paths.config_file()}"
            )
            return Result(Status.REFUSED, url, adapter.name, reason=reason)
        if send and not resend and attempts.blocks_send(mine, posting):
            last = attempts.last(mine, posting)
            reason = f"already_sent: last attempt ended {last}; pass --resend"
            return Result(Status.REFUSED, url, adapter.name, reason=reason)
        result = _fill(page, adapter, url, answers, user)
        if result.status is not Status.READY:
            return result
        result.warning = _unverified(adapter.name)
        if not send:
            return result
        attempts.record(mine, posting, attempts.PENDING, url=url)
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
            mine, posting, result.status, evidence=result.evidence, reason=result.reason
        )
        return result


def read(
    url: str,
    *,
    user: str = paths.DEFAULT_USER,
    data_dir: Path | None = None,
    sessions: Sessions | None = None,
) -> Result:
    """Lists the form's fields, over HTTP when an adapter can (ADR 0011), else from the page."""
    for adapter in adapters():
        if not isinstance(adapter, ReadsOverHttp):
            continue
        if (fields := adapter.read_api(url)) is not None:
            result = _result(adapter, url, user)
            try:
                _listed(result, fields)
            except ValueError as e:
                return Result(Status.UNSUPPORTED, url, adapter.name, f"adapter_error: {e}")
            _unfillable(result)
            return result
    data = paths.data_dir(data_dir)
    return _in_budget(url, lambda seen: _read_page(data, user, url, sessions, seen))


def fill(
    url: str,
    answers: dict[str, Answer],
    *,
    user: str = paths.DEFAULT_USER,
    data_dir: Path | None = None,
    sessions: Sessions | None = None,
) -> Result:
    data = paths.data_dir(data_dir)
    return _in_budget(
        url, lambda seen: _form_command(data, user, url, answers, sessions, False, False, seen)
    )


def send(
    url: str,
    answers: dict[str, Answer],
    *,
    resend: bool = False,
    user: str = paths.DEFAULT_USER,
    data_dir: Path | None = None,
    sessions: Sessions | None = None,
) -> Result:
    data = paths.data_dir(data_dir)
    return _in_budget(
        url, lambda seen: _form_command(data, user, url, answers, sessions, True, resend, seen)
    )


def _unverified(adapter: str) -> str | None:
    """The warning a ready dry run or a send carries when no send through the adapter was
    verified, or the last verified one fails now (ADR 0012)."""
    source = registry.for_adapter(adapter)
    if source and source.send_broken:
        return f"send_broken: {_broken(source)}"
    if source and source.send_verified:
        return None
    verification = source.verification if source else None
    return f"not_send_verified: {adapter} is {verification or 'not in the registry'}"


def _sessions(data: Path, users: list[str], candidates: list[Adapter]) -> list[SessionReport]:
    """Every session a user stored, each asked of its system."""
    return [
        SessionReport(user, adapter.name, scope, accounts.state(adapter, path, scope), path)
        for user in users
        for adapter in candidates
        if isinstance(adapter, NeedsAccount)
        for scope, path in accounts.stored(paths.user_dir(data, user), adapter.name)
    ]


def status(*, data_dir: Path | None = None) -> StatusReport:
    """Also asks each system that needs an account about every stored session."""
    data = paths.data_dir(data_dir)
    paths.user_dir(data, paths.DEFAULT_USER)  # moves a data directory from before users
    users, candidates = paths.users(data), adapters()
    return StatusReport(
        version=version("atsfill"),
        data_dir=data,
        config=paths.config_file(),
        browser=browser.version(),
        browser_pinned=browser.BUILD,
        secret_store=passwords.secret_store(),
        users=users,
        adapters=[_adapter_status(a, users) for a in candidates],
        sessions=_sessions(data, users, candidates),
    )


def _broken(source: registry.Source) -> str:
    broken, last = source.send_broken, source.send_verified
    assert broken
    since = f"send broken since {broken.date} ({broken.version}), {broken.reason}"
    return since + (f"; last verified {last.date} ({last.version})" if last else "")


def _adapter_status(adapter: Adapter, users: list[str]) -> AdapterStatus:
    source = registry.for_adapter(adapter.name)
    verified = source.verification if source else None
    v = getattr(source, verified) if source and verified else None
    return AdapterStatus(
        adapter.name,
        submit_allowed=[u for u in users if paths.submit_enabled(adapter.name, u)],
        side_effects=adapter.side_effects,
        side_effects_muted=[u for u in users if paths.muted(adapter.name, u)],
        verified=verified,
        verified_version=v.version if v else None,
        verified_release=registry.release(v.version, version("atsfill")) if v else None,
        send_broken=_broken(source) if source and source.send_broken else None,
    )


def _account(name: str) -> NeedsAccount:
    adapter = next((a for a in adapters() if a.name == name), None)
    if not isinstance(adapter, NeedsAccount):
        raise ValueError(f"{name} is not an adapter that needs an account")
    return adapter


def _scope(account: NeedsAccount, url: str | None) -> str | None:
    """The account scope `--url` gives, required where each employer keeps its own accounts."""
    scope = account.account_scope(url) if url else None
    if account.per_employer and scope is None:
        raise ValueError(f"{account.name} keeps an account per employer: pass --url <posting>")
    return scope


def login(
    adapter: str,
    *,
    user: str = paths.DEFAULT_USER,
    url: str | None = None,
    email: str | None = None,
    password: str | None = None,
    window: bool = False,
    create: bool = False,
    name: str | None = None,
    data_dir: Path | None = None,
) -> SessionReport:
    """Signs in with the account's password source, headless, and stores the session; without
    a source, or with `window`, opens a window on the system's login page for a person.

    `email` is remembered for the account; `password` is used for this sign-in only. `create`
    creates the account with a password atsfill generates and stores first (a side effect),
    giving `name` where the sign-up asks it; with a password already stored, the account may
    exist, so it signs in with that password and keeps the account's email instead."""
    account, data = _account(adapter), paths.data_dir(data_dir)
    scope = _scope(account, url)
    mine = paths.user_dir(data, user)
    session = accounts.session_file(mine, adapter, scope)
    report = SessionReport(user, adapter, scope, "missing", session)
    if create and not email:
        raise ValueError("creating an account needs --email")
    try:
        kept = create and passwords.stored(mine, adapter, scope) is not None
    except passwords.PasswordError as e:
        report.status, report.reason = Status.BLOCKED, f"password_source: {e}"
        return report
    if email and not kept:
        accounts.remember(mine, adapter, scope, email)
    if create and not kept:
        assert email
        return _create(report, account, url, mine, (email, name), data)
    if kept:
        report.password = "kept"
    if password and not (isinstance(account, SignsIn) and accounts.email(mine, adapter, scope)):
        raise ValueError(f"signing in to {adapter} with a password needs --email")
    try:
        credentials = None if window else _credentials(account, user, mine, scope, password)
    except passwords.PasswordError as e:
        report.status, report.reason = Status.BLOCKED, f"password_source: {e}"
        return report
    if credentials is None:
        report.status, report.reason = (
            Status.BLOCKED,
            accounts.wait_for_person(account, scope, data, session),
        )
    else:
        result = _headless(data, account, scope, credentials, session)
        command = login_command(adapter, user, url)
        report.status, report.reason = (
            result.status,
            _email_link(result.reason, credentials[0], command),
        )
    if report.reason is None:
        report.session = report.status = "signed_in"
    return report


def _create(
    report: SessionReport,
    account: NeedsAccount,
    url: str | None,
    mine: Path,
    who: tuple[str, str | None],
    data: Path,
) -> SessionReport:
    """Creates the account: the generated password is stored before the click, and deleted
    only on a refusal the page states before any account exists."""
    if not isinstance(account, CreatesAccount):
        report.status = Status.UNSUPPORTED
        report.reason = f"no_sign_up: atsfill cannot create a {account.name} account"
        return report
    email, name = who
    if account.asks_name and not name:
        raise ValueError(f"creating a {account.name} account needs --name")
    password = passwords.generate()
    try:
        passwords.store(mine, account.name, report.scope, password)
    except passwords.PasswordError as e:
        report.status, report.reason = Status.BLOCKED, f"password_source: {e}"
        return report
    result = _headless(data, account, report.scope, (email, password), report.path, True, name)
    command = login_command(account.name, report.user, url)
    report.status, report.reason = result.status, _email_link(result.reason, email, command)
    report.password = "stored"
    if result.status is Status.REFUSED and (result.reason or "").split(":")[0] in NO_ACCOUNT:
        passwords.delete(mine, account.name, report.scope)
        report.password = "deleted"
    if report.reason is None:
        report.session = report.status = "signed_in"
    return report


def _headless(
    data: Path,
    account: NeedsAccount,
    scope: str | None,
    credentials: Credentials,
    session: Path,
    create: bool = False,
    name: str | None = None,
) -> Result:
    """Signs in, or creates the account, through the adapter: `ready` once the session is
    stored."""
    assert isinstance(account, SignsIn)
    url = account.login_url(scope)

    def run(_: list[str]) -> Result:
        result = Result(Status.READY, url, account.name)
        with open_page(data, None) as page, _stops(result):
            accounts.sign_in(page, account, scope, *credentials, session, create, name)
        return result

    return _in_budget(url, run)


def logout(
    adapter: str,
    *,
    user: str = paths.DEFAULT_USER,
    url: str | None = None,
    data_dir: Path | None = None,
) -> SessionReport:
    scope = _scope(_account(adapter), url)
    mine = paths.user_dir(paths.data_dir(data_dir), user)
    session = accounts.session_file(mine, adapter, scope)
    session.unlink(missing_ok=True)
    return SessionReport(user, adapter, scope, "missing", session)
