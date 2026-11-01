import shlex
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from atsfill.adapters import adapters
from atsfill.core import (
    accounts,
    attempts,
    browser,
    conditions,
    matching,
    otp,
    passwords,
    paths,
    registry,
    snapshot,
    telemetry,
)
from atsfill.core.adapter import (
    Adapter,
    Blocked,
    CreatesAccount,
    CreatesAccountByOtp,
    Held,
    KeepsProfile,
    ListsShownFields,
    NeedsAccount,
    NotTaken,
    OpensForm,
    ProfileIncomplete,
    ReadsByRequest,
    Refused,
    RevealsFields,
    SendsToOpen,
    SignedOut,
    SignsIn,
    SignsInByOtp,
    SignUpDetails,
    Unsupported,
)
from atsfill.core.browser import OutOfTime, Overlaid, Page, open_page
from atsfill.core.results import (
    AdapterStatus,
    Answer,
    Field,
    ReportResult,
    Result,
    SessionReport,
    Status,
    StatusReport,
)

type Sessions = dict[str, Path]


@dataclass
class Seen:
    """What a command saw before it ended, for a result it did not get to make."""

    adapter: str | None = None  # the adapter detected
    page: tuple[str, str] | None = None  # a failed run's page, for its report (ADR 0016)


def _detect(candidates: list[Adapter], url: str, page: Page | None = None) -> Adapter | None:
    return next((a for a in candidates if a.detect(url, page)), None)


def login_command(adapter: str, user: str, url: str | None, *more: str) -> str:
    """The `login` command line for one account: its user, and a posting where it is scoped."""
    command = ["atsfill", "login", adapter]
    if user != paths.DEFAULT_USER:
        command += ["--user", user]
    if url:
        command += ["--url", url]
    return shlex.join([*command, *more])


def profile_command(adapter: str, user: str, url: str | None) -> str:
    """The `profile` command line for one account, as `login_command` gives `login`'s."""
    return login_command(adapter, user, url).replace(" login ", " profile ", 1)


def _profile_incomplete(result: Result, adapter: Adapter, user: str, url: str) -> None:
    """Names the `profile` command in a `profile_incomplete` reason (ADR 0017)."""
    if (result.reason or "").partition(":")[0] != "profile_incomplete":
        return
    scoped = isinstance(adapter, NeedsAccount) and adapter.per_employer
    command = profile_command(adapter.name, user, url if scoped else None)
    result.reason = f"{result.reason}, run `{command}`"


def _login_required(url: str, adapter: NeedsAccount, user: str) -> Result:
    command = login_command(adapter.name, user, url if adapter.per_employer else None)
    return Result(Status.BLOCKED, url, adapter.name, reason=f"login_required: run `{command}`")


type Credentials = tuple[str, str | None]  # email, password: None for an account without one
# Refusals the page states before any account exists: the generated password goes.
NO_ACCOUNT = {"email_in_use", "email_domain_rejected", "password_rule", "sign_up_details"}
# A sign-in's refusals that find no account with the password: a stored one may be from a
# sign-up blocked before the account existed.
NOT_SIGNED_UP = {"wrong_password", "no_account"}


def _next_step(
    reason: str | None, email: str, adapter: str, user: str, url: str | None
) -> str | None:
    """Names the command to run next: once the verification link sent to the address is
    clicked, or `--create` for an email the system has no account for."""
    code = (reason or "").split(":")[0]
    if code == "email_link":
        command = login_command(adapter, user, url)
        return f"email_link: click the link sent to {email}, then run `{command}`"
    if code == "no_account":
        command = login_command(adapter, user, url, "--email", email, "--create")
        return f"no_account: {email} has no {adapter} account; run `{command}`"
    return reason


def _credentials(
    adapter: NeedsAccount, user: str, mine: Path, scope: str | None, given: str | None = None
) -> Credentials | None:
    """The account's email and a password from its password source, or no password for an
    account without one when the user has an OTP command for it, where atsfill can sign in
    through the adapter; None otherwise."""
    if not isinstance(adapter, (SignsIn, SignsInByOtp)):
        return None
    if not (email := accounts.email(mine, adapter.name, scope)):
        return None
    if isinstance(adapter, SignsInByOtp):
        return (email, None) if otp.command(user, adapter.name) else None
    password = passwords.password(user, mine, adapter.name, scope, email, given)
    return (email, password) if password else None


