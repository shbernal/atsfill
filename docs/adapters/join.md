# JOIN

Observed on live postings on 2026-10-26, headless Camoufox, the account profile on 2026-10-28. The fixture pages are [tests/pages/join/job.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/join/job.html), a posting and its wizard served signed in by `tests/test_join.py`, [tests/pages/join/account.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/join/account.html), the sign-in and sign-up, and [tests/pages/join/profile.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/join/profile.html), the account profile's form.

## Where the form is

- A posting is `join.com/companies/<company>/<job id>-<slug>`; the job id alone keys it. The page is server-rendered with the job in `__NEXT_DATA__` (`pageData.job`: `id`, `companyId`, `status`).
- Apply now (`[data-testid=ApplyButton]`, two in the page, one visible) opens a wizard on `/companies/<company>/<job id>/apply/<step>`, one step per page, each with Back and Continue: `authentication`, `cv`, `personalInformation`, `professionalLinks`, `coverLetter`, `question-<position>` per screening question, `review`. Continue stays disabled until the step's required answer is given.
- `review` lists every answer, each with Edit, and ends with Confirm & apply (`button[type=submit]`), the send. Nothing reaches the employer before it; the CV goes to storage on upload (`GenerateJobApplicationAttachmentUploadInfo`, then a signed Google Cloud Storage URL).
- The page loads an invisible reCAPTCHA; headless Camoufox passed every step.
- Plain HTTP clients are served too: `curl` read the posting and the screening questions, Python's `urllib` was answered 403, and after about a dozen postings `curl` stopped getting the page's data.

## Account

One account per user for the whole system, a JOIN candidate account.

- Signed out, the wizard's first step asks the email (`#email`) and calls `CheckCandidate` (`candidate-api/graphql`), which answers `exists` and `hasPassword`. An unknown email goes straight to `cv`: the form can be read and filled without an account. A known one is asked to sign in, so a user's second application needs the account.
- Sign-up: `join.com/talent/home` redirects to `/auth/login/candidate`; an unknown email asks first name, last name and a password, posted as `RegisterCandidate` then `LoginWithCredentials`: signed in. A confirmation email ("Verify your account") follows with a `/auth/candidates/verify-account?loginToken=…` link.
- Until the link is clicked every sign-in lands on `/auth/candidates/unverified-email`, and the posting's Apply button does not open the wizard. Each such sign-in mails a new link and voids the previous one, so only the latest works.
- Sign-in: the email, then a password (`#password`) with Sign in (`[data-testid=SubmitButton]`), or "Get a login link" by email. `LoginWithCredentials` answers `Empty` on success.
- The signed-in check is `GET join.com/api/me`: the candidate (`type: candidate`, `confirmedAt` once verified).
- Signed in, the wizard opens on `authentication` and leaves it for `cv` a moment later. It skips `personalInformation` and `professionalLinks`, taking the name from the account: the steps are `cv`, `coverLetter`, the questions, `review`.
- atsfill signs in (`login join`) and creates the account (`login join --create --email <address> --details <file>` with a first and a last `name`, which ends `blocked: email_link`); with a password source, a form command signs in again on its own.

## Account profile

What the wizard takes from the account is its profile, kept at `join.com/talent/me`: general information, then experience, education, certifications, projects, languages and skills, each section edited on its own. The general information is a form at `/talent/me?edit=profile`, read and saved by `atsfill profile join` (ADR 0017) as one profile step, `general`. A job that requires a detail it lacks shows `personalInformation` in the wizard (see below). The page is in the account's language (Paramètres, Langue de l'application), the country names too.

| Field id | Field type | Rendering |
|---|---|---|
| `first_name`, `last_name` | `text`, required, 30 characters | input |
| `phone` | `phone`, answered `+<code> <number>` | a country code button opening a popover of countries ("France (+33)", Germany and Switzerland first), then a tel input that keeps digits only; `data-value` holds the code and the number |
| `country` | `choice`, required | react-select, preset from the visitor's location; its menu lists every country |
| `city` | `typeahead` | react-select, suggestions from `GetPlacesAutocomplete` within the country of residence |
| `linkedin` | `url` | input |

