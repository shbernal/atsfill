# 0009. Tests

## Decision

The repository is public and holds no personal data.

- Committed tests run on small hand-made HTML pages that reproduce each system's quirks: the banner over the consent box, the button that moves in a short window, the select that saves only on Enter. Captured employer pages are never committed: they carry company names, tracking ids and sometimes recruiter details.
- Live tests (`pytest -m live`) dry-run real postings with URLs, answers and files from the gitignored `.env` and `.tmp/`. They never run `send`.
