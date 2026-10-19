# Hiring systems

Generated from [src/atsfill/registry.toml](../../src/atsfill/registry.toml) by `uv run scripts/backlog.py`. The registry is described in [ADR 0012](../adr/0012-systems-registry.md), the order in [ADR 0013](../adr/0013-survey-and-adapter-rounds.md): no account first, blocked last, then by share of postings.

| Source | Kind | Share | Account | Hosting | Block | State |
|---|---|---|---|---|---|---|
| ashby | ats |  | none | hosted, embedded |  | [send_verified](ashby.md) |
| greenhouse | ats |  | none | hosted, embedded |  | [dry_run_verified](greenhouse.md) |
| teamtailor | ats |  | none | hosted |  | [dry_run_verified](teamtailor.md) |
| wttj | board |  | to_send | hosted |  | [adapter](wttj.md) |
