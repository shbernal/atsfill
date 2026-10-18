# 0006. The submit gate

## Decision

**Dry run by default.** `fill` fills and reads back, and never clicks submit. Only `send` submits.

**Submit is enabled by the user, in a file.** The `submit` list in `$XDG_CONFIG_HOME/atsfill/config.toml` (`~/.config/atsfill/config.toml` when the variable is unset, `~/.atsfill/config.toml` elsewhere) names the adapters allowed to submit. There is no flag or environment variable for it: the agent writes the command line, so anything on it is the agent's to widen. Without the list, every adapter reads and dry-runs and every `send` is refused, naming the adapter.

**The harness asks before each send.** The consumer's harness allows `read` and `fill` and asks the user before `send`. The config file stops an agent that follows the contract; the permission prompt is the human check on every real application.

**`send` fills again on a fresh page** and submits only when its own read-back matches: no answer for a required field, a value outside a field's options, or any mismatch refuses.

**Every form command returns one status:**

| Status | Means |
|---|---|
| `ready` | dry run clean |
| `refused` | a mismatch, a missing answer, or an adapter not enabled to submit |
| `submitted` | sent, with the evidence |
| `blocked` | a captcha challenge, an emailed code, a reCAPTCHA score refusal, a login wall, or the time budget (ADR 0004) running out before the click stopped it; the reason and the URL come back |
| `unknown` | submit was clicked and no evidence came before the time budget ran out |
| `unsupported` | no adapter for the system, an account required, or a form shape the adapter does not handle; the reason comes back |

The reason is a snake_case code, followed by `: detail` where there is one: `invalid_answers: …`, `mismatch: <field ids>`, `submit_not_enabled`, `already_sent`, `email_code`, `time_budget`, `covered: …` (what sits on a control), `no_adapter`, `form_shape: …`, `adapter_error: …` (an error the adapter did not expect, before any click), `no_evidence`, `error_after_click: …`.

The exit code is 0 for `ready` and `submitted`, 1 for any other status, 2 for a usage error.

**Double-send guard.** Before clicking submit, atsfill appends the attempt to `attempts.jsonl` in its data directory, keyed by the posting. A `send` for a posting whose last attempt ended `submitted` or `unknown` is refused unless it passes `--resend`. This covers a command killed after the click, which no consumer can tell apart from one that failed before it.

**Emailed codes** end the command as `blocked: email_code` and close the page. The consumer starts over later.
