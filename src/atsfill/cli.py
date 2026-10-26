import argparse
import dataclasses
import json
import sys
from pathlib import Path

from atsfill import core
from atsfill.adapters import adapters
from atsfill.core import browser, paths
from atsfill.core.adapter import NeedsAccount, SignUpDetails
from atsfill.core.results import Result, SessionReport, Status, StatusReport

OK = {Status.READY, Status.SUBMITTED}


def session(value: str) -> tuple[str, Path]:
    adapter, sep, path = value.partition("=")
    if not (sep and adapter and path):
        raise argparse.ArgumentTypeError(f"expected <adapter>=<path>, got {value!r}")
    return adapter, Path(path)


def user(value: str) -> str:
    if not paths.USER_NAME.fullmatch(value):
        raise argparse.ArgumentTypeError(f"lowercase letters, digits and -, got {value!r}")
    return value


def with_user(command: argparse.ArgumentParser) -> argparse.ArgumentParser:
    command.add_argument(
        "--user",
        type=user,
        default=paths.DEFAULT_USER,
        help="the user applications are sent for (default: %(default)s)",
    )
    return command


HELP = {
    "read": "list a posting's form fields",
    "fill": "dry run: fill the form and read it back, never submit",
    "send": "fill, read back, and submit when every answer matches",
}
EPILOG = """\
A dry run is visible to the hiring system: some save each answer or the resume as it is
typed (`status` lists each adapter's side effects). A `send` that ends `submitted` is a real
application, final. Each command prints one JSON result on one line, without the keys that
are null, false or empty, and after `fill` and `send` only the checks that failed; `-v`
prints all of it. Exit 0 for ready and submitted, 1 for any other status, 2 for a usage
error."""


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="atsfill",
        description="Read, dry-run and submit job application forms on hiring systems.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--data-dir", type=Path, help="default: $ATSFILL_DATA_DIR, then the state dir")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("read", "fill", "send"):
        c = with_user(
            sub.add_parser(name, help=HELP[name], epilog=EPILOG, formatter_class=p.formatter_class)
        )
        c.add_argument("url")
        c.add_argument(
            "--session",
            type=session,
            action="append",
            default=[],
            metavar="ADAPTER=PATH",
            help="storage state for an adapter that needs an account",
        )
        if name != "read":
            c.add_argument(
                "--answers",
                type=answers_file,
                required=True,
                help="JSON {field_id: value} file, - for stdin",
            )
    sub.choices["send"].add_argument(
        "--resend", action="store_true", help="send even though an earlier send may have gone"
    )
    sub.add_parser("status", help="version, paths, browser, and each adapter's state")
    sub.add_parser("install-browser", help="download the Camoufox build atsfill is pinned to")
    accounts = [a.name for a in adapters() if isinstance(a, NeedsAccount)]
    for name, text in (
        ("login", "sign in to a system that needs an account"),
        ("logout", "delete a stored session"),
    ):
        c = with_user(sub.add_parser(name, help=text))
        c.add_argument("adapter", choices=accounts)
        c.add_argument("--url", help="a posting, for a system with an account per employer")
    login = sub.choices["login"]
    login.add_argument("--email", help="the account's email, remembered for it")
    login.add_argument(
        "--password", choices=["-"], help="read the password from stdin, for this sign-in only"
    )
    login.add_argument(
        "--window", action="store_true", help="sign in in a visible window, by a person"
    )
    login.add_argument(
        "--create",
        action="store_true",
        help="create the account with --email and a password atsfill generates and stores",
    )
    login.add_argument(
        "--details",
        type=details_file,
        help="a JSON file of the user's sign-up details (name, phone, address), for --create",
    )
    for name in ("read", "fill", "send", "status", "login", "logout"):
        sub.choices[name].add_argument(
            "-v", "--verbose", action="store_true", help="the whole result, every key, indented"
        )
    return p


def answers_file(path: str) -> dict:
    try:
        answers = json.load(sys.stdin) if path == "-" else json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise argparse.ArgumentTypeError(str(e)) from e
    if not isinstance(answers, dict):
        raise argparse.ArgumentTypeError("expected a JSON object {field_id: value}")
    return answers


def details_file(path: str) -> SignUpDetails:
    try:
        return SignUpDetails.parse(json.loads(Path(path).read_text()))
    except (OSError, ValueError) as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def run(args: argparse.Namespace) -> int:
    try:
        paths.config(getattr(args, "user", paths.DEFAULT_USER))
    except paths.ConfigError as e:
        print(f"atsfill: {e}", file=sys.stderr)
        return 2
    match args.command:
        case "read":
            result = core.read(
                args.url, user=args.user, data_dir=args.data_dir, sessions=dict(args.session)
            )
        case "fill":
            result = core.fill(
                args.url,
                args.answers,
                user=args.user,
                data_dir=args.data_dir,
                sessions=dict(args.session),
            )
        case "send":
            result = core.send(
                args.url,
                args.answers,
                resend=args.resend,
                user=args.user,
                data_dir=args.data_dir,
                sessions=dict(args.session),
            )
        case "status":
            report = core.status(data_dir=args.data_dir)
            emit(report, args.verbose)
            signed_out = any(s.session == "signed_out" for s in report.sessions)
            return 1 if signed_out or report.browser != report.browser_pinned else 0
        case "install-browser":
            return browser.install()
        case "login" | "logout":
            where = {"user": args.user, "url": args.url, "data_dir": args.data_dir}
            try:
                if args.command == "logout":
                    session = core.logout(args.adapter, **where)
                else:
                    password = sys.stdin.readline().rstrip("\n") if args.password else None
                    session = core.login(
                        args.adapter,
                        email=args.email,
                        password=password,
                        window=args.window,
                        create=args.create,
                        details=args.details,
                        **where,
                    )
            except ValueError as e:
                print(f"atsfill: {e}", file=sys.stderr)
                return 2
            emit(session, args.verbose)
            return 0 if args.command == "logout" or session.session == "signed_in" else 1
    emit(result, args.verbose, args.command)
    return 0 if result.status in OK else 1


def emit(
    value: Result | StatusReport | SessionReport, verbose: bool, command: str | None = None
) -> None:
    """Prints the whole value with `verbose`; else what its consumer acts on, on one line."""
    data = dataclasses.asdict(value)
    if verbose:
        json.dump(data, sys.stdout, default=str, ensure_ascii=False, indent=2)
    else:
        if isinstance(value, Result):
            data = _slim(data, command)
        json.dump(_sparse(data), sys.stdout, default=str, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def _slim(result: dict, command: str | None) -> dict:
    """A form command's result without what the consumer already has: the URL it passed and,
    after `read`, the fields, the side effects and each answer it gave."""
    del result["url"]
    if command != "read":
        del result["fields"], result["side_effects"]
        result["checks"] = [
            {"field_id": c["field_id"], "read_back": c["read_back"]}
            for c in result["checks"]
            if not c["match"]
        ]
    return result


def _sparse(value: object) -> object:
    """Drops the keys whose value is null, false or empty, at every depth; empty `options`
    stay, nothing to choose, where null is unknown."""
    if isinstance(value, dict):
        kept = {k: _sparse(v) for k, v in value.items()}
        return {
            k: v
            for k, v in kept.items()
            if v not in (None, False, [], {}) or (k == "options" and v == [])
        }
    if isinstance(value, list):
        return [_sparse(v) for v in value]
    return value


def main() -> None:
    sys.exit(run(parser().parse_args()))
