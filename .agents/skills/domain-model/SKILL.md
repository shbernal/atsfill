---
name: domain-model
description: Build and sharpen a project's domain model, its glossary of terms and its architectural decisions. Use when discussing codebase terminology, writing or editing a GLOSSARY.md, or recording an ADR.
---

Actively build and sharpen the project's domain model as you design: challenge terms, invent edge-case scenarios, and write the glossary and decisions down the moment they settle. Merely *reading* `GLOSSARY.md` for vocabulary is not this skill; this is for *changing* the model.

Only when the user invokes the skill as `/domain-model <command>`, follow `commands/<command>.md` instead of the session behaviour below. Otherwise never read `commands/`.

The model lives in two places, both created lazily, only when there is something to write:

- `GLOSSARY.md` at the repo root: the project's terms, in the format in [GLOSSARY-FORMAT.md](GLOSSARY-FORMAT.md).
- `docs/adr/`: decisions, in the format in [ADR-FORMAT.md](ADR-FORMAT.md). If the repo already keeps decisions elsewhere, use that.

When you create `GLOSSARY.md`, make sure the repo's `AGENTS.md` tells agents to use its terms and avoid the words under `_Avoid_`; otherwise the glossary gets written and never read.

## During the session

- **Challenge against the glossary.** When the user uses a term that conflicts with `GLOSSARY.md`, call it out: "The glossary defines 'cancellation' as X, but you seem to mean Y. Which is it?"
- **Sharpen fuzzy language.** When a term is vague or overloaded, propose a precise canonical one: "By 'account', do you mean the Customer or the User?"
- **Discuss concrete scenarios.** Stress-test domain relationships with invented edge cases that force precise boundaries between concepts.
- **Cross-reference with code.** When the user states how something works, check the code agrees, and surface contradictions: "Your code cancels entire Orders, but you said partial cancellation is possible. Which is right?"
- **Update `GLOSSARY.md` inline.** Write each term the moment it is resolved; don't batch. The glossary holds no implementation details: it is not a spec or a scratch pad.
- **Offer ADRs sparingly.** Only when the decision is hard to reverse, surprising without context, *and* the result of a real trade-off. If any of the three is missing, skip it.
