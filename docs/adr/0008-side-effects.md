# 0008. Side effects

## Context

A dry run is not invisible. Ashby saves each field to its servers as it is typed, and Teamtailor stores the resume when it is uploaded, before anyone clicks submit.

## Decision

Each adapter declares its side effects:

```
{ step: "load" | "fill" | "upload" | "submit" | "sign_up",
  reaches: "hiring_system" | "employer",
  effect: "each field change is saved to Ashby as typed" }
```

They are shown before anything is filled: `status` lists them for every adapter, and `read` returns them for the system it found. The home's owner, having read them, mutes them per adapter and per user in the config file (ADR 0006), `mute-side-effect-warnings = ["ashby", "teamtailor"]` or `["all"]`. `read` then leaves them out for those adapters, and `status` still lists them, marked muted. An adapter added later warns until it is muted.

Creating an account (`login --create`, ADR 0015) is a side effect too, and never part of `fill` or `send`.
