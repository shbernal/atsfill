# Gupy

Observed on live postings of 13 employers, last on 2026-10-26, with the Test user's account. The fixture page is [tests/pages/gupy/apply.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/gupy/apply.html), served with [forms.json](https://github.com/shbernal/atsfill/blob/main/tests/pages/gupy/forms.json) as an Acme posting in `tests/test_gupy.py`: the register step's screens in turn, with one example of each question type.

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
- `login --create` takes the email and `--details` (a name, a phone, an address) only. The sign-up also asks an identification document number, a mobile number and a date of birth, so the adapter has no sign-up: a user makes the account once in the `login` window. The Test user's was made outside atsfill, its password put in the secret store under `test/gupy`.
- `login_url` is `login.gupy.io/candidates/signin`. `sign_in` fills the email and password, presses Access account and waits to leave the page.
- The signed-in check is `GET private-api.gupy.io/authentication/candidate/account/current` with an `Origin` header: 200 with the account, 401 to a visitor, 500 without the header.

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

## Reading

`browser_read` opens Apply and walks past what comes before Additional information: the contact details dialog (Remind me later), Complete resume, Continue Application, then Continue. It stops at Additional information, or at the questions' introduction ("Answer now") when an earlier visit saved Additional information. The buttons are found by their text, which is English for an English account. Continue Application is disabled while the resume loads; still disabled after `RESUME_WAIT_S`, the resume is incomplete: the `resume` gap.

- Additional information: each radio group is a `choice` named by its input's name (`radioGroupIsIndicatedTitle`, `radioGroupIsCompanyEmployeeTitle`), labelled by its fieldset's legend, the checked option `prefilled` (No by default). The source combobox ("Where did you find this job?", optional) is listed `unhandled_rendering`.
- The questions come from the page's own `question-forms` answer: `question-<questionId>`, `SELECT` a `choice`, `MULTI_SELECT` a `multi_choice`, `TEXT` and `TEXT_AREA` a `text`, `required` as given, a parent question a `shown_when`. Any other type is listed `unhandled_rendering`.

## Filling

- Additional information's radios are clicked by their wrapping label's text. Where the posting has questions, Save and continue saves it (`PUT …/curriculum/<application>/additional-info`), and Answer now opens the questions.
- The questions are on one page: a `SELECT` is radios `question-<id>`, a `MULTI_SELECT` checkboxes `checkbox-<id>-<n>`, each clicked by its label; a `TEXT_AREA` a `textarea` and a `TEXT` an input, each named by the question's title.
- Read-back: Additional information as it was before its save, the questions' checked labels and typed text.
- Submit is the last page's Save and continue: the questions, which "cannot be edited later", or Additional information on a posting without them. No send was made.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `resume` | the account's resume, shared by every application, is not filled: an incomplete one disables Continue Application. The Test user's was completed once, by hand | `unsupported_flow` | none with the Test user's |

The resume as observed: an academic entry is Education level, then Academic qualification and Status (react-aria comboboxes opened with ArrowDown), the institution and course (autocompletes), start and end month and year; a professional one Company, Position, current job, start and end, activities. About you with "Do you live in Brazil?" No asks Country, Address (an autocomplete that also fills State, City and Postal Code), State, City. Each part has its own Save and continue, which releases the next.

Not handled yet, `unhandled`: a `CONDITIONAL` question type; a `question-forms` answer that was not JSON; a button detached from the page as it was clicked.

Not observed yet: what the page shows once the questions are saved, and a posting without questions.
