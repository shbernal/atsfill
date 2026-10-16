# 0010. Package layout

## Decision

One distribution, three layers:

```
src/atsfill/
  cli.py      arguments, the answers file, JSON on stdout, exit codes
  core/       the browser, matching, the submit gate, attempts, statuses
  adapters/   one module per hiring system (ADR 0003)
```

- The CLI only translates. It turns argv and the answers file into a call to `atsfill.core`, prints the returned result as JSON and maps its status to an exit code (ADR 0006). It holds no rule, so another front end, such as an MCP server, is a file of the same size.
- Core functions take and return typed values (`send(url, answers) -> Result`), never paths to answers, stdin or exit codes.
- The submit gate lives in core: core reads `config.toml` itself, and no caller can pass it permission to submit, acknowledged side effects or a skipped double-send guard beyond `resend`.
- Adapters import from core (`atsfill.core.adapter`, `atsfill.core.results`); core finds them through the registry in `atsfill.adapters`. Neither knows the CLI.
- The sync Playwright API: a command drives one page at a time.

## Rejected

Separate `atsfill-core` and `atsfill` packages, and a public Python API with stability promises. The core has one consumer; it becomes a library when a second one exists.