@contextmanager
def _opened(
    data: Path,
    user: str,
    url: str,
    sessions: Sessions | None,
    seen: Seen,
    known: Adapter | None = None,
    seconds: float = browser.BUDGET,
) -> Generator[tuple[Page, Adapter] | Result]:
    """The loaded page and the adapter that claims it by its URL, else by the URL a redirect
    lands on, else by its page; or the result that ends the command first. An adapter that
    needs an account gets the user's session, and only that adapter's; signed out with a
    password source, or an OTP command for an account without a password, atsfill signs in
    again (ADR 0015). The adapter's name goes in `seen` once known; `known` skips the
    detection. The adapters are fresh: none keeps state from another posting (a survey or the
    adapter gate runs many commands in one process)."""
    candidates = adapters()
    adapter = known or _detect(candidates, url)
    if adapter:
        seen.adapter = adapter.name
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
    with open_page(data, session, seconds) as page:
        try:
            _load(page, adapter, url)
        except browser.Timeout:
            if adapter is None:
                raise
            # A page that never finishes loading (a redirect to an employer's career page)
            # is left to the adapter that claimed its URL.
        if adapter is None and (
            adapter := _detect(candidates, page.url) or _detect(candidates, url, page)
        ):
            seen.adapter = adapter.name
        if adapter is None:
            yield _no_adapter(url)
            return
        scope = adapter.account_scope(url) if isinstance(adapter, NeedsAccount) else None
        with otp.source(user, adapter.name, scope):
            if not isinstance(adapter, NeedsAccount):
                yield page, adapter
                return
            token = _again.set(lambda: _sign_in_again(page, adapter, user, mine, url, None))
            try:
                if not (session and adapter.signed_in(page.request, scope)):
                    yield _sign_in_again(page, adapter, user, mine, url, credentials) or (
                        page,
                        adapter,
                    )
                else:
                    yield page, adapter
            finally:
                _again.reset(token)
            if session:  # a system that rotates the session's tokens as the page uses them
                with suppress(Exception):
                    browser.save_session(page, session)


def _load(page: Page, adapter: object, url: str) -> None:
    """Loads the posting, or its form where the adapter that claims it opens it (ADR 0003)."""
    if isinstance(adapter, OpensForm):
        adapter.open_form(page, url)
    else:
        page.goto(url)


_again: ContextVar[Callable[[], Result | None] | None] = ContextVar("again", default=None)


def _read_signed_in(page: Page, adapter: Adapter, mine: Path, user: str, url: str) -> list[Field]:
    """`browser_read`, the form opened first where opening it sends, once more after signing in
    again where the page asked to sign in although the session held: the result that ends the
    command instead is raised as `Blocked`."""

    def read() -> list[Field]:
        if isinstance(adapter, SendsToOpen):
            _open(page, adapter, mine, user, url)
        return adapter.browser_read(page)

    try:
        return read()
    except SignedOut:
        again = _again.get()
        if again is None:
            raise
        if ended := again():
            raise Blocked(ended.reason) from None
        return read()


def _open(page: Page, adapter: SendsToOpen, mine: Path, user: str, url: str) -> None:
    """Opens a form whose system sends the application to show it, under a send's rules: the
    adapter in `submit-allowed`, and the send recorded `opened`, which the send of that same
    application does not count against (ADR 0006). An error past the adapter's own stops may
    follow the send: it is recorded `unknown`."""
    if not paths.submit_enabled(adapter.name, user):
        raise Refused(f"{_not_enabled(adapter, user)}; opening its form sends the application")
    posting = adapter.posting(page.url)
    try:
        evidence = adapter.open(page)
    except SignedOut, Unsupported, Blocked:
        raise
    except Exception as e:
        reason = f"error_after_click: {type(e).__name__}: {e}"
        attempts.record(mine, posting, Status.UNKNOWN, reason=reason)
        raise
    if evidence:
        attempts.record(mine, posting, attempts.OPENED, evidence=evidence, url=url)


def _not_enabled(adapter: Adapter, user: str) -> str:
    where = "" if user == paths.DEFAULT_USER else f" under [users.{user}]"
    return (
        f"submit_not_enabled: add {adapter.name} to `submit-allowed`{where}"
        f" in {paths.config_file()}"
    )


