# Contributing

Issues and pull requests are welcome, including fully AI-generated ones.

## AI disclosure

Say in the issue or pull request which harness and model wrote it (for example "Claude Code, Claude Opus 5.5"), or that you wrote it yourself.

Commit messages carry no AI attribution or disclosure: no `Co-authored-by:` trailers for a tool, no "Generated with" lines. CI rejects a commit that has one, so turn your harness's commit attribution off. A squash merge builds its message from the commits, never from the pull request description, which keeps the disclosure off `main`.

## Setup

```sh
uv sync
```

```sh
uv run atsfill install-browser
```

## Checks

```sh
uv run ruff check && uv run ruff format && uv run ty check
```

```sh
uv run pytest tests/<file>
```

Tests run in one process by default. The whole suite in parallel, as CI runs it, launches a browser per core: `uv run pytest -n auto --dist loadscope`.

## Survey

`uv run python -m scripts.survey` reads and dry-runs postings with the committed Test user and writes one row per URL to `.tmp/corpus/` ([ADR 0013](docs/adr/0013-survey-and-adapter-rounds.md)). It never sends. Sampling a source needs:

- the [freehire CLI](https://github.com/strelov1/freehire-cli): `mise use -g github:strelov1/freehire-cli`;
- the `freehire-search` skill, committed under `.agents/` and `.claude/skills/`; to reinstall it: `npx skills add shbernal/skills --skill freehire-search -a claude-code -a codex -y`.

`uv run python -m scripts.gate <system> [--fresh]` says whether an adapter is done: the fixture page and doc, lint, types and the system's tests (the whole suite is CI's), a dry run `ready` on five employers at the current code (a commit to docs, tests or the registry keeps it), request read parity. On PASS it commits the system as `supported` to the registry ([ADR 0016](docs/adr/0016-telemetry-and-run-history.md)). Its output names no employer or posting, so it can go in a pull request.

For a system that needs an account, the survey and the adapter gate take `--data-dir` set to the data directory holding the Test user's account, and the dry runs fill under it. A round creates that account with `atsfill login <adapter> --user test --create`, never a personal one ([ADR 0013](docs/adr/0013-survey-and-adapter-rounds.md)).

Commands run from a checkout send their telemetry as `source = dev`, with the commit when the tree has no uncommitted changes, so the run history tells development runs from users' ([docs/telemetry.md](docs/telemetry.md)). The test suite and CI never send.

`uv run python -m scripts.failures <system> <command> [reason]` lists the postings behind a `failure` issue, with their URLs and builds: from the run's event at `detailed`, else by hashing freehire's postings of that system; one not found, usually closed, is printed unresolved. It needs `POSTHOG_READ_KEY` in `.env`, and its output stays local.

## Triage

`scripts/triage.sh` works the `failure` issues, one per run, run hourly by a systemd user timer on the maintainer's machine. It syncs them, claims the oldest open one no triage has taken with `agent:working`, and runs a headless agent on it with `scripts/triage.md` in `.claude/worktrees/triage` at `origin/main`. The agent dry-runs, never sends, and opens a pull request it never merges. It ends on one label, `agent:done`, `cannot-reproduce`, `agent:skip` or `agent:stuck`, and a later run never takes the issue again. A reopened issue loses that label. A claim left by an interrupted run is released once, then labelled `agent:stuck`. To hand an issue back to triage, remove its label.

```ini
# ~/.config/systemd/user/atsfill-triage.service
[Service]
Type=oneshot
Environment=PATH=%h/.local/share/mise/shims:/usr/bin
ExecStart=/usr/bin/flock -n %t/atsfill-triage.lock %h/Work/atsfill/scripts/triage.sh

# ~/.config/systemd/user/atsfill-triage.timer
[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

`systemctl --user enable --now atsfill-triage.timer` starts it, `journalctl --user -u atsfill-triage` shows its runs, and `TRIAGE_BUDGET_USD` caps one run's spend.

## Other scripts

`uv run python -m scripts.backlog` regenerates [docs/adapters/README.md](docs/adapters/README.md), the README's adapter badges and the docs site's carriers from the registry.

## Docs site

`uv run --group docs zensical serve` previews the [docs site](https://shbernal.github.io/atsfill/): `docs/` with the home page in `overrides/home.html`, its design in [DESIGN.md](DESIGN.md). Each push to `main` deploys it.

## Rules

- The repository is public and holds no personal data: answers, resumes, posting URLs, sessions and account emails stay in the data directory, the config file or the gitignored `.tmp/`. Test pages are hand-made, never captured from an employer.
- A hiring system's behaviour is observed, not specified: its doc in [docs/adapters/](docs/adapters/), its adapter and its test page change together.
- The send rules (matching, refusal, statuses, double-send guard) live in the core. An adapter only knows its system's page. The reasons are in [docs/adr/](docs/adr/README.md).
