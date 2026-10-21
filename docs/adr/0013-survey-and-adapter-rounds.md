# 0013. Survey and adapter rounds

## Context

The registry (ADR 0012) is filled by trying real postings, and adapters are added one system at a time by an agent. Both need a repeatable procedure and a definition of done that does not depend on reading the agent's transcript.

## Decision

**Survey.** `scripts/survey.py`, a development tool outside the CLI, runs `read` and a `fill` dry run with the test persona (ADR 0009) over a list of URLs, one browser at a time. It writes one row per URL to `.tmp/corpus/`, with the commit it ran at (none on a tree with uncommitted changes), and labels what stopped the form: a login wall, a block, an embed, a redirect, or `unclear` for the agent to judge. It never runs `send`.

**Coverage.** Postings come from freehire. Mapping covers the sources that add up to 90% of postings, five different employers each; an aggregator is resolved by following its links to the systems behind it.

**Backlog order.** Account `none` first, then `to_send`, then `to_read`. Systems with a block go last; there is no evasion work beyond what Camoufox does.

**Adapter gate.** An adapter is done when `python -m scripts.gate <system>` prints PASS: a fixture page, an adapter doc, the committed tests green, a dry run `ready` on at least five employers outside the round's sample, and `read_api` parity (ADR 0011) where the adapter has one. Only rows surveyed at the current commit count; on a tree with uncommitted changes the dry-run criterion fails. `--fresh` surveys about twice the threshold from the system's own hosts first. The adapter gate lists the rows on the system that were not `ready`, by status and label, without a threshold: the pull request's reviewer judges them. An adapter may ship send unverified.

**Skipped postings.** The system's field vocabulary and every way a field type renders are the adapter's job. Only a closed posting, an apply step on another system, or a page the employer built counts as a skipped posting.

**Rounds.** A round is one `/goal` agent run for one system, on branch `adapter/<system>`, capped at about 80 turns:

1. Survey about 15 employers on the system (`survey --source`); these rows are its sample.
2. Write a field type × rendering inventory into the adapter doc: each rendering, supported or not, and how many postings showed it. Compose the fixture page of one example per rendering, not a copy of one posting.
3. Make the adapter handle the inventory, then run `gate --fresh`.

It ends in a pull request with the adapter gate output in its description, or, when no adapter can be made, in a registry entry that says why: `account = "to_read"`, a `block`, or `unsupported` (ADR 0012). The user starts every round and merges every pull request; a patch release follows.

## Consequences

The adapter gate checks what can be checked without a send, so a merged adapter is dry-run verified, not send verified; the registry and the `send` warning say which. Postings close fast, so an adapter gate run cannot be replayed later on the same URLs. A survey with `--session` fills under that account, so its dry runs are visible to the system as that account's.
