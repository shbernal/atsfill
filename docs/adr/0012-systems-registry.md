# 0012. Systems registry

## Context

Which hiring system gets the next adapter depends on how many postings it carries, whether it needs an account, and whether it lets Camoufox through. Without a record, those findings stay in one agent run's local log, and a block seen on one machine cannot be compared with one seen on another.

## Decision

One committed file in the package records every mapped freehire `source`, one entry each:

- **Kind**: `ats`; `board`, a job board that takes the application itself (Welcome to the Jungle); `aggregator`, a job aggregator or a career-site front-end whose postings only link to a form on another source, its share added to the sources it resolves to, listed in `links_to`; or `custom`, a form the employer built, counted and never given an adapter. Only an `ats` or a `board` is a hiring system and gets an adapter.
- **Share** of postings, worldwide, from the freehire `source` facet with no market filter.
- **Account**: `none`, `to_send` (needed to submit) or `to_read` (needed to see the form). Whether the form is hosted by the system or embedded on the employer's site.
- **Block**, where one was met: the protection, by the name its signal carries (DataDome, Cloudflare, Akamai, PerimeterX, hCaptcha or `unknown`), the signal it was recognised by, the step it stopped at (posting, form, upload), the date, the atsfill version, the camoufox package version and the browser build. The evidence stays in `.tmp/`.
- **Scope** (ADR 0016): `supported`, with the date and commit of the dev dry run that passed the adapter gate; `in_scope`, wanted, with the `reason` a round stopped at, the known gap by field type and step, never postings, which puts the system last in the backlog with the ones that have a block; or `out_of_scope`, a source atsfill will never fill (ADR 0014), a video-first system for instance, which leaves the backlog's main table for the side table, with its reason.

The Camoufox browser build is pinned along with the package (ADR 0004), so a block result compares across machines.

`atsfill status` shows each adapter's scope. The backlog in `docs/adapters/` and each adapter's run history image in the README are generated from it.

## Consequences

The registry is public: it names systems and vendors, never employers, postings or people. A share is a snapshot of freehire's index on its date and drifts as the index grows.
