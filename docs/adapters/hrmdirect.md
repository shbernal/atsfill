# HRMDirect

HRMDirect, now ClearCompany, observed on live postings on 2026-10-26. The adapter is unfinished: no fixture page yet, no dry run `ready`.

## Where the form is

- freehire's postings are `<site>.hrmdirect.com/employment/job-opening.php?req=<n>`, which redirects to the career site's job page, `apply.clearcompany.com/<site>?jobId=<job id>`, a widget (`careers-content.clearcompany.com/js/v1/career-site.js`). All 16 sites tried redirected.
- The job page's APPLY link is `https://<org>.clearcompany.com/careers/jobs/<job id>/apply?source=…`. The org is the site's own name on most sites, another org on some (a site whose parent company hosts its applications). The double-send key is the job id, which both hosts share.

## Account

Applying needs an account, one per org: the apply page's first step (`#personal-info-form`) asks first and last name, email twice, an optional mobile phone and an SMS consent, and Continue (`POST /api/v1/careers/apply/user/create`) creates the candidate and opens the wizard, signed in. It sets no password: an email follows with a sign-in link and a link to set a password, both expiring in 15 minutes.

- An email that already has an account at the org switches the step to sign-in ("Sign in with username and password instead"): `refused: email_in_use`.
- Signing in again is by an emailed sign-in link (Email, NEXT) or by a password set through the emailed link (Username, Password). A password atsfill generates is never set on the system, so once the session ends, signing in needs the user's own password through a `password-commands` entry, or `login --window`.
- The session is the `HrmBase-careers` cookie on the org's host, whose `Token` the API takes as `API-Token` with `API-Realm: CWEB`, `API-AppType: careers` and `API-ShortName: <org>`; `GET /api/v1/users/auth/authenticate` answers 200 while signed in, 401 after. The token lasts 180 minutes (`SessionLength`), the cookie 20 days.
- On 2 of 4 sites tried, the sign-up step rendered differently from the one above (no visible `#personal-info-form`, or its Continue not found); not inventoried yet.

## Reading the form

The wizard is pages (`.form-progress-holder li`, "Page 1", "Page 2", "Page 3" on the first posting), each saved as Continue leaves it; only the current page is in the DOM, and a page with a required field empty shows `.has-error` and does not move. Each field is a `.form-field` with a `qa-<kind>-field` class and a `.field-title`, a `.required-indicator` marking it required:

| Rendering | Field type | Postings |
|---|---|---|
| text input (`qa-text-field`), its `data-max` the length | `text` | 1 |
| `select` (`qa-dropdown-field`): state, country | `choice` | 1 |
| radio group (`qa-radio-field`), each input's `data-field` the field's id | `choice` | 1 |
| checkboxes (`qa-checkboxexplain-field`), an option's follow-up in `.child-field-holder-<n>` | `multi_choice` | 1 |
| date input (`.date-input`, `MM/DD/YYYY`, a datepicker) | `date` | 1 |
| a repeatable section (`.section-repeatable`, Work Experience), Add Additional Row | its first row's fields | 1 |
| upload (`qa-resume-upload resume`, `appcoverletter`): a `file-upload` input in a menu with Dropbox and Google Drive | `file` | 1 |
| LinkedIn profile (`qa-resume-upload applinkedin`), the resume's alternative | out of scope | 1 |

## Submit

The last page's primary button is Submit, not Continue (`data-localized-key`); no send was made.

## Known gaps

None declared yet.
