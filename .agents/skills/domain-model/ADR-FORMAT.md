# ADR format

ADRs are numbered sequentially: `0001-slug.md`, `0002-slug.md`. Scan for the highest number and add one.

```md
# {Short title of the decision}

{1-3 sentences: the context, what we decided, and why.}
```

That's it. The value is recording *that* a decision was made and *why*, not filling out sections. Add these only when they earn their place:

- **Status** (`proposed | accepted | deprecated | superseded by ADR-NNNN`): when decisions get revisited.
- **Considered options**: when the rejected alternatives are worth remembering.
- **Consequences**: when downstream effects are non-obvious.

## What qualifies

All three must hold: hard to reverse, surprising without context, the result of a real trade-off. Typical cases:

- **Architectural shape.** "The write model is event-sourced, the read model is projected into Postgres."
- **Technology choices with lock-in.** Database, message bus, auth provider, deployment target; not every library.
- **Boundary and scope decisions.** "Other modules reference Customer data by ID only." The explicit no's matter as much as the yes's.
- **Deliberate deviations from the obvious path.** "Manual SQL instead of an ORM because X." These stop the next person "fixing" something deliberate.
- **Constraints invisible in the code.** Compliance, partner API contracts, latency budgets.
- **Non-obvious rejections.** If GraphQL lost to REST for subtle reasons, record it, or someone proposes GraphQL again in six months.
