# HRMDirect

HRMDirect, now ClearCompany, observed on live postings on 2026-10-26. The fixture page is [tests/pages/hrmdirect/apply.html](../../tests/pages/hrmdirect/apply.html), a two-page wizard served as an Acme org in `tests/test_hrmdirect.py`. Unsupported (see the registry): the known gaps below stop sign-up on 6 of 17 orgs, and the gate's 5 dry runs were not reached.

## Where the form is

- freehire's postings are `<site>.hrmdirect.com/employment/job-opening.php?req=<n>`, which redirects to the career site's job page, `apply.clearcompany.com/<site>?jobId=<job id>`, a widget (`careers-content.clearcompany.com/js/v1/career-site.js`). All 16 sites tried redirected.
- The job page's APPLY link is `https://<org>.clearcompany.com/careers/jobs/<job id>/apply?source=…`. The org is the site's own name on most sites, another org on some (a site whose parent company hosts its applications). The double-send key is the job id, which both hosts share.

## Account

Applying needs an account, one per org: the apply page's first step (`#personal-info-form`) asks first and last name, email twice, a mobile phone (optional on 16 of 17 orgs), an SMS consent and, on most orgs, "Would you like to be considered for other positions" (required); atsfill declines both questions. Continue (`POST /api/v1/careers/apply/user/create`) creates the candidate and opens the wizard, signed in. It sets no password: an email follows with a sign-in link and a link to set a password (`/careers/password/<id>/reset/<auth>`, two password inputs, Set New Password), both expiring in 15 minutes. `login --create` therefore ends `blocked: email_link`: the set-password link takes the generated password, and `login` then signs in with it.

- An email that already has an account at the org switches the step to sign-in ("Sign in with username and password instead"): `refused: email_in_use`.
- Signing in is by an emailed sign-in link (Email, NEXT) or by the password (Username, Password, Continue): from the sign-up step, I Already Have An Account, then "Sign in with username and password instead"; from a wizard page, the header's login link opens a modal. "Incorrect username or password" is `refused: wrong_password`; on 2 of 7 orgs, a password the set-password page reported as updated was refused so.
- The session is the `HrmBase-careers` cookie on the org's host, whose `Token` the API takes as `API-Token` with `API-Realm: CWEB`, `API-AppType: careers` and `API-ShortName: <org>`; `GET /api/v1/users/auth/authenticate` answers 200 while signed in, 401 after. The token lasts 180 minutes (`SessionLength`), the cookie 20 days.
- The sign-up step comes after the form's first page on 4 of 14 orgs (`screening_first`); on 1 of 17 it requires the mobile phone (`sign_up_phone`); on 1 of 17 a reCAPTCHA box keeps its Continue disabled, `blocked: captcha`.

## Reading the form

The wizard is pages (`.form-progress-holder li`, "Page 1", "Page 2", "Page 3" on the first posting), each saved as Continue leaves it; only the current page is in the DOM, and a page with a required field empty shows `.has-error` and does not move. Each field is a `.form-field` with a `qa-<kind>-field` class and a `.field-title`, a `.required-indicator` marking it required:

| Rendering | Field type | Postings |
|---|---|---|
| text input or `textarea` (`qa-text-field`), its `data-max` the length | `text` | 2 |
| `select` (`qa-dropdown-field`): state, country | `choice` | 1 |
| radio group (`qa-radio-field`), each input's `data-field` the field's id | `choice` | 1 |
| checkboxes (`qa-checkboxexplain-field`), an option's follow-up in `.child-field-holder-<n>` | `multi_choice` | 1 |
| date input (`.date-input`, `MM/DD/YYYY`, a datepicker) | `date` | 1 |
| a repeatable section (`.section-repeatable`, Work Experience), Add Additional Row | its first row's fields | 1 |
| upload (`qa-resume-upload resume`, `appcoverletter`): a `file-upload` input in a menu with Dropbox and Google Drive | `file` | 1 |
| LinkedIn profile (`qa-resume-upload applinkedin`), the resume's alternative | out of scope | 1 |

Reading walks the pages from the first, pressing Continue as long as a page saves with what it holds: a fresh application stops on the first page, its resume empty. A fill saves the pages it can and is refused (`answer_not_taken: … read again`) on a page showing required fields the read did not list; the next read walks the saved pages. A survey therefore dry-runs a posting twice.

## Filling

- Text is filled and the field left; a select is picked by its option's text, and picked again when a later field (the country) renders its list again; a country other than the form's turns the state into a text input, then typed.
- A radio or a box is clicked through its label; one box is a `Yes`/`No` choice.
- A date is typed `MM/DD/YYYY`; its datepicker stays open over the fields below it and is hidden.
- An upload is set on the menu's file input and waited for until its row shows the file's name without "Uploading…".

## Submit

The last page's primary button is Submit, not Continue (`data-localized-key`); no send was made.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `screening_first` | the form's first page comes before the sign-up step | `unsupported_flow` | 4 of 14 orgs |
| `sign_up_phone` | the sign-up step requires a mobile phone, which `login` does not take | `unsupported_flow` | 1 of 17 orgs |
