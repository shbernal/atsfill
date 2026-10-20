# Hiring systems

Generated from [src/atsfill/registry.toml](../../src/atsfill/registry.toml) by `uv run scripts/backlog.py`. The registry is described in [ADR 0012](../adr/0012-systems-registry.md), the order in [ADR 0013](../adr/0013-survey-and-adapter-rounds.md): no account first, blocked last, then by share of postings.

The registry covers 90.1% of postings.

| Source | Kind | Share | Account | Hosting | Block | State |
|---|---|---|---|---|---|---|
| oracle | ats | 8.3% | none | hosted |  | [dry_run_verified](oracle.md) |
| smartrecruiters | ats | 4.4% | none | hosted |  | [dry_run_verified](smartrecruiters.md) |
| greenhouse | ats | 3.5% | none | hosted, embedded |  | [dry_run_verified](greenhouse.md) |
| paycom | ats | 2.4% | none | hosted |  | mapped |
| manatal | ats | 1.9% | none | hosted |  | mapped |
| paylocity | ats | 1.7% | none | hosted |  | mapped |
| dayforce | ats | 1.7% | none | hosted |  | [mapped](dayforce.md) |
| workable | ats | 1.6% | none | hosted |  | mapped |
| ashby | ats | 1.5% | none | hosted, embedded |  | [send_verified](ashby.md) |
| bamboohr | ats | 1.3% | none | hosted |  | mapped |
| teamtailor | ats | 1.2% | none | hosted |  | [send_verified](teamtailor.md) |
| jazzhr | ats | 1.2% | none | hosted |  | mapped |
| lever | ats | 1.2% | none | hosted |  | mapped |
| hireology | ats | 1.0% | none | hosted |  | mapped |
| paycor | ats | 0.9% | none | hosted, embedded |  | mapped |
| zohorecruit | ats | 0.9% | none | hosted |  | mapped |
| ukgready | ats | 0.9% | none | hosted |  | mapped |
| phenom | ats | 0.7% | none | hosted |  | mapped |
| breezy | ats | 0.7% | none | hosted |  | mapped |
| recruitee | ats | 0.7% | none | hosted |  | mapped |
| applicantpro | ats | 0.6% | none | hosted |  | mapped |
| rippling | ats | 0.5% | none | hosted |  | mapped |
| isolvedhire | ats | 0.5% | none | hosted |  | mapped |
| personio | ats | 0.4% | none | hosted |  | mapped |
| eightfold | ats | 0.4% | none | hosted |  | mapped |
| pinpoint | ats | 0.4% | none | hosted |  | mapped |
| hiringthing | ats | 0.3% | none | hosted |  | mapped |
| wttj | board | 0.1% | to_send | hosted |  | [send_verified](wttj.md) |
| workday | ats | 17.9% | to_read | hosted |  | mapped |
| successfactors | ats | 3.5% | to_read | hosted |  | mapped |
| ukg | ats | 3.5% | to_read | hosted |  | mapped |
| icims | ats | 1.9% | to_read | hosted |  | mapped |
| mycareersfuture | board | 1.6% | to_read | hosted |  | mapped |
| taleo | ats | 1.2% | to_read | hosted |  | mapped |
| adp | ats | 1.1% | to_read | hosted |  | mapped |
| adpmyjobs | ats | 0.8% | to_read | hosted |  | mapped |
| hrmdirect | ats | 0.6% | to_read | hosted |  | mapped |
| jobstreet | board | 0.5% | to_read | hosted |  | mapped |
| gupy | ats | 0.4% | to_read | hosted |  | mapped |
| join | ats | 0.3% | to_read | hosted |  | mapped |
| trudvsem | board | 10.1% | to_read | hosted | unknown at posting | mapped |

Aggregators and company-built forms, counted and not adapted:

| Source | Kind | Share | Account | Hosting | Block | State |
|---|---|---|---|---|---|---|
| adzuna | aggregator | 1.3% | none |  |  | mapped |
| eures | aggregator | 1.0% | none |  |  | mapped |
| whatjobs-uk | aggregator | 0.4% | none |  |  | mapped |
| startupjobs | aggregator → smartrecruiters, recruitee | 0.4% | none |  |  | mapped |
| radancy | aggregator → icims, workday | 0.3% | none |  |  | mapped |
| 4dayweek | aggregator → crelate | 0.3% | none |  |  | mapped |
| jibe | aggregator → icims | 0.3% | none |  |  | mapped |
| telegram | aggregator | 0.3% | none |  |  | mapped |
| whatjobs-es | aggregator | 0.2% | none |  |  | mapped |
| energyjobline | aggregator | 1.6% | to_read | hosted |  | mapped |
