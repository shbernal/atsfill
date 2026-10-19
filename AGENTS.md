`atsfill`: CLI that reads, dry-runs and submits job application forms on hiring systems (Ashby, Teamtailor, Greenhouse, Welcome to the Jungle), one adapter per system.

- Key commands
  - `uv run pytest` (add `-m live` for dry runs on real postings)
  - `uv run ruff check && uv run ruff format && uv run ty check`

- Key documentation
  - [docs/adr/](docs/adr/README.md) the decisions the design rests on
  - [docs/adapters/](docs/adapters/) each hiring system as observed

- Rules
  - Public repository. Personal data (answers, resumes, posting URLs, sessions) lives only in the gitignored `.env` and `.tmp/`, never in commits, fixtures or docs.
  - The same goes for names: no employer, company or applicant names in code, fixtures, docs, branch names or commit messages. Use generic placeholders (`Acme`, `Jane Doe`).
  - Development dry-runs real postings freely. A `send` is a real application: never during development unless the user asks for that send.
  - The send rules (matching, refusal, statuses, double-send guard) live in the core, once. An adapter only knows its system's page.
  - A hiring system's behaviour is observed, not specified. Its doc, its adapter and its fixture page change together.

- Iron Laws
  - Tokens are expensive, state of the art models need minimal guidance, don't repeat yourself, don't babysit, don't be over-specific.
  - AI-native project. All code is AI-generated.
  - Minimal attention when model implements without errors, we document in more detail when model struggles.
  - Do not expect the user to have read each line, don't lose him on the internals, give visibility on a higher-architectural level.
  - No journaling: code comments / documentation describe current state, they don't carry a log of their own edit history.
