import argparse
import dataclasses
import json
import sys
from pathlib import Path

from atsfill import core
from atsfill.core.results import Result, Status, StatusReport

OK = {Status.READY, Status.SUBMITTED}


def session(value: str) -> tuple[str, Path]:
    adapter, sep, path = value.partition("=")
    if not (sep and adapter and path):
        raise argparse.ArgumentTypeError(f"expected <adapter>=<path>, got {value!r}")
    return adapter, Path(path)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="atsfill")
    p.add_argument("--data-dir", type=Path, help="default: $ATSFILL_DATA_DIR, then the state dir")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("read", "fill", "send"):
        c = sub.add_parser(name)
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
            c.add_argument("--answers", required=True, help="JSON file, - for stdin")
    sub.choices["send"].add_argument("--resend", action="store_true")
    sub.add_parser("status")
    for name in ("login", "logout"):
        sub.add_parser(name).add_argument("adapter")
    return p


def load_answers(path: str) -> dict:
    return json.load(sys.stdin) if path == "-" else json.loads(Path(path).read_text())


def run(args: argparse.Namespace) -> int:
    match args.command:
        case "read":
            result = core.read(args.url, data_dir=args.data_dir, sessions=dict(args.session))
        case "fill":
            answers = load_answers(args.answers)
            result = core.fill(
                args.url, answers, data_dir=args.data_dir, sessions=dict(args.session)
            )
        case "send":
            answers = load_answers(args.answers)
            result = core.send(
                args.url,
                answers,
                resend=args.resend,
                data_dir=args.data_dir,
                sessions=dict(args.session),
            )
        case "status":
            report = core.status(data_dir=args.data_dir)
            emit(report)
            return 0
        case "login" | "logout":
            getattr(core, args.command)(args.adapter, data_dir=args.data_dir)
            return 0
    emit(result)
    return 0 if result.status in OK else 1


def emit(value: Result | StatusReport) -> None:
    json.dump(dataclasses.asdict(value), sys.stdout, default=str, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def main() -> None:
    sys.exit(run(parser().parse_args()))
