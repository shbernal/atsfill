# 0016. Telemetry and run history

Supersedes the verification parts of ADR 0012: the verification ladder, `send_broken`, the `not_send_verified` and `send_broken` warnings, and the verification badges.

## Context

The verification ladder records what one person saw once: a dry run passed, a send went through. Hiring systems change their pages without notice, so a `send_verified` from last month says little about today, and a break is only found when someone runs into it and records it by hand. Users' own runs are the only steady signal of whether an adapter works, and today they are not seen.

PostHog, checked 2026-10-27:

- Free plan, no card: 1M events a month, 1 year retention, 1 project. Over quota, ingestion stops; there is no bill.
- EU Cloud (Frankfurt) on the free plan, the region fixed when the organization is created. EU Cloud discards IPs by default, and the Python SDK disables GeoIP by default.
- The `phc_` project key only ingests and is documented as safe to ship. Anyone holding it can write events, unthrottled.
- The Python SDK sends from a background thread, in batches. Its atexit flush is capped at 1 s; `shutdown()` has no timeout. `disabled=True` turns it off.
- Anonymous events: `$process_person_profile: false` and a random `distinct_id` per event.
- The SDK drops an event above 900 KiB, the server above 1 MB. There is no attachment: a snapshot goes in an event property.
- The query API takes HogQL (`argMax` by timestamp, grouped by property gives the latest event per posting and version), with a personal API key scoped to the one project, Query Read only.

## Decision

**Telemetry.** atsfill sends anonymous events to PostHog EU Cloud, with no per-install id. The config key `telemetry` is `off`, `basic` (the default) or `detailed`; `DO_NOT_TRACK=1` forces `off`. Nothing is sent from the test suite or CI, enforced in code, not by convention. The core bounds the flush itself (`flush` with a timeout, low `timeout` and `max_retries`), so telemetry never holds a command past its time budget.

**`basic`.** One event per `read`, `fill` and `send`: the hiring system, the command, the verdict, the status and reason code, for `invalid_answers` each listed field's id and the rule its answer broke, the atsfill version, the posting hash, `source` (`dev` or `user`), and `commit` when `source = dev`, since a checkout reports the last release's version. Nothing the user typed.

- **Posting hash**: a plain hash of the posting's key, the one the double-send guard uses (the URL where no adapter gives one), without its fragment and `utm_` parameters, scheme and host lowercased. A key keeps the rest of its query, which tells postings apart on some systems. Pseudonymous, not anonymous: the maintainer can match a hash to a public posting by hashing a list of postings. The telemetry docs page says so in those words.
- An `unsupported` posting carries the registry source its URL matches, else `unknown`.

**Verdict.** What a run says about its adapter, classified by the core, once:

- `pass`: `ready`, `submitted`.
- `excluded`: the run says nothing about the adapter. Refusals from the user's input (an answer not among the options, a missing answer, the double-send guard, submit not enabled), `out_of_scope`, and every skipped posting of ADR 0013 (a closed posting, an apply step on another system, a page the employer built).
- `fail`: everything else, `blocked` and known gaps included, and `unknown_field`, which can be a field the read missed. The run history answers "does it work for a user today", not "is it the adapter's fault".
- `unsupported`: sent, never drawn.

**`detailed`.** `basic`, plus the posting URL, plus a scrubbed snapshot of the failing step (input values stripped) in a separate `report` event under 900 KB, trimmed to the failing step's subtree when larger. `atsfill report` sends the last failed run at `detailed`, once, whatever the config says.

**Scope.** The registry's verification gives way to a scope: `out_of_scope` (ADR 0014), `in_scope` (wanted, no adapter that passes the adapter gate yet), `supported` (a dev dry run passed, recorded with its `date` and `commit`, never demoted). The registry is a map of sources, not a status record; whether an adapter works today is PostHog's to say.

**Run history.** One docs page, per hiring system: a fill run history and a send run history (`read` is sent, not drawn). One mark per posting and version (a release or a pre-release; a dev run's commit is shown, not counted), at its latest run, coloured by its verdict, `pass` or `fail`, newest first; `excluded` is not drawn. Dev runs hollow, user runs filled. A dev run is drawn only when its commit is on `main`; a tree with uncommitted changes sends none. Each mark shows its version, and its commit for a dev run. The window is the last 30 days; with more users, it becomes a release window (the latest stable and the pre-releases after it). A GitHub Action holding the PostHog read key builds it at each docs site deploy, the daily schedule included, for the docs site and the README. Posting hashes never appear publicly.

**Failure.** A posting hash and command whose latest run within the run history's window is `fail`, until it passes on another build. Failures are grouped by hiring system, command and reason code: one `failure` issue each (versions and count, no hashes), closed with the reason once none is left. A failure whose posting passed before is a regression: the same version on both sides points at the hiring system, a newer one at the code; the issue says so per row. An issue closed by a fix reopens only when a build containing the fix fails the same way, so older builds still in use never reopen a fixed failure. The scheduled Action and a local hourly triage keep the issues in line; the triage then works one issue per run, claiming it with a label (CONTRIBUTING.md, Triage). A dev script resolves the postings behind an issue to URLs, privately.

**Spam.** The run history's generator drops events whose hiring system, command or version it does not know (the registry, PyPI's releases). The run history is best-effort, not proof. Further measures only if spikes appear.

**Privacy.** A docs page says what each level sends, to whom, how to turn it off, and the hash caveat. The README carries one line and the link.

## Consequences

Whether an adapter works is read from runs, its own and users', dated and versioned, instead of from a ladder set by hand. A break shows up without anyone recording it, and a failure issue says, for a regression, whether to look at the hiring system or at the code.

Runs leave the machine by default. The `basic` event holds no answer and no URL, but its posting hash can be matched to a public posting, and anyone with the project key can write events, so the run history is a signal, not a record. Over the free quota, events stop until the next month.
