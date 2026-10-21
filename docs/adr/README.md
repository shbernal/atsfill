# Architecture decisions

One file per decision, numbered in the order they were taken. A decision that changes gets its file rewritten, or replaced by a new one that says which it supersedes.

| ADR | Decision |
|---|---|
| [0001](0001-application-forms-as-a-cli.md) | Application forms as a CLI: `read`, `fill` (dry run), `send` |
| [0002](0002-stack.md) | Python with ty, Camoufox, uv, MIT, PyPI |
| [0003](0003-adapters.md) | One adapter per hiring system, rules owned by the core |
| [0004](0004-browser.md) | A logged-out headless Camoufox per command, one saved identity, a fresh context each time, one command at a time |
| [0005](0005-answers-and-read-back.md) | Answers keyed by field id, read-back matched by field type |
| [0006](0006-submit-gate.md) | Dry run by default, submit enabled in the user's config file, fixed statuses, a double-send guard |
| [0007](0007-data-directory-and-sessions.md) | Data directory, per-adapter sessions and the login command |
| [0008](0008-side-effects.md) | Side effects declared per adapter |
| [0009](0009-tests.md) | Hand-made fixture pages, live runs opt-in, a committed test persona |
| [0010](0010-package-layout.md) | One package: a thin CLI over a core that owns every rule, adapters behind it |
| [0011](0011-http-read.md) | `read` over HTTP where the system publishes its form, the page otherwise |
| [0012](0012-systems-registry.md) | A committed registry of hiring systems: kind, share, account, block, verification |
| [0013](0013-survey-and-adapter-rounds.md) | A survey script, an adapter gate script, and one agent round per system |
| [0014](0014-out-of-scope.md) | Out of scope is what needs the user live; out of scope, known gap and unhandled told apart at run time |
