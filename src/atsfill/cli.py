import argparse
import dataclasses
import json
import sys
from pathlib import Path

from atsfill import core
from atsfill.adapters import adapters
from atsfill.core import browser, paths
from atsfill.core.adapter import NeedsAccount
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
application, final. Each command prints one JSON result; exit 0 for ready and submitted,
1 for any other status, 2 for a usage error."""


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
        ("login", "sign in, in a visible window, to a system that needs an account"),
        ("logout", "delete a stored session"),
    ):
        c = with_user(sub.add_parser(name, help=text))
        c.add_argument("adapter", choices=accounts)
        c.add_argument("--url", help="a posting, for a system with an account per employer")
    return p


def answers_file(path: str) -> dict:
    try:
        answers = json.load(sys.stdin) if path == "-" else json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise argparse.ArgumentTypeError(str(e)) from e
    if not isinstance(answers, dict):
        raise argparse.ArgumentTypeError("expected a JSON object {field_id: value}")
    return answers


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
            emit(report)
            signed_out = any(s.session == "signed_out" for s in report.sessions)
            return 1 if signed_out or report.browser != report.browser_pinned else 0
        case "install-browser":
            return browser.install()
        case "login" | "logout":
            command = core.login if args.command == "login" else core.logout
            try:
                session = command(
                    args.adapter, user=args.user, url=args.url, data_dir=args.data_dir
                )
            except ValueError as e:
                print(f"atsfill: {e}", file=sys.stderr)
                return 2
            emit(session)
            return 0 if args.command == "logout" or session.session == "signed_in" else 1
    emit(result)
    return 0 if result.status in OK else 1


def emit(value: Result | StatusReport | SessionReport) -> None:
    json.dump(dataclasses.asdict(value), sys.stdout, default=str, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def main() -> None:
    sys.exit(run(parser().parse_args()))