def _sign_in_again(
    page: Page,
    adapter: NeedsAccount,
    user: str,
    mine: Path,
    url: str,
    credentials: Credentials | None,
) -> Result | None:
    """Signs in as `_credentials` allows, stores the session and loads the posting again; the
    result that ends the command instead, `login_required` where it does not."""
    result = Result(Status.BLOCKED, url, adapter.name)
    with _stops(result):
        scope = adapter.account_scope(url)
        try:
            credentials = credentials or _credentials(adapter, user, mine, scope)
        except passwords.PasswordError as e:
            result.reason = f"password_source: {e}"
            return result
        if credentials is None or not isinstance(adapter, (SignsIn, SignsInByOtp)):
            return _login_required(url, adapter, user)
        session = accounts.session_file(mine, adapter.name, scope)
        accounts.sign_in(page, adapter, scope, *credentials, session)
        _load(page, adapter, url)
        return None
    email = credentials[0] if credentials else ""
    scoped = url if adapter.per_employer else None
    result.reason = _next_step(result.reason, email, adapter.name, user, scoped)
    return result


def _result(adapter: Adapter, url: str, user: str) -> Result:
    shown = [] if paths.muted(adapter.name, user) else adapter.side_effects
    return Result(Status.READY, url, adapter.name, side_effects=shown)


def _unfillable(result: Result, answers: dict[str, Answer] | None = None) -> bool:
    """Stops the form on a required field atsfill cannot fill, or a required group none of whose
    alternatives it can; an optional one stays listed, left empty (ADR 0014). A follow-up counts
    once the answers show it and require it, and without answers never."""
    form = conditions.Form(result.fields, answers or {})

    def stuck(f: Field) -> bool:
        return f.unfillable is not None and f.prefilled is None

    field = next(
        (
            f
            for f in result.fields
            if stuck(f)
            and (form.asked(f) if answers is not None else f.required and not f.shown_when)
            and all(stuck(m) for m in form.groups.get(f.one_of or "", [f]))
        ),
        None,
    )
    if field:
        result.status, result.reason = Status.UNSUPPORTED, field.unfillable
    return field is not None


def _listed(result: Result, fields: list[Field]) -> None:
    """Sets the fields `browser_read` or `request_read` listed; their conditions must name listed
    fields, never a loop."""
    if faults := conditions.faults(fields):
        raise ValueError("; ".join(faults))
    result.fields = fields


