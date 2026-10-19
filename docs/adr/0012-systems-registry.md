# 0012. Systems registry

## Context

Which hiring system gets the next adapter depends on how many postings it carries, whether it needs an account, and whether it lets Camoufox through. Without a record, those findings stay in a session transcript, and a block seen on one machine cannot be compared with one seen on another.

## Decision

One committed file in the package records every mapped system, one entry per freehire `source`:

- **Kind**: `ats`; `board`, a job board that takes the application itself (Welcome to the Jungle); `aggregator`, which links out, its share added to the systems it resolves to; or `custom`, a form the company built, counted and out of scope.
- **Share** of postings, worldwide, from the freehire `source` facet with no market filter.
- **Account**: `none`, `to_send` (needed to submit) or `to_read` (needed to see the form). Whether the form is hosted by the system or embedded on the company's site.
- **Block**, where one was met: the vendor (DataDome, Cloudflare, Akamai, PerimeterX or `unknown`), the signal it was recognised by, the step it stopped at (posting, form, upload), the date, the atsfill version, the camoufox package version and the browser build. The evidence stays in `.tmp/`.
- **Adapter state**: `mapped`, `dry_run_verified`, `send_verified`, each with its date, exact atsfill version and verifier. Only the user sets `send_verified`.

The Camoufox browser build is pinned along with the package (ADR 0004), so a block result compares across machines.

`atsfill status` shows the registry, `send` warns when its adapter is not send verified, and the backlog in `docs/adapters/` is generated from it.

## Consequences

The registry is public: it names systems and vendors, never companies, postings or people. A share is a snapshot of freehire's index on its date and drifts as the index grows.
