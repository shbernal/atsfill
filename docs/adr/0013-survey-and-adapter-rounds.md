# 0013. Survey and adapter rounds

## Context

The registry (ADR 0012) is filled by trying real postings, and adapters are added one system at a time by an agent. Both need a repeatable procedure and a definition of done that does not depend on reading the agent's transcript.

## Decision

**Survey.** `scripts/survey.py`, a development tool outside the CLI, runs `read` and a `fill` dry run with the test persona (ADR 0009) over a list of URLs, one browser at a time. It writes one row per URL to `.tmp/corpus/` and labels what stopped the form: a login wall, a block, an embed, a redirect, or `unclear` for the agent to judge. It never runs `send`.

**Corpus.** Postings come from freehire. Mapping covers the sources that add up to 90% of postings, five different companies each; an aggregator is resolved by following its links to the systems behind it.

**Backlog order.** Account `none` first, then `to_send`, then `to_read`. Blocked systems go last; there is no evasion work beyond what Camoufox does.

**Done.** An adapter is done when `scripts/gate.py <system>` prints PASS: a fixture page, an adapter doc, the committed tests green, a dry run `ready` on at least three companies, and `read_api` parity (ADR 0011) where the adapter has one. An adapter may ship send unverified.

**Rounds.** A round is one `/goal` session for one system, on branch `adapter/<system>`, capped at 40 turns. It ends in a pull request with the gate output in its description, or in a registry entry (`to_read`, `blocked`, `unsupported`) with its descriptor. The user starts every round and merges every pull request.

## Consequences

The gate checks what can be checked without a send, so a merged adapter is dry-run verified, not send verified; the registry and the `send` warning say which. Postings close fast, so a gate run cannot be replayed later on the same URLs.
