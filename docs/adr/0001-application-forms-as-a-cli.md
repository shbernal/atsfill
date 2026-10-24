# 0001. Application forms as a CLI

## Context

Agents that apply to jobs on external hiring systems drive a generic browser and carry each system's quirks as prose: which browser passes the captcha, which field saves only on Enter, what proves a send. The prose is reread on every run, the check before submitting is an instruction rather than code, and every run pays for raw page snapshots twice, once to read the form and once to fill it. LinkedIn's Easy Apply, by contrast, has a tool contract in LinkedIn MCPs: read the form, dry-run the fill, submit only on confirm.

## Decision

atsfill is a standalone command line tool that gives external application forms that contract. It is a product: any agent with a shell is a consumer, and the home's owner decides which hiring systems it lets submit (ADR 0015). Each form command prints one JSON document and exits:

- `atsfill read <url>`: the form's fields, the hiring system and its side effects.
- `atsfill fill <url> --answers <file>`: fills and reads back, never submits (the dry run).
- `atsfill send <url> --answers <file>`: fills again and submits only when the read-back is clean.
- `atsfill status`: version, data directory, browser, and every adapter with its verification.

The document holds what its status needs, on one line, without the keys that are null, false or empty, but for an empty `options`, nothing to choose where null is unknown: a `ready` fill is its status, system and any warning. `-v` prints the whole result, indented.

Sending is its own command, not a flag on `fill`, so an agent harness can ask the home's owner before every `send` (ADR 0006). Answers come from a file (`-` for stdin) so personal data stays out of command lines and shell history.

Finding job postings is out of scope. atsfill starts from an application URL.

## Consequences

A form on a hiring system nobody has written an adapter for returns `unsupported`, and the consumer falls back to driving a browser itself. The prose a consumer keeps for a system goes away once that system's adapter is enabled.

Each command launches and closes its own browser, a few seconds per call. Agents without a shell are not served; a thin MCP wrapper over the same core can serve them later.
