# 0017. Account profiles

## Context

Some hiring systems keep an account profile that applying depends on. Work at a Startup refuses to apply until its profile, a wizard of seven steps, is complete and visible to employers. Join shows `personalInformation` when the profile lacks a phone (the known gap `profile_incomplete`). Welcome to the Jungle sends a new account to an onboarding that prefills its forms but is optional. UKG Ready's application is a profile wizard. `login --create --details` cannot carry any of it: sign-up details are a fixed name, phone and address, while a profile has work history, skills and the system's own questions.

## Decision

**A profile form.** An account profile is edited through a form of its own, the profile form, in one or more profile steps: fields, answers, read-back and checks as on an application form, matched by the same core rules (ADR 0005). The adapter knows each step's page: its fields, its fill, its read-back and its save control. The core walks the steps.

**`atsfill profile <adapter> [--user u] [--url posting]`** reads the profile form, listing each step's fields with the saved values as `prefilled`. `--url` means what it means for `login`: needed only where the account scope depends on the employer. A read never saves: where a step loads only once the earlier ones are saved, it lists the saved steps and the first incomplete one, and names the rest. Filling and reading again reaches the end.

**`--answers <file>` saves it**, step by step: fill, read-back and, when every check matches, save and go to the next step. A step whose read-back already matches is not saved again, so a run on an unchanged profile changes nothing; the result lists the steps it saved. A mismatch stops with the failed checks, a `time_budget` block stops where it is, and the steps already saved stay saved either way: running again picks up from there. It ends `ready` once every step is saved and matches, whatever the profile's visibility. No new status.

**Not an application.** `profile` is never part of `fill` or `send`, like account creation (ADR 0015). Saving is a side effect on the hiring system that the adapter declares (step `profile`, ADR 0008). No `submit-allowed` and no double-send guard: running it again updates the profile.

**Answers.** The consumer's file, one per user and hiring system, like a form's: one flat map by field id, ids unique across the profile's steps. Which step a field is on is the adapter's to know. atsfill keeps none of it.

**Form commands.** A form command that meets an incomplete profile, or one the hiring system will not apply from (Work at a Startup's hidden profile), ends `blocked: profile_incomplete: <detail>`, naming the `profile` command to run, as `login_required` names `login`. One code for both, since the fix is the same command. Its verdict is `fail`, as `login_required`'s is: the run history answers whether atsfill works for a user today.

**Telemetry** (ADR 0016). `profile` sends one event at `basic`: command `profile`, the verdict, the status and reason code, with no posting hash since it has no posting. The run history does not draw it, as it does not draw `read`.

## Consequences

A hiring system that gates applying on its profile is served without a person in a window, and the profile's answers are checked like a form's. A profile saved for one posting serves every later one, so a profile that changes between sends changes what employers see on earlier applications too: it is the user's to keep, not atsfill's.
