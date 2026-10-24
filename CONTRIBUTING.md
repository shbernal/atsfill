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

`uv run python -m scripts.gate <system> [--fresh]` says whether an adapter is done: the fixture page and doc, lint, types and the system's tests (the whole suite is CI's), a dry run `ready` on five employers at the current code (a commit to docs, tests or the registry keeps it), `read_api` parity. On PASS it commits `dry_run_verified` to the registry; `--broken "<reason>" --cause <cause>` commits `send_broken` ([ADR 0012](docs/adr/0012-systems-registry.md)). Its output names no employer or posting, so it can go in a pull request.

For a system that needs an account, the survey and the adapter gate take `--data-dir` set to the data directory holding the Test user's account, and the dry runs fill under it. A round creates that account with `atsfill login <adapter> --user test --create`, never a personal one ([ADR 0013](docs/adr/0013-survey-and-adapter-rounds.md)).

`uv run python -m scripts.backlog` regenerates [docs/adapters/README.md](docs/adapters/README.md) and the README's adapter badges from the registry.

## Rules

- The repository is public and holds no personal data: answers, resumes, posting URLs, sessions and account emails stay in the data directory, the config file or the gitignored `.tmp/`. Test pages are hand-made, never captured from an employer.
- A hiring system's behaviour is observed, not specified: its doc in [docs/adapters/](docs/adapters/), its adapter and its test page change together.
- The send rules (matching, refusal, statuses, double-send guard) live in the core. An adapter only knows its system's page. The reasons are in [docs/adr/](docs/adr/README.md).