- atsfill picks the code the answer starts with, the longest of those listed, so `+1 684…` is American Samoa and `+1 212…` the first `+1`.
- The photo, the bio, the work authorizations, the other links and the other sections are not listed and stay as they are.
- Save changes (`button[type=submit]`) posts `UpdateTalentPersonalInformation` and returns to `/talent/me`. It stays disabled until a field changes, and a city changed alone does not count: `refused: step_refused: general`, saved only along with another change. A saved phone cannot be emptied: the mutation answers "Phone number is invalid".

## Reading and filling

The screening questions come from `JobScreeningQuestions` (`candidate-api/graphql`, `companyId` and `jobId` from the posting page), callable without a session: each question's `id`, `question`, `type`, `options`, `isRequired`, `position` and `questionClass`. On `question-<position>` the answer's input is named `screeningQuestions.answer-<id>`.

| Step | Field type | Rendering | Postings |
|---|---|---|---|
| `cv` | `file` | PDF upload, then "Confirm your CV" with Remove file | 1 walked |
| `personalInformation` | `text`, `choice`, `typeahead`, `phone`, `file` | first and last name (required), country of residence (a select, preset from the visitor's location), city (async select), phone with a country code, profile picture (PNG or JPEG) | 1 walked |
| `professionalLinks` | `text` | LinkedIn URL, Add link for more | 1 walked |
| `coverLetter` | `file` | PDF upload | 1 walked |
| `SINGLE_CHOICE` | `choice` | `div[role=radio]` cards lettered A, B, …: plain, `AVAILABILITY`, `WORK_AUTHORIZATION` | 2, 7, 5 |
| `INTEGER` | `number` | `input[type=number]`: plain, `SALARY_EXPECTATION` | 3, 7 |
| `BOOLEAN` | `choice` | Yes and No cards, as a single choice | 4 |
| `DATE` | `date` | an inline calendar, days `td[data-part=table-cell][data-value=YYYY-MM-DD]`, month and year selects | 4 |
| `TEXT` | `text` | text input | 1 |

The question counts are postings, from 11 employers whose posting asks screening questions, read by request. Which steps a posting asks was not found in the page's data: the wizard's steps are the source.

- atsfill lists the CV (required) and the questions; the account's name, links and contact details are the account profile's, not the form's. A question of another type is listed `unhandled_rendering`.
- The fill walks the wizard from `cv` to `review`, answering each step, and stops there; a step it does not know stops it as `unhandled_rendering`. Continue is the footer's last button (its label is in the job's language); one left disabled means a required answer is missing.
- A question of a class has a step named for it (`workAuthorization`, `salaryExpectation`); the others are `question-<position>`. A job asking only plain questions positions them from 1; one that also asks classed questions keeps positions 1 to 3 for the classes (work authorization, availability, salary) and positions its plain ones from 1001, so its steps are `question-1001`, `question-1002`, …, rendered as any other. A work authorization's cards hold the option and a description; a salary is `#salaryExpectationAmount`.
- `personalInformation` (photo, name, country, city, phone) shows when the job requires what the account profile lacks, a phone on 13 of 45 sample postings. Continue is then disabled, or enabled and going nowhere. atsfill leaves the step as it is: the form command ends `blocked: profile_incomplete: the job asks for a phone`, naming `atsfill profile join`. With a phone in the profile, the same jobs pass the step.
- A file is stored on upload, before Continue: the step then shows the file in place of its input.
- A choice's cards are its options in order, each a letter, the option and, for a work authorization, a description; a boolean's read in the job's language (`Ja`, `Nein`) and are answered `Yes`, `No` by position.
- `coverLetter` is asked by some jobs only, optional where seen: it is left without one, and not listed.
- The calendar is paged with its previous and next buttons to the answer's month.
- The read-back is each question as its step was left (the checked card, the picked day, the input), and the CV by name from the review page: the review shows a classed question under a label of its own and its answer reworded (`€50,000`, an availability in the account's language).

## Submitting

Confirm & apply (`button[type=submit]` on `review`) is the send; the evidence is the GraphQL request it makes. Not sent yet: which operation it is, and its answer, are unobserved.
