# GLOSSARY.md format

```md
# {Project name}

{One or two sentences on what this domain is.}

## Language

**Order**:
A request from a Customer to buy one or more products.
_Avoid_: Purchase, transaction

**Customer**:
A person or organization that places Orders.
_Avoid_: Client, buyer, account
```

## Rules

- **Be opinionated.** When several words exist for one concept, pick the best and list the others under `_Avoid_`.
- **Keep definitions tight.** One or two sentences. Define what it *is*, not what it does.
- **Only project-specific terms.** General programming concepts (timeouts, error types, utility patterns) don't belong, however often the project uses them.
- **Group terms under subheadings** when natural clusters emerge; otherwise a flat list.
- **Flag resolved ambiguities** in a closing `## Flagged ambiguities` section when a word used to mean two things, so it isn't reintroduced.
