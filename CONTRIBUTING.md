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

`uv run python -m scripts.regressions <system> <command>` lists the postings behind a `regression` issue, with their URLs and the builds on both sides: from the run's event at `detailed`, else by hashing freehire's postings of that system; one not found, usually closed, is printed unresolved. It needs `POSTHOG_READ_KEY` in `.env`, and its output stays local.

`uv run python -m scripts.backlog` regenerates [docs/adapters/README.md](docs/adapters/README.md), the README's adapter badges and the docs site's carriers from the registry.

## Docs site

`uv run --group docs zensical serve` previews the [docs site](https://shbernal.github.io/atsfill/): `docs/` with the home page in `overrides/home.html`, its design in [DESIGN.md](DESIGN.md). Each push to `main` deploys it.

## Rules

- The repository is public and holds no personal data: answers, resumes, posting URLs, sessions and account emails stay in the data directory, the config file or the gitignored `.tmp/`. Test pages are hand-made, never captured from an employer.
- A hiring system's behaviour is observed, not specified: its doc in [docs/adapters/](docs/adapters/), its adapter and its test page change together.
- The send rules (matching, refusal, statuses, double-send guard) live in the core. An adapter only knows its system's page. The reasons are in [docs/adr/](docs/adr/README.md).
