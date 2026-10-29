# 0006. The submit gate

## Decision

**Dry run by default.** `fill` fills and reads back, and never clicks submit. Only `send` submits.

**Submit is enabled by the home's owner, in a file.** The `submit-allowed` list in `$XDG_CONFIG_HOME/atsfill/config.toml` (`~/.config/atsfill/config.toml` when the variable is unset, `~/.atsfill/config.toml` elsewhere) names the adapters allowed to submit, per user (ADR 0015). There is no flag or environment variable for it: the agent writes the command line, so anything on it is the agent's to widen. Without the list, every adapter reads and dry-runs and every `send` is refused, naming the adapter.

**The harness asks before each send.** The consumer's harness allows `read` and `fill` and asks the home's owner before `send`. The config file stops an agent that follows the contract; the permission prompt is the human check on every real application.

**`send` fills again on a fresh page** and submits only when its own read-back matches: no answer for a required field, a value outside a field's options, or any mismatch refuses.

**Every form command returns one status:**

| Status | Means |
|---|---|
| `ready` | dry run clean |
| `refused` | a mismatch, a missing answer, an answer the page would not take, or an adapter not enabled to submit |
| `submitted` | sent, with the evidence |
| `blocked` | a captcha challenge, an OTP atsfill could not get, a low reCAPTCHA score, a login wall, or the time budget (ADR 0004) running out before the click stopped it; the reason comes back |
| `unknown` | submit was clicked and no evidence came before the time budget ran out |
| `unsupported` | no adapter for the system, a required field atsfill cannot fill (ADR 0014), a flow the adapter does not handle, or a step on another site; the reason comes back |

The reason is a snake_case code, followed by `: detail` where there is one: `invalid_answers: …`, `answer_not_taken: …` (no suggestion or option holds it), `mismatch: <field ids>`, `submit_not_enabled`, `already_sent`, `no_otp_command`, `time_budget`, `overlaid: …` (the overlay on a control), `no_adapter`, `out_of_scope: <field id> is <what>`, `unsupported_rendering: <gap id>: …`, `unsupported_flow: <gap id>: …` (a known gap, ADR 0014), `unhandled_rendering: …` (a rendering the adapter does not know), `external_step: …`, `adapter_error: …` (an error the adapter did not expect, before any click), `no_evidence`, `error_after_click: …`.

The exit code is 0 for `ready` and `submitted`, 1 for any other status, 2 for a usage error.

**Double-send guard.** Before clicking submit, atsfill appends the attempt to the user's `attempts.jsonl` (ADR 0015), keyed by the posting. A `send` for a posting whose last attempt ended `submitted` or `unknown` is refused unless it passes `--resend`. This covers a command killed after the click, which no consumer can tell apart from one that failed before it.

**A form that shows only once the application is sent.** Some systems send the application on Apply and only then show the steps that complete it (ADP MyJobs): every form command opens that form, `read` and `fill` included, so none of them is a dry run there. The adapter says so (`SendsToOpen`), and the core holds each form command to a send's rules: refused without the adapter in `submit-allowed`, the open recorded `opened` with its evidence, or `unknown` when an error the adapter did not expect may have followed it. A later command continues that application rather than sending another; its `send` is the click that completes it, and an `opened` attempt does not count against it. The harness's prompt before `send` no longer stands before the first send of such a system: the `submit-allowed` entry is the owner's consent to it.

**OTPs.** An emailed OTP is typed in while the page waits, from the user's `otp-commands` entry (ADR 0015). Without an entry the command ends `blocked: no_otp_command`, with no OTP in time `blocked: otp_not_received`, with one the page does not take `blocked: otp_rejected`; an SMS OTP ends `blocked: sms_otp`. The page closes either way: the next attempt meets a fresh OTP.
