`atsfill`: CLI that reads, dry-runs and submits job application forms on hiring systems (Ashby, Teamtailor, Greenhouse, Welcome to the Jungle), one adapter per system.

- Key commands
  - `uv run pytest tests/<file>`: the files a change touches; the whole suite runs in CI
  - `uv run ruff check && uv run ruff format && uv run ty check`

- Key documentation
  - [docs/adr/](docs/adr/README.md) the decisions the design rests on
  - [docs/adapters/](docs/adapters/) each hiring system as observed

- Rules
  - Public repository. Personal data (answers, resumes, posting URLs, sessions, account emails) is never committed, in code, fixtures or docs: it stays in the data directory, the config file or the gitignored `.tmp/`.
  - The same goes for names: no employer or user names in code, fixtures, docs, branch names or commit messages. Use generic placeholders (`Acme`, `Jane Doe`).
  - Development dry-runs real postings freely. A `send` is a real application: never during development unless the user asks for that send.
  - The send rules (matching, refusal, statuses, double-send guard) live in the core, once. An adapter only knows its system's page.
  - A hiring system's behaviour is observed, not specified. Its doc, its adapter and its fixture page change together.
  - CI green on the first run: merge. A red run counts even if a re-run goes green; re-run only to tell a flake from a break.
  - A flake (red then green on one commit, main red after a green PR, green locally and red in CI) in the code the PR touches is its bug: fix it before merging. Elsewhere: find or file a `flaky` issue, then merge. After merging, check main's CI the same way.
  - Waits and timeouts are named constants, never hardcoded.
  - OTPs are a known gap; each one met (sign-in, sign-up or form) gets an `otp-required` issue naming the system and the step, never the posting: the count decides whether to handle them.
  - Development creates accounts for the Test user freely, never for a real user.

- Iron Laws
  - Tokens are expensive, state of the art models need minimal guidance, don't repeat yourself, don't babysit, don't be over-specific.
  - AI-native project. All code is AI-generated.
  - Minimal attention when model implements without errors, we document in more detail when model struggles.
  - Do not expect the user to have read each line, don't lose him on the internals, give visibility on a higher-architectural level.
  - No journaling: code comments / documentation describe current state, they don't carry a log of their own edit history.

- Domain model
  - Domain terms are defined in `GLOSSARY.md`. Use those terms in code, comments, and conversation; never use a word listed under `_Avoid_` to refer to a domain term.
