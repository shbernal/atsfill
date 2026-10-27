# init: bootstrap the glossary from an existing codebase

If `GLOSSARY.md` already exists, say so and offer `audit` instead.

1. Read for names, not implementation: README, docs, `AGENTS.md`, model/schema/type names, API routes, UI copy.
2. Collect candidate terms: project-specific nouns that recur across code and docs. Note synonyms (Customer / Client / Account) and overloaded words.
3. Write the uncontested terms to `GLOSSARY.md` in the format in [GLOSSARY-FORMAT.md](../GLOSSARY-FORMAT.md).
4. Resolve the contested ones with the user, one at a time, writing each as it settles.
5. Make sure `AGENTS.md` points agents to the glossary.

Write no ADRs: the code shows what was decided, not why.
