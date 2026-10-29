# Hiring systems

Generated from [src/atsfill/registry.toml](https://github.com/shbernal/atsfill/blob/main/src/atsfill/registry.toml) by `uv run python -m scripts.backlog`. The registry is described in [ADR 0012](../adr/0012-systems-registry.md), the order in [ADR 0013](../adr/0013-survey-and-adapter-rounds.md): no account first, blocked and stopped by a known gap last, then by share of postings.

The registry covers 90.3% of postings.

| Source | Kind | Share | Account | Hosting | Block | Scope |
|---|---|---|---|---|---|---|
| oracle | ats | 8.3% | none | hosted |  | [supported](oracle.md) |
| smartrecruiters | ats | 4.4% | none | hosted |  | [supported](smartrecruiters.md) |
| greenhouse | ats | 3.5% | none | hosted, embedded |  | [supported](greenhouse.md) |
| manatal | ats | 1.9% | none | hosted |  | [supported](manatal.md) |
| paylocity | ats | 1.7% | none | hosted |  | [supported](paylocity.md) |
| dayforce | ats | 1.7% | none | hosted |  | [supported](dayforce.md) |
| workable | ats | 1.6% | none | hosted |  | [supported](workable.md) |
| ashby | ats | 1.5% | none | hosted, embedded |  | [supported](ashby.md) |
| bamboohr | ats | 1.3% | none | hosted |  | [supported](bamboohr.md) |
| teamtailor | ats | 1.2% | none | hosted |  | [supported](teamtailor.md) |
| jazzhr | ats | 1.2% | none | hosted |  | [supported](jazzhr.md) |
| lever | ats | 1.2% | none | hosted |  | [supported](lever.md) |
| hireology | ats | 1.0% | none | hosted |  | [supported](hireology.md) |
| paycor | ats | 0.9% | none | hosted, embedded |  | [supported](paycor.md) |
| zohorecruit | ats | 0.9% | none | hosted |  | [supported](zohorecruit.md) |
| phenom | ats | 0.7% | none | hosted |  | [supported](phenom.md) |
| breezy | ats | 0.7% | none | hosted |  | [supported](breezy.md) |
| recruitee | ats | 0.7% | none | hosted |  | [supported](recruitee.md) |
| applicantpro | ats | 0.6% | none | hosted |  | in scope |
| rippling | ats | 0.5% | none | hosted |  | [supported](rippling.md) |
| personio | ats | 0.4% | none | hosted |  | [supported](personio.md) |
| eightfold | ats | 0.4% | none | hosted |  | [supported](eightfold.md) |
| pinpoint | ats | 0.4% | none | hosted |  | [supported](pinpoint.md) |
| hiringthing | ats | 0.3% | none | hosted |  | [supported](hiringthing.md) |
| recruiterflow | ats | 0.1% | none | hosted |  | [supported](recruiterflow.md) |
| jobaffinity | ats |  | none | hosted |  | [supported](jobaffinity.md) |
| welcomekit | ats |  | none | hosted |  | [supported](welcomekit.md) |
| join | ats | 0.3% | to_send | hosted |  | [supported](join.md) |
| workatastartup | board | 0.1% | to_send | hosted |  | [supported](workatastartup.md) |
| wttj | board | 0.1% | to_send | hosted |  | [supported](wttj.md) |
| workday | ats | 17.9% | to_read | hosted |  | [supported](workday.md) |
| successfactors | ats | 3.5% | to_read | hosted |  | [supported](successfactors.md) |
| adpmyjobs | ats | 0.8% | to_read | hosted |  | [supported](adpmyjobs.md) |
| isolvedhire | ats | 0.5% | to_read | hosted |  | [supported](isolvedhire.md) |
| jobstreet | board | 0.5% | to_read | hosted |  | [supported](jobstreet.md) |
| gupy | ats | 0.4% | to_read | hosted |  | [supported](gupy.md) |
| paycom | ats | 2.4% | none | hosted | hCaptcha at form | in scope |
| trudvsem | board | 10.1% | to_read | hosted | unknown at posting | in scope |
| ukg | ats | 3.5% | to_read | hosted |  | [in scope: unhandled on the forms the sign-up now reaches, 3 of 8 tenants ready: no gender decline box to tick (3), a State whose condition names no Country (1), a numeric answer read back differently (1)](ukg.md) |
| icims | ats | 1.9% | to_read | hosted | hCaptcha at form | in scope |
| mycareersfuture | board | 1.6% | to_read | hosted |  | in scope: unsupported_flow: national_id_login: applying needs a Singpass sign-in, whose account takes a Singapore NRIC or FIN number; no account can be made for the Test user (7 of 7 employers) |
| taleo | ats | 1.2% | to_read | hosted |  | in scope: unsupported_rendering: location_levels: a place's state and city list their options only once the level above is picked, so a fresh profile's are not listed (3 of 4 zones walked) |
| adp | ats | 1.1% | to_read | hosted |  | in scope: unsupported_flow: sign_up_phone: the talent community step before the form requires a mobile number, even when its OTP goes to the email (14 of 15 employers) |
| ukgready | ats | 0.9% | to_read | hosted |  | in scope: unsupported_flow: profile_wizard: signed in, the form is the applicant portal's profile wizard, items of a dozen types edited in cards, entry lists, a security question and an electronic signature; not built within the round (1 tenant walked) |
| hrmdirect | ats | 0.6% | to_read | hosted |  | in scope: unsupported_flow: screening_first: the form's first page comes before the sign-up step (4 of 14 orgs); a reCAPTCHA box on the sign-up step (1 of 17); 3 orgs signed in, under the 5 dry runs the gate needs |

Aggregators, employer-built forms and out-of-scope systems ([ADR 0014](../adr/0014-out-of-scope.md)), counted and not adapted:

| Source | Kind | Share | Account | Hosting | Block | Scope |
|---|---|---|---|---|---|---|
| adzuna | aggregator | 1.3% | none |  |  | in scope |
| eures | aggregator | 1.0% | none |  |  | in scope |
| whatjobs-uk | aggregator | 0.4% | none |  |  | in scope |
| startupjobs | aggregator → smartrecruiters, recruitee | 0.4% | none |  |  | in scope |
| radancy | aggregator → icims, workday | 0.3% | none |  |  | in scope |
| 4dayweek | aggregator → crelate | 0.3% | none |  |  | in scope |
| jibe | aggregator → icims | 0.3% | none |  |  | in scope |
| telegram | aggregator | 0.3% | none |  |  | in scope |
| whatjobs-es | aggregator | 0.2% | none |  |  | in scope |
| energyjobline | aggregator | 1.6% | to_read | hosted |  | in scope |
