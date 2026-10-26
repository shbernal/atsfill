# JOIN

Observed on live postings on 2026-10-26, headless Camoufox. No adapter yet: the observations below are the first round's, the field inventory taken by request.

## Where the form is

- A posting is `join.com/companies/<company>/<job id>-<slug>`; the job id alone keys it. The page is server-rendered with the job in `__NEXT_DATA__` (`pageData.job`: `id`, `companyId`, `status`).
- Apply now (`[data-testid=ApplyButton]`, two in the page, one visible) opens a wizard on `/companies/<company>/<job id>/apply/<step>`, one step per page, each with Back and Continue: `authentication`, `cv`, `personalInformation`, `professionalLinks`, `coverLetter`, `question-<n>` per screening question, `review`. Continue stays disabled until the step's required answer is given.
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
- Signed in, Apply skips `authentication` and opens `cv`, with the account's name on `personalInformation`.

## Reading and filling

The screening questions come from `JobScreeningQuestions` (`candidate-api/graphql`, `companyId` and `jobId` from the posting page), callable without a session: each question's `id`, `question`, `type`, `options`, `isRequired` and `questionClass`. On `question-<n>` the answer's input is named `screeningQuestions.answer-<id>`.

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

The question counts are postings, from 11 employers whose posting asks screening questions, read by request; one posting was walked to `review`. Which of the fixed steps a posting asks, and which are required, was not found in the page's data: the wizard's steps are the source.
