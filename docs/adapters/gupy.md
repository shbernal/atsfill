# Gupy

Observed on live postings of 11 employers, last on 2026-10-26, with the Test user's account. There is no adapter yet: the round that wrote this page mapped the account and the form and stopped at its turn cap, before an adapter, a fixture page or a dry run. The registry has Gupy `to_read`, mapped.

## Where the form is

- A posting is `<employer>.gupy.io/jobs/<id>`; Apply is `<employer>.gupy.io/candidates/jobs/<id>/apply` (the posting's links add `?jobBoardSource=gupy_public_page`). An employer's career page lives on the same subdomain.
- Apply sends a visitor to `<employer>.gupy.io/candidates/signin`.
- The pages are a React app over a JSON API at `private-api.gupy.io`, which the page calls with the session's cookies.

## Account

Applying needs a signed-in account, one per user for the whole system: the account made on one employer's subdomain signs in on every other. The account scope is None.

- Sign-in: `/candidates/signin`, "Email or Identification Document" and Password, then "Access account". It also offers "Log in without a password", Google and LinkedIn. With the right password it signs in straight away: no code, no captcha seen.
- Sign-up ("Create account", `/candidates/signup`), three screens:
  1. Country of origin (a react-select whose options carry no role, Brazil by default), then a CPF for Brazil or "ID" (an identification document number) for any other country, Email and its confirmation.
  2. Full name, Mobile number with its country code (a react-select of `US +1`-style options), Date of birth (`DD/MM/YYYY`).
  3. Password and its confirmation (at least 8 characters, letters, numbers and one special character), then "Complete registration".
- The account is then unconfirmed: "Check the inbox … and click the link received". The link lands on `/candidates/auth/confirm-email`, "Email verified successfully", without signing in.
- `login --create` takes the email and `--name` only. The sign-up also asks an identification document number, a mobile number and a date of birth, so the Test user's account was made outside atsfill, its password put in the secret store under `test/gupy`. A user makes theirs once, in the `login` window.
- The account endpoint `GET private-api.gupy.io/authentication/candidate/account/current` answers the account (name, email, ID number, mobile number) for a signed-in session.

## The application

Opening Apply signed in creates the application at once (`selection-process/candidate/application`, status `in_process`, `registrationComplete: false`) and lands on `/candidates/applications/<application>/steps/<step>/curriculum`. A first visit shows "Complete resume" before it, and on every visit a dialog may ask to confirm the email and mobile number ("Update" or "Remind me later").

The posting's steps are `selection-process/candidate/application/<application>/step` (`job.jobSteps`). The first is always `register` ("Cadastro"); the application is complete once it is. The steps after it (`online` tests and video, `PRE_INTERVIEW`, `internal`, `offline` interviews) happen after the application, at the employer's invitation. None of the 11 postings had `quickApply` or a profile test step (`hasProfileTestStep`), though the account reports `isProfileTestEnabled`.

The register step has two parts:

1. **My resume**, one per account, shared by every application ("adjustments will be replicated across all your active applications"). `curriculum-management/candidate/curriculum/<candidate>/completion-status` reports `personalInfoCompleted`, `experiencesCompleted`, `formationsCompleted`; "Save and continue" releases the next part only once they are.
2. **The employer's questions**, `question-forms/candidates/applications/<application>/steps/<step>/forms`: `questionForm.questions`, each with `title`, `required`, `type`, `options`, `answer`, and `parentQuestionId` with `conditionalTriggerOptions` for a question shown by another's answer. One posting of 11 had no form.

## Inventory

Each rendering, from the resume (the same on every posting) and the question forms of 10 postings:

| Rendering | Field type | Postings |
|---|---|---|
| resume: an optional file to auto-fill the resume from (DOCX or PDF) | `file` | 11 |
| academic experience, a list entry: Education level (a combobox, `educationLevel`), then the fields that level asks | `choice`, then by level | 11 |
| professional experience, a list entry: Company, Position, "This is my current job", Start and End as a month and a year combobox each, Main activities | `text`, `choice` `Yes`/`No`, `text` `MM/YYYY`, `text` | 11 |
| Language, Achievements or certifications: optional list entries | not read | 11 |
| About you: gender and disability radios, "Address in Brazil" Yes/No, a ZIP code for Yes, LinkedIn URL | `choice`, `text` | 11 |
| Diversity: pronoun, gender identity, sexual orientation, race radios and a consent box, optional | `choice` | 11 |
| Skills: an autocomplete of up to 30 | not read | 11 |
| a question `SELECT` | `choice` | 9 |
| a question `TEXT_AREA` | `text` | 6 |
| a question `TEXT` | `text` | 3 |
| a question `MULTI_SELECT` | `multi_choice` | 2 |

No question had a `parentQuestionId` on these postings.

## Known gaps

| Gap | What | Postings |
|---|---|---|
| `sign_up_fields` | the sign-up asks an identification document number, a mobile number and a date of birth, which `login --create` does not take | every one |

Not observed yet: the "Address in Brazil: No" path, the fields each education level asks, the question page's rendering, what Continue Application shows after the questions, and its submit.