def _shown_as_listed(
    page: Page, adapter: Adapter, result: Result, answers: dict[str, Answer]
) -> None:
    """Stops a fill that showed a field `browser_read` did not list, or a follow-up whose
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


def _refuse(result: Result, problems: list[matching.Problem], *more: str) -> None:
    result.status = Status.REFUSED
    result.reason = "invalid_answers: " + "; ".join([*problems, *more])
    result.refused = [f"{p.field_id}: {p.rule}" for p in problems]


def _no_field(result: Result, answers: dict[str, Answer]) -> bool:
    """Refuses an answer to no listed field. Ids come from `read`, so it says as much about the
    adapter as about the answers: a form that changed, or a field read missed."""
    listed = matching.known(result.fields, answers)
    if unknown := [i for i in answers if i not in listed]:
        result.status, result.reason = Status.REFUSED, f"unknown_field: {', '.join(unknown)}"
    return bool(unknown)


def _revealed(
    page: Page, adapter: RevealsFields, result: Result, answers: dict[str, Answer]
) -> dict[str, Answer]:
    """Fills what the saved answers reveal, until they reveal nothing more, and returns the
    answers the form took. A revealed field no answer takes refuses, naming what it asks, since
    `fill` prints no fields (ADR 0005)."""
    mine = matching.as_listed(result.fields, matching.known(result.fields, answers))
    while new := [f for f in adapter.revealed(page) if f.id not in {g.id for g in result.fields}]:
        _listed(result, result.fields + new)
        mine = matching.as_listed(result.fields, matching.known(result.fields, answers))
        if _unfillable(result, mine):
            return mine
        if problems := matching.problems(result.fields, mine):
            _refuse(
                result,
                problems,
                *[f"{f.id} takes {f.options}" for f in new if f.options and f.id not in mine],
            )
            return mine
        left = [f for f in new if f.id not in mine]
        result.warnings += [f"an answer showed {f.id} ({f.label}), left empty" for f in left]
        adapter.fill(page, matching.to_fill(new, {f.id: mine[f.id] for f in new if f.id in mine}))
    _no_field(result, answers)
    return mine


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
    except ProfileIncomplete as e:
        result.status, result.reason = Status.BLOCKED, f"profile_incomplete: {e}"
    except Overlaid as e:
        result.status, result.reason = Status.BLOCKED, f"overlaid: {e}"


def _in_budget(url: str, seen: Seen, command: Callable[[], Result]) -> Result:
    """Runs a command to a status whatever happens: past the click it reports `unknown` itself,
    before it an error the adapter did not expect means the page is not one it handles. The
    result keeps the adapter detected before the stop."""
    try:
        return command()
    except OutOfTime as e:
        return Result(Status.BLOCKED, url, seen.adapter, f"time_budget: {e}")
    except Exception as e:
        reason = f"adapter_error: {type(e).__name__}: {e}"
        return Result(Status.UNSUPPORTED, url, seen.adapter, reason)


def _kept(page: Page, seen: Seen, run: Callable[[], Result]) -> Result:
    """The run's result; a failed run's page goes in `seen`, values stripped, when telemetry
    is on."""
    try:
        result = run()
    except Exception:
        _capture(page, seen)
        raise
    if telemetry.verdict(result) == "fail":
        _capture(page, seen)
    return result


def _capture(page: Page, seen: Seen) -> None:
    with suppress(Exception):  # a page the budget closed has nothing to keep
        if telemetry.level() != "off":
            seen.page = snapshot.capture(page)


def _secrets(data: Path, user: str, answers: dict[str, Answer], result: Result) -> set[str]:
    emails = accounts.emails(paths.user_dir(data, user))
    return snapshot.secrets(answers, result.fields, emails)


def _fill(
    page: Page, adapter: Adapter, mine: Path, url: str, answers: dict[str, Answer], user: str
) -> Result:
    """Fills and reads back; `ready` when every answer reads back as given."""
    result = _result(adapter, url, user)
    with _stops(result):
        _listed(result, _read_signed_in(page, adapter, mine, user, url))
        held_back = isinstance(adapter, RevealsFields)  # an answer may be to a field yet to show
        given = matching.known(result.fields, answers) if held_back else answers
        if _unfillable(result, given) or _no_field(result, given):
            return result
        given = matching.as_listed(result.fields, given)
        if problems := matching.problems(result.fields, given):
            _refuse(result, problems)
            return result
        result.warnings = matching.knockouts(result.fields, given)
        adapter.fill(page, matching.to_fill(result.fields, given))
        if isinstance(adapter, RevealsFields):
            given = _revealed(page, adapter, result, answers)
        else:
            _shown_as_listed(page, adapter, result, given)
        if result.status is not Status.READY:
            return result
        result.checks = matching.check(result.fields, given, adapter.read_back(page))
        if mismatched := [c.field_id for c in result.checks if not c.match]:
            result.status, result.reason = Status.REFUSED, f"mismatch: {', '.join(mismatched)}"
    return result


def _browser_read(data: Path, user: str, url: str, sessions: Sessions | None, seen: Seen) -> Result:
    mine = paths.user_dir(data, user)
    with _opened(data, user, url, sessions, seen) as opened:
        if isinstance(opened, Result):
            return opened
        page, adapter = opened

        def run() -> Result:
            result = _result(adapter, url, user)
            with _stops(result):
                _listed(result, _read_signed_in(page, adapter, mine, user, url))
                _unfillable(result)
            _profile_incomplete(result, adapter, user, url)
            return result

        return _kept(page, seen, run)


def _form_command(
    data: Path,
    user: str,
    url: str,
    answers: dict[str, Answer],
    sessions: Sessions | None,
    send: bool,
    resend: bool,
    seen: Seen,
) -> Result:
    mine = paths.user_dir(data, user)  # before the page takes the lock its first use needs
    with _opened(data, user, url, sessions, seen) as opened:
        if isinstance(opened, Result):
            return opened
        page, adapter = opened
        return _kept(
            page, seen, lambda: _on_page(page, adapter, mine, user, url, answers, send, resend)
        )


def _on_page(
    page: Page,
    adapter: Adapter,
    mine: Path,
    user: str,
    url: str,
    answers: dict[str, Answer],
    send: bool,
    resend: bool,
) -> Result:
    posting = adapter.posting(page.url)  # past any redirect a link goes through
    if send and not paths.submit_enabled(adapter.name, user):
        return Result(Status.REFUSED, url, adapter.name, reason=_not_enabled(adapter, user))
    if send and not resend and attempts.blocks_send(mine, posting):
        last = attempts.last(mine, posting)
        reason = f"already_sent: last attempt ended {last}; pass --resend"
        return Result(Status.REFUSED, url, adapter.name, reason=reason)
    result = _fill(page, adapter, mine, url, answers, user)
    _profile_incomplete(result, adapter, user, url)
    if result.status is not Status.READY:
        return result
    if not send:
        return result
    attempts.record(mine, posting, attempts.PENDING, url=url)
    try:
        result.evidence = adapter.submit(page)
        if result.evidence:
            result.status = Status.SUBMITTED
        else:
            result.status, result.reason = Status.UNKNOWN, "no_evidence"
    except Held as e:
        result.status, result.evidence, result.reason = Status.SUBMITTED, page.url, f"held: {e}"
    except Blocked as e:
        result.status, result.reason = Status.BLOCKED, str(e)
    except Exception as e:  # after the click, nothing tells a failed send from a sent one
        result.status = Status.UNKNOWN
        result.reason = f"error_after_click: {type(e).__name__}: {e}"
    attempts.record(mine, posting, result.status, evidence=result.evidence, reason=result.reason)
    return result


def read(
    url: str,
    *,
    user: str = paths.DEFAULT_USER,
    data_dir: Path | None = None,
    sessions: Sessions | None = None,
) -> Result:
    """Lists the form's fields by a request read when an adapter has one (ADR 0011), else by a
    browser read."""
    data, seen = paths.data_dir(data_dir), Seen()
    result = _read(data, user, url, sessions, seen)
    return telemetry.sent("read", result, data, seen.page, _secrets(data, user, {}, result))


def _read(data: Path, user: str, url: str, sessions: Sessions | None, seen: Seen) -> Result:
    for adapter in adapters():
        if not isinstance(adapter, ReadsByRequest):
            continue
        if (fields := adapter.request_read(url)) is not None:
            result = _result(adapter, url, user)
            try:
                _listed(result, fields)
            except ValueError as e:
                return Result(Status.UNSUPPORTED, url, adapter.name, f"adapter_error: {e}")
            _unfillable(result)
            return result
    return _in_budget(url, seen, lambda: _browser_read(data, user, url, sessions, seen))


def fill(
    url: str,
    answers: dict[str, Answer],
    *,
    user: str = paths.DEFAULT_USER,
    data_dir: Path | None = None,
    sessions: Sessions | None = None,
) -> Result:
    data, seen = paths.data_dir(data_dir), Seen()
    result = _in_budget(
        url, seen, lambda: _form_command(data, user, url, answers, sessions, False, False, seen)
    )
    return telemetry.sent("fill", result, data, seen.page, _secrets(data, user, answers, result))


def send(
    url: str,
    answers: dict[str, Answer],
    *,
    resend: bool = False,
    user: str = paths.DEFAULT_USER,
    data_dir: Path | None = None,
    sessions: Sessions | None = None,
) -> Result:
    data, seen = paths.data_dir(data_dir), Seen()
    result = _in_budget(
        url, seen, lambda: _form_command(data, user, url, answers, sessions, True, resend, seen)
    )
    return telemetry.sent("send", result, data, seen.page, _secrets(data, user, answers, result))


def report(*, data_dir: Path | None = None) -> ReportResult:
    """Sends the last failed run's URL and scrubbed page, once (ADR 0016)."""
    return telemetry.report(paths.data_dir(data_dir))


