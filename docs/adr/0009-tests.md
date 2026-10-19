# 0009. Tests

## Decision

The repository is public and holds no personal data.

- Committed tests run on small hand-made HTML pages that reproduce each system's quirks: the banner over the consent box, the button that moves in a short window, the select that saves only on Enter. Captured employer pages are never committed: they carry company names, tracking ids and sometimes recruiter details.
- Live tests (`pytest -m live`) dry-run real postings with URLs, answers and files from the gitignored `.env` and `.tmp/`. They never run `send`.
- A test persona is committed under `tests/`: an `example.com` email, a fictional phone number and a PDF résumé generated at run time. Its answers are derived from each form's own fields: "decline to answer" where a demographic, disability or veteran question offers it, else the first option; a fixed placeholder for salary; placeholder text otherwise. It lets the survey (ADR 0013) and the adapter gate dry-run postings nobody wrote answers for.
