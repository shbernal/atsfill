# 0013. Survey and adapter rounds

## Context

The registry (ADR 0012) is filled by trying real postings, and adapters are added one system at a time by an agent. Both need a repeatable procedure and a definition of done that does not depend on reading the agent's transcript.

## Decision

**Survey.** `scripts/survey.py`, a development tool outside the CLI, runs `read` and a `fill` dry run with the Test user (ADR 0009) over a list of URLs, one browser at a time. It writes one row per URL to `.tmp/corpus/`, with the commit it ran at (none on a tree with uncommitted changes), and labels what stopped the form: a login wall, a `block`, an embed, a redirect, `out_of_scope`, a `known_gap`, an `unhandled` case (ADR 0014), or `unclear` for the agent to judge. It never runs `send`.

**Coverage.** Postings come from freehire. Mapping covers the sources that add up to 90% of postings, five different employers each; an aggregator is resolved by following its links to the systems behind it.

**Backlog order.** Account `none` first, then `to_send`, then `to_read`. Systems with a block go last; there is no evasion work beyond what Camoufox does.

**Adapter gate.** An adapter is done when `python -m scripts.gate <system>` prints PASS: a fixture page, an adapter doc, lint, types and the system's tests green, a dry run `ready` on at least five employers outside the round's sample, and request read parity (ADR 0011) where the adapter has one. The whole suite is CI's: every merge requires its check, so the adapter gate does not run it again. A row counts when it was surveyed at a commit whose code is the current commit's: nothing under `src/atsfill/` but the registry differs, so a commit to docs, tests or the registry keeps the dry runs. On a tree with uncommitted changes the dry-run criterion fails. `--fresh` surveys about twice the threshold from the system's own hosts first. The adapter gate lists the rows on the system that were not `ready`, by status and label, without a threshold: the pull request's reviewer judges them. An adapter may ship send unverified.

**Skipped postings.** The system's field vocabulary and every way a field type renders are the adapter's job. Only a closed posting, an apply step on another system, a page the employer built, or an `out_of_scope` posting (ADR 0014) counts as a skipped posting. A known gap or an unhandled case is not ready.

**Rounds.** A round is one `/goal` agent run for one system, on branch `adapter/<system>`, capped at about 80 turns:

1. Survey about 15 employers on the system (`survey --source`); these rows are its sample.
2. Write a field type × rendering inventory into the adapter doc: each rendering handled and how many postings showed it; one not handled yet goes under "Known gaps" (ADR 0014). Compose the fixture page of one example per rendering, not a copy of one posting.
3. Make the adapter handle the inventory, then run `gate --fresh`.

**Account prep.** A system that needs an account for the dry run (its registry entry and adapter doc say so) is dry-run verified only under a Test user account, and the round creates it, never the adapter gate: a verification link is clicked in the mailbox, which atsfill does not read (ADR 0015). `atsfill login <adapter> --user test --create --email <address>` with a mailbox address, `--url <posting>` where the account is per employer, `--name` where the sign-up asks it; on `blocked: email_link` the link is clicked and `login` runs again. The session and `accounts.json` land in the data directory under `users/test/`, the generated password in the secret store, which is the Test user's password source (a `password-commands` entry under `[users.test]` for an account made otherwise). The survey and the adapter gate take that data directory with `--data-dir`, and a later session needs nothing more: signed out, a form command signs in again from the password source. Where the account is per employer, `gate --fresh` stops on each fresh employer with `login_wall`; the round creates an account on each from the rows in `.tmp/corpus/gate-<system>.jsonl`, surveys those postings again with `--data-dir`, then runs the gate without `--fresh`, which counts the latest row of each posting.

It ends in a pull request with the adapter gate output in its description, or, when no adapter can be made, in a registry entry that says why: `account = "to_read"`, a `block`, `unsupported` naming its known gap, or `out_of_scope` (ADR 0012). The user starts every round and merges every pull request; a patch release follows.

## Consequences

A dry-run verified adapter rests on the adapter gate and on CI together; if the ruleset stops requiring CI's check, the adapter gate no longer stands for a green suite. The adapter gate checks what can be checked without a send, so a merged adapter is dry-run verified, not send verified; the registry and the `send` warning say which. Postings close fast, so an adapter gate run cannot be replayed later on the same URLs. A survey with `--data-dir` holding a Test user account fills under it, so its dry runs are visible to the system as that account's.