def profile(
    adapter: str,
    answers: dict[str, Answer] | None = None,
    *,
    user: str = paths.DEFAULT_USER,
    url: str | None = None,
    data_dir: Path | None = None,
) -> Result:
    """Reads the account profile's profile form, or with `answers` saves it step by step: a
    step whose read-back already matches is not saved again (ADR 0017). `url` is a posting,
    for a system with an account per employer."""
    account, data = _account(adapter), paths.data_dir(data_dir)
    if not isinstance(account, KeepsProfile):
        raise ValueError(f"{adapter} keeps no account profile atsfill fills")
    start = account.profile_url(_scope(account, url))

    seen = Seen()

    def run() -> Result:
        with _opened(data, user, start, None, seen, account, browser.PROFILE_BUDGET) as opened:
            if isinstance(opened, Result):
                return opened
            page, _ = opened
            result = _result(account, start, user)
            with _stops(result):
                try:
                    if answers is None:
                        _read_profile(page, account, result)
                    else:
                        _save_profile(page, account, result, answers)
                except OutOfTime as e:  # the result keeps the steps saved before the stop
                    result.status, result.reason = Status.BLOCKED, f"time_budget: {e}"
                except Unsupported, NotTaken, Refused, Blocked, ProfileIncomplete, Overlaid:
                    raise
                except Exception as e:
                    result.status = Status.UNSUPPORTED
                    result.reason = f"adapter_error: {type(e).__name__}: {e}"
            return result

    result = _in_budget(start, seen, run)
    return telemetry.sent(
        "profile", result, data, None, _secrets(data, user, answers or {}, result)
    )


