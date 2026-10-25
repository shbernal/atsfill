# 0014. Out of scope: what needs the user live

## Context

atsfill aims to cover as many forms as it can while staying close to the standard application form. Some forms ask for what stored answers can never give, others for what an adapter does not handle yet, and others hold something nobody has looked at. Without a line between them, a posting atsfill gives up on for good looks the same as one it will fill next release, and an adapter's missing rendering looks the same as a surprise.

## Decision

**The test.** A field is out of scope when its answer cannot come from stored answers and files, because it needs the user present for the content of the answer: a video recorded in a live recorder, a timed or proctored assessment. The line is drawn narrow on purpose, and the postings that ask for such a field are given up.

In scope, even where rare: a pre-recorded video given as a file, an untimed written assessment (text), a video link (text), booking an interview slot, an OTP (plumbing, not the content of an answer).

**Three cases.** A form atsfill cannot fill falls in one of three, each telling at run time:

| Case | What it is | Recorded | At run time |
|---|---|---|---|
| Out of scope | known, never pursued | this ADR, and one line per adapter doc | `unsupported`, `out_of_scope: <field id> is <what>` |
| Known gap | known, wanted | declared by the adapter, listed under "Known gaps" in its doc | `unsupported_rendering: <gap id>: …`, `unsupported_flow: <gap id>: …` |
| Unhandled | not yet observed | nowhere; the survey surfaces it | `unhandled_rendering: …`, `adapter_error: …` |

`unsupported_rendering` and `unsupported_flow` only ever name a known gap: the adapter raises them on purpose, with the gap's id. A rendering that falls through the adapter's cases is `unhandled_rendering`.

`posting_gone`, `external_step` and `not_qualified` are facts about the posting, outside the three cases.

**Optional fields.** An optional field atsfill cannot fill, out of scope or a known gap, is left empty and reported; only a required one stops the form. `browser_read` returns the field with its case attached, the core applies the rule, and `read` shows it before any fill.

**Registry.** A whole source can be out of scope (`out_of_scope = { reason, date }`, ADR 0012), a video-first system for instance: it leaves the backlog's main table for the side table. An `unsupported` entry stays wanted work and names its known gap.

**Adapter gate.** It skips an `out_of_scope` posting as it skips a closed posting or an external step (ADR 0013). A known gap or an unhandled case is not ready.

## Consequences

A consumer can tell a posting atsfill will never fill from one it may fill later, and a known gap from an unhandled case. Every known gap has a doc entry a reader finds. The postings that need the user live are lost for good, unless this test changes.
