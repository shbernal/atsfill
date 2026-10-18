# Contributing

Issues and pull requests are welcome, including fully AI-generated ones.

## AI disclosure

Say in the issue or pull request which harness and model wrote it (for example "Claude Code, Claude Opus 5.5"), or that you wrote it yourself.

## Setup

```sh
uv sync
```

```sh
uv run atsfill install-browser
```

[lefthook](https://lefthook.dev) runs the format, lint and type checks before each commit and the tests before each push, once installed with `lefthook install`.

## Checks

```sh
uv run ruff check && uv run ruff format && uv run ty check
```

```sh
uv run pytest
```

`uv run pytest -m live` dry-runs real postings from a gitignored `.env` and `.tmp/`. It never sends.

## Rules

- The repository is public and holds no personal data: answers, resumes, posting URLs and sessions stay in `.env` and `.tmp/`. Test pages are hand-made, never captured from an employer.
- A hiring system's behaviour is observed, not specified: its doc in [docs/adapters/](docs/adapters/), its adapter and its test page change together.
- The send rules (matching, refusal, statuses, double-send guard) live in the core. An adapter only knows its system's page. The reasons are in [docs/adr/](docs/adr/README.md).
