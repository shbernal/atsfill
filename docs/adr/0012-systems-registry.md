# 0012. Systems registry

## Context

Which hiring system gets the next adapter depends on how many postings it carries, whether it needs an account, and whether it lets Camoufox through. Without a record, those findings stay in one agent run's local log, and a block seen on one machine cannot be compared with one seen on another.

## Decision

One committed file in the package records every mapped freehire `source`, one entry each:

- **Kind**: `ats`; `board`, a job board that takes the application itself (Welcome to the Jungle); `aggregator`, a job aggregator or a career-site front-end whose postings only link to a form on another source, its share added to the sources it resolves to, listed in `links_to`; or `custom`, a form the employer built, counted and out of scope. Only an `ats` or a `board` is a hiring system and gets an adapter.
- **Share** of postings, worldwide, from the freehire `source` facet with no market filter.
- **Account**: `none`, `to_send` (needed to submit) or `to_read` (needed to see the form). Whether the form is hosted by the system or embedded on the employer's site.
- **Block**, where one was met: the protection, by the name its signal carries (DataDome, Cloudflare, Akamai, PerimeterX, hCaptcha or `unknown`), the signal it was recognised by, the step it stopped at (posting, form, upload), the date, the atsfill version, the camoufox package version and the browser build. The evidence stays in `.tmp/`.
- **Verification**: `mapped`, `dry_run_verified`, `send_verified`, each with its date, the release (`version`, what `status` compares with the installed one), the exact `commit` and the verifier. Only the user sets `send_verified`.
- **Unsupported**: `unsupported`, a round that ended without its adapter passing the adapter gate (ADR 0013), with date, version, commit, verifier and `reason`: what stopped it, by field type and step, never postings. The system goes last in the backlog with the ones that have a block.
- **Send broken**: `send_broken`, next to `send_verified`, which stays as the last known good, with date, version, commit, verifier, `cause` and `reason`. Present means sending is broken; writing `send_verified` deletes it. `reason` names fields and steps, never postings. `python -m scripts.gate --broken` records it; the adapter gate suggests it when a send-verified system fails the dry-run criterion, and never records it on its own.

The `cause` says what is known, and what to do:

- `system`: the same failure at the verified commit, on a field type that worked in the verified send. Observe the page again, adapt the adapter and its doc.
- `code`: passes at the verified commit, fails at the current one. Bisect.
- `unknown`, the default: anything less. Run the check above.

A re-run at an old commit meets today's postings, not the ones the verified send met, so `system` means the same field type, not the same form. A rendering or a flow the adapter never handled is a coverage gap, not a break, and never goes in `send_broken`.

The Camoufox browser build is pinned along with the package (ADR 0004), so a block result compares across machines.

`atsfill status` shows each adapter's verification, its verified release against the installed one (`this_release`, `older`, `newer`) and a break with its last verified send. `send` warns `not_send_verified` or `send_broken`. The backlog in `docs/adapters/` and one badge per adapter in the README, red when sending is broken, are generated from it.

## Consequences

The registry is public: it names systems and vendors, never employers, postings or people. A share is a snapshot of freehire's index on its date and drifts as the index grows.