def _read_profile(page: Page, adapter: KeepsProfile, result: Result) -> None:
    """Lists each step's fields up to the first one the system will not load yet; a read never
    saves."""
    steps = adapter.profile_steps(page)
    for i, step in enumerate(steps):
        if not adapter.open_step(page, step):
            result.unloaded = steps[i:]
            return
        _listed(result, result.fields + adapter.profile_read(page))


def _save_profile(
    page: Page, adapter: KeepsProfile, result: Result, answers: dict[str, Answer]
) -> None:
    """Fills, reads back and saves each step that differs from its answers; stops at the first
    step that refuses, the steps saved before it staying saved. The steps are listed again after
    each, since a saved answer can add one."""
    done: list[str] = []
    while step := next((s for s in adapter.profile_steps(page) if s not in done), None):
        done.append(step)
        if not adapter.open_step(page, step):
            raise RuntimeError(f"profile step {step} did not load after the ones before it")
        fields = adapter.profile_read(page)
        _listed(result, result.fields + fields)
        ids = {f.id for f in fields}
        mine = matching.as_listed(fields, {k: v for k, v in answers.items() if k in ids})
        step_result = Result(Status.READY, result.url, fields=fields)
        if _unfillable(step_result, mine):
            result.status, result.reason = step_result.status, step_result.reason
            return
        if problems := matching.problems(fields, mine):
            _refuse(result, problems)
            return
        checks = matching.check(fields, mine, adapter.profile_read_back(page))
        if not all(c.match for c in checks):
            adapter.profile_fill(page, mine)
            checks = matching.check(fields, mine, adapter.profile_read_back(page))
            if mismatched := [c.field_id for c in checks if not c.match]:
                result.checks += checks
                result.status, result.reason = Status.REFUSED, f"mismatch: {', '.join(mismatched)}"
                return
            adapter.save(page)
            result.saved.append(step)
        result.checks += checks
    _no_field(result, answers)


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
        run_history=registry.RUN_HISTORY,
    )


