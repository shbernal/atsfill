# isolved Hire

isolved Applicant Tracking's career sites, observed on live postings on 2026-10-26, headless, with the Test user's account. The fixture pages are [tests/pages/isolvedhire/](../../tests/pages/isolvedhire/), served as an Acme site by `tests/test_isolvedhire.py`: the sign-in and sign-up ([account.html](../../tests/pages/isolvedhire/account.html), [sign-up.html](../../tests/pages/isolvedhire/sign-up.html)), the wizard ([apply.html](../../tests/pages/isolvedhire/apply.html)) and its steps as `/apply/view.php` serves them ([steps.html](../../tests/pages/isolvedhire/steps.html)), one example of each rendering.

## Where the form is

- A posting is `https://<employer>.isolvedhire.com/jobs/<id>`, one subdomain per employer.
- The posting page holds an apply step in place, under Apply now: "How would you like to verify your identity?", Continue with phone (a text code) or Continue with email, then a six-digit code mailed or texted before the form (`sign_up_otp`). 15 of 15 employers showed it. atsfill goes around it, through an account made from the sign-in.
- The application is a wizard at `/apply/<id>-0.php`. Signed out it redirects to `/account/login.php?listing_id=<id>&i=0`. Signed in, opening it starts an application, kept unfinished in the account (`goal=app_started`), and the next visit resumes it at the step last reached.
- The steps are listed on the left (`#steps a.step[data-file]`): `instructions`, the employer's choice among `screening` (Job Questions), `employment`, `education`, `resume`, `references`, `additional_questions`, `eeoc` (Voluntary Disclosure), `source`, `disclosure` (Applicant Statement), then `submit`. Each step loads into `#landingStrip` from `GET /apply/view.php?n=apply&f=<step>&csrf_token=<token>`, the token in the page's `input[name=csrf_token]`; Proceed to Next Step (`#next`) checks the step's required answers, saves it and loads the next.

## Account

One account for the whole system, a "job profile": made on one employer's site, it signs in on another's. The account scope is None. The session (`PHPSESSID`) is kept per employer's site, so a form on a site not signed in yet signs in there again from the password source.

- The sign-in (`/account/login.php`: `username`, `password`, Log In) lands on `/account/bootstrap.index.php`, signed in, on any employer's site. A wrong password comes back to the sign-in with `error=invalid` and the attempts left.
- The sign-up is the sign-in's "Need an account? Click here!" (`/account/`): first and last name, email and its confirmation, phone and its type (Cell, Home, Work), street address, country (`United States` preset), state (reloaded for the country picked), city, zip code, password and its confirmation (no rule), text message consent (`applicant_consent_approval`, required), preferred contact method (`text_approval`, Email unless texts are agreed to) and the privacy policy box (`info_use_consent`). The same 18 fields on 15 of 15 employers. Create Job Profile signs in at once, staying on `/account/` as "Update Job Profile"; no code or link is asked, and a mail with the account's login link follows.
- `login --create --details` takes the name, the phone, and the address with a country and a state: the phone as a cell phone, without `+1` for a US or Canadian number; the country by its English name; the state as the country's list names it (`District of Columbia`, not `DC`), refused before sending when the list lacks it. Text messages are declined and the privacy policy box ticked. `login` takes `--url` for the employer's site to sign in on: the system has no site of its own.
- The signed-in check is `GET /account/` on the posting's site: a Logout link (`/account/logout.php`) shows signed in.
- The wizard opens on a dialog, "Please take a minute to update your contact information" (`#verify_contact_info_popup`), the account's contact details with the text message consent and the privacy policy box again: on an employer's site first visited, the consent is empty and both must be given before Save. It cannot be closed otherwise. The fill saves it as it is, texts declined where no choice is saved yet, the privacy box ticked.

## Inventory

From the 15 employers of the sample, read signed in. A step's fields are read from `/apply/view.php` without opening it; a field is `<step>.<key>`, the key a question's id, or a control's id or name.

| Step | Rendering | Field type | Employers |
|---|---|---|---|
| `screening`, `additional_questions` | `select` of Yes and No, or of an employer's options | `choice` | 9 |
| | `select.conditional-answer-select`: one option (`data-conditional_answer`, an index) loads follow-up questions from `f=buildAdditionalQuestions` under it | `choice`, the follow-ups `shown_when` | 1 |
| | text input or `textarea`, `data-maxlength` | `text` | 6 |
| | checkboxes sharing the question's id as their name | `multi_choice` | 3 |
| `resume` | file input (DOC, DOCX, TXT or PDF), uploaded with the Upload Files button it adds; the step loads again listing the file under Submitted Documents | `file` | 15 |
| `eeoc` | radio groups `sex_type`, `ethnic_type`, `is_vet_2014`, each with a decline option, named by their fieldset's legend; `select#is_disabled` | `choice` | 6 |
| `source` | `source_type_id`, then `source_id` loaded for the type from `f=getSources`, Other added to every list | `choice`, `source_id` by option set | 15 |
| | `source_other`, enabled for the source Other or the type Referral (value 4) | `text`, `shown_when` | 15 |
| `references` | text inputs `name_<n>`, `company_<n>`, `occupation_<n>`, `phone_<n>`, `email_<n>`, `years_known_<n>` | `text` | 1 |
| `disclosure` | a checkbox, I agree to the above | `choice` `Yes`/`No` | 1 |
| | a signature drawn on a canvas (`canvas.pad`) | not fillable: `drawn_signature` | 1 |
| `employment`, `education` | a list of entries, each added with its own form and Save | not fillable: `entry_list` | 1 |

- A question saves its answer as it is given (`updateAnswer`); the other steps save on Proceed to Next Step. The fill opens each step from the steps list, answers it and proceeds; a step that stays shows its errors (`[id^=message_]`), the reason the fill stops. It ends on `submit`.
- Once saved, the source is locked: the step shows the type and source disabled, the source's id in a hidden input. A later fill lists them prefilled, and an answer that differs is not taken.
- The read-back fetches each step again: the answers as the system saved them, the source's name looked up in its type's list, the resume the last file listed.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `sign_up_otp` | the posting page's Apply now sends a one-time code to the email or phone before the form; atsfill signs up from the sign-in instead | `unsupported_flow` | 15 of 15 employers |
| `entry_list` | the employment or education step adds entries one by one, each with its own form | `unsupported_rendering` | 1 of 15 employers in the sample, 1 of 10 at the adapter gate |
| `drawn_signature` | the applicant statement asks a signature drawn on a canvas | `unsupported_rendering` | 1 of 15 employers |

## Submitting

Submit Application (`#next` on `submit`) shows once every required step is complete, else the step lists those left. It moves the page to `/apply/thanks.php?orig_job_app=<job>-<application>`, the evidence. Not sent yet.