def _adapter_status(adapter: Adapter, users: list[str]) -> AdapterStatus:
    source = registry.for_adapter(adapter.name)
    return AdapterStatus(
        adapter.name,
        submit_allowed=[u for u in users if paths.submit_enabled(adapter.name, u)],
        side_effects=adapter.side_effects,
        side_effects_muted=[u for u in users if paths.muted(adapter.name, u)],
        scope=source.scope.value if source else None,
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
    details: SignUpDetails | None = None,
    data_dir: Path | None = None,
) -> SessionReport:
    """Signs in with the account's password source, headless, and stores the session; without
    a source, or with `window`, opens a window on the system's login page for a person.

    `email` is remembered for the account; `password` is used for this sign-in only. `create`
    creates the account with a password atsfill generates and stores first (a side effect),
    giving the sign-up the user's `details`; with a password already stored, the account may
    exist, so it signs in with that password and keeps the account's email instead, the
    `details` finishing a sign-up the account was left in. An account without a password signs
    in through the user's OTP command in place of a password source, and is created the same
    way."""
    account, data = _account(adapter), paths.data_dir(data_dir)
    scope = _scope(account, url)
    mine = paths.user_dir(data, user)
    session = accounts.session_file(mine, adapter, scope)
    report = SessionReport(user, adapter, scope, "missing", session)
    if create and not email:
        raise ValueError("creating an account needs --email")
    if details and not create:
        raise ValueError("--details goes with --create")
    by_otp = isinstance(account, SignsInByOtp)
    if password and by_otp:
        raise ValueError(f"{adapter} signs in with an emailed OTP, not a password")
    try:
        stored = passwords.stored(mine, adapter, scope) if create and not by_otp else None
    except passwords.PasswordError as e:
        report.status, report.reason = Status.BLOCKED, f"password_source: {e}"
        return report
    kept = stored is not None
    if email and not kept:
        accounts.remember(mine, adapter, scope, email)
    if create and not kept:
        assert email
        return _create(report, account, url, mine, email, details or SignUpDetails(), data)
    if kept:
        report.password = "kept"
        # the details finish a sign-up the account may have been left in
        if (
            details
            and isinstance(account, CreatesAccount)
            and (missing := details.missing(account.asks))
        ):
            report.status = Status.REFUSED
            report.reason = f"sign_up_details: no {', '.join(missing)}"
            return report
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
        result = _headless(data, user, account, scope, credentials, session, details)
        if (
            kept
            and isinstance(account, CreatesAccount)
            and (result.reason or "").split(":")[0] in NOT_SIGNED_UP
            and credentials[1] == stored
        ):
            # a sign-up blocked before the account existed: sign up again, same password
            return _create(
                report,
                account,
                url,
                mine,
                credentials[0],
                details or SignUpDetails(),
                data,
                kept=credentials[1],
            )
        report.status = result.status
        report.reason = _next_step(result.reason, credentials[0], adapter, user, url)
    if report.reason is None:
        report.session = report.status = "signed_in"
    return report


def _create(
    report: SessionReport,
    account: NeedsAccount,
    url: str | None,
    mine: Path,
    email: str,
    details: SignUpDetails,
    data: Path,
    kept: str | None = None,
) -> SessionReport:
    """Creates the account: refused before the browser opens when `details` lack one the
    sign-up asks; the generated password is stored before the click, and deleted only on a
    refusal the page states before any account exists. A `kept` password, stored by an
    earlier sign-up, is used again and never deleted. An account without a password gets
    none."""
    if not isinstance(account, (CreatesAccount, CreatesAccountByOtp)):
        report.status = Status.UNSUPPORTED
        report.reason = f"no_sign_up: atsfill cannot create a {account.name} account"
        return report
    if missing := details.missing(account.asks):
        report.status = Status.REFUSED
        report.reason = f"sign_up_details: no {', '.join(missing)}"
        return report
    password = kept or (None if isinstance(account, CreatesAccountByOtp) else passwords.generate())
    try:
        if password and not kept:
            passwords.store(mine, account.name, report.scope, password)
    except passwords.PasswordError as e:
        report.status, report.reason = Status.BLOCKED, f"password_source: {e}"
        return report
    credentials = (email, password)
    result = _headless(
        data, report.user, account, report.scope, credentials, report.path, details, create=True
    )
    report.status = result.status
    report.reason = _next_step(result.reason, email, account.name, report.user, url)
    report.password = "kept" if kept else "stored" if password else "none"
    if (
        password
        and not kept
        and result.status is Status.REFUSED
        and (result.reason or "").split(":")[0] in NO_ACCOUNT
    ):
        passwords.delete(mine, account.name, report.scope)
        report.password = "deleted"
    if report.reason is None:
        report.session = report.status = "signed_in"
    return report


def _headless(
    data: Path,
    user: str,
    account: NeedsAccount,
    scope: str | None,
    credentials: Credentials,
    session: Path,
    details: SignUpDetails | None = None,
    create: bool = False,
) -> Result:
    """Signs in, or creates the account with `create`, through the adapter, an OTP it meets
    from the user's `otp-commands` entry: `ready` once the session is stored."""
    assert isinstance(account, (SignsIn, SignsInByOtp))
    url = account.login_url(scope)

    def run() -> Result:
        result = Result(Status.READY, url, account.name)
        with open_page(data, None) as page, _stops(result), otp.source(user, account.name, scope):
            accounts.sign_in(page, account, scope, *credentials, session, details, create)
        return result

    return _in_budget(url, Seen(), run)


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
