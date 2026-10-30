# Work at a Startup

Y Combinator's job board. Observed on live postings on 2026-10-28, headless Camoufox, on 16 employers signed in and 139 postings read by request. The fixture pages are [tests/pages/workatastartup/posting.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/workatastartup/posting.html), a ycombinator.com posting, [job.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/workatastartup/job.html), its signed-in Work at a Startup job page with the apply dialog, and [account.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/workatastartup/account.html), the sign-in and sign-up, served by `tests/test_workatastartup.py`.

## Where the form is

- Postings come as `www.ycombinator.com/companies/<employer>/jobs/<slug>-<title>`. The page is an Inertia page: its `data-page` attribute holds the props (component `WaasShowJobPage`), among them `job.id`, `customQuestions`, `signedIn` and `showDirectApply`. The same job is `www.workatastartup.com/jobs/<job id>`, which keys the posting: a ycombinator.com URL's key takes the id from its props, by request.
- The Work at a Startup job page's props (component `JobDetailPage`) hold the job, its `customQuestions`, the account's `profile` (`minimally_complete`, `shared`), `appliedJobIds` and `locationEligible`. The form is filled there.
- A closed posting answers 404.
- Plain HTTP clients are served the posting page, props included, and `GET www.workatastartup.com/jobs/<job id>/custom_questions.json`, the job's questions, without an account: the request read. The Work at a Startup job page itself answers them 406.

### Signed out

"Apply to role" on ycombinator.com opens a modal that needs no account: first and last name, email, a LinkedIn URL (required, `linkedin.com/...`), a phone with a country select, the current city (a typeahead of `City, ST, USA`), `us_authorized` and `us_visa_sponsorship` radios on US jobs only, a resume (PDF, DOC or DOCX), a message, the custom questions, and an hCaptcha checkbox. The checkbox brings up an image challenge (3 of 3 runs, headless and headed), so this form cannot send and atsfill does not fill it.

## Account

One account per user for the whole system, a Y Combinator account (`account.ycombinator.com`), shared by every YC site.

- Sign-in (`login workatastartup`): `account.ycombinator.com/?continue=<url>`, the email or username (`#ycid-input`), Continue (`POST /start_sign_in`), then the password (`#password-input`) and "Log in with password" (`POST /sign_in`), which lands on Work at a Startup. A wrong password is 422, "Password doesn't match": `refused: wrong_password`. "Send login link" signs in by email instead, never used.
- Some password sign-ins end on "We've sent an email to <address>. Open the link in the email we just sent, or enter its code": an OTP of six digits, valid ten minutes, typed into the step's input from the OTP command and posted to `POST /sign_in/email_code`. It was asked on the account's second sign-in, and not on later ones, from that browser identity or a new one.
- Sign-up (`login workatastartup --create --email <address> --details <file>`, its `name` the first and the last name): `account.ycombinator.com/?continue=<url>&defaults[signUpActive]=true` asks first name, last name, email, username and password; atsfill makes the username from the name and six random hex digits. Posted to `POST /sign_up`. No email verification: it lands signed in on the profile wizard. A taken email is 422 with `errors.email`: `refused: email_in_use`; a password refused, `errors.password`: `refused: password_rule`. A `name` of one word is `refused: sign_up_details`, before anything is sent.
- The signed-in check is `GET www.workatastartup.com/user/current_user.json`: the user for an account, `null` for a visitor. Signed out, `/application/<step>` redirects to `account.ycombinator.com/authenticate`.

## Account profile

Applying needs the account's profile complete and visible. Signed in with the profile incomplete, the job page shows "Finish profile to apply" (to `/application/next`, the first incomplete step); with the profile hidden, "Change status to apply".

The profile is a wizard at `www.workatastartup.com/application/<step>`, read and saved by `atsfill profile workatastartup` (ADR 0017). Its props (`ProfileWizardPage`, `sectionAppProps`) list the steps in `sectionInfo`, each with its status, and name the step shown in `activeSection`: a step asked before the ones it needs are saved shows another, and the read stops there. Every step of a complete profile loads directly, with its saved values. The fixture page is [tests/pages/workatastartup/profile.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/workatastartup/profile.html).

- Steps: `personal`, `location`, `role`, `experience`, `skills` (Engineering only), `career`, `share`.
- Save & next (Continue on an incomplete profile, Finish on the last step) posts `POST /application/next`, `{from: <step>, profile: {data: {…}}}`, answered `{redirect_to}`, where the page goes next. A value the server refuses is 422 with `errors` ("Phone number is an invalid number"): `refused: step_refused: <step>: <errors>`. A required field left empty shows "Required" under it and posts nothing: `refused: step_refused: <step>`.
- The job search status is `share`, on `personal`: "I'm actively looking for a job", "I'm open to new opportunities", or "I'm not looking for a job / not ready to meet founders yet. HIDE MY PROFILE." Any but the last lets the account apply; which one is the user's answer, never atsfill's.
- `experience` offers a resume upload, a LinkedIn import, or "fill in your experience manually": atsfill takes the manual path, the only one read back field by field, and fills the first entry of Work History and of Education.
- The page loads a SEON device fingerprint (`/api/sessions`) on every step.

| Field ids | Field type | Rendering |
|---|---|---|
| `first_name`, `last_name`, `email`, `linkedin`, `github`, `experience` (a number), `short_phrase` | `text` | input, labelled |
| `looking_for`, `proud_project`, `work_summary`, `education_summary` | `text` | textarea |
| `share`, `yc_affiliation`, `us_authorized`, `us_visa_sponsorship`, `remote`, `relocate`, `in_school`, `equity_importance`, `salary_range_importance` | `choice` | radio fieldset, the labels the options (`us_visa_sponsorship`'s `__no` is labelled Yes) |
| `role` | `choice` | radio fieldset, each option its `<b>` name |
| `job_type` | `multi_choice` | checkbox fieldset |
| `company_sizes_1_10`, `_11_50`, `_51_300`, `_300_+` | `choice` | a grid row of radios, each `aria-label` "<row>: Not Interested\|OK\|Preferred" |
| `phone_country` | `typeahead` | react-select, "France (+33)" |
| `phone_number` | `phone` | tel input, formatted as typed |
| `city_current` | `typeahead` | places combobox, "City, ST, USA" |
| `eng_type` | `multi_choice` | react-select multi, options on focus, a chip per pick; shown for Engineering. Backspace in its empty box removes the last pick |
| `work_employer`, `work_title`, `education_school`, `education_field`, `education_degree` | `text` | downshift combobox that takes free text: a suggestion is picked only when it is the answer |
| `work_location` | `text` | places combobox, the typed text kept |
| `work_from`, `work_to`, `education_from`, `education_to` | `text`, `MM/YYYY` | month and year selects named `<entry id>_start_date_month`; `work_to` shows while `work_current` is `No` |
| `work_current` | `choice`, `Yes`/`No` | checkbox "I currently work here" |
| `skills` | `multi_choice`, each `<skill>: Beginner\|Intermediate\|Advanced` | a react-select search, up to 10, a remove button and a rating radio group per skill |
| `hidden_companies` | `multi_choice`, no options | "Are there YC companies you want to be hidden from?", marked `*` but saved empty: a downshift search of the YC directory by any part of the name ("Stripe" lists StrideQ, Simple Strips), a chip with a × button per pick. Each answer is searched and the result named exactly as it picked, `answer_not_taken` otherwise; a company the answer leaves out is removed, so an empty answer clears the list |

## Reading and filling

A form command on an account profile not complete (`minimally_complete` false) or hidden (`shared` false) ends `blocked: profile_incomplete`, naming the `profile` command. A job in `appliedJobIds` is `posting_gone`, as is a page with no job.

Signed in with the profile complete and visible, Apply on `/jobs/<job id>` opens a dialog "Reach out to <founder> at <employer>". Field ids: `message`, and `question_<id>` per question. The page names none of the controls: each question's block is found by its label.

- The message, a textarea whose placeholder names the account ("Hi! My name is Jane Doe and…"), at least 50 characters. Send stays disabled until it has them and every required question is answered. The field declares it (`min_length`): a shorter answer is `refused: invalid_answers`.
- The custom questions, from the props' `customQuestions` or `GET /jobs/<job id>/custom_questions.json`, each with `id`, `question_type`, `label`, `required`, `archived_at` and `options` (`id`, `label`); archived ones are not shown. A required one's label ends in ` *`. Renderings, from the page's `QuestionsForm` and seen live:

| Question type | Rendering | Questions (on 23 postings of 139) |
|---|---|---|
| `long_text` | textarea, 5 rows, up to 5000 characters, no name | 21 |
| `text` | textarea, 1 row, up to 250 characters, no name | 15 |
| `url` | textarea, 1 row, up to 2000 characters, no name | 0 |
| `single_choice` | radios named `custom_q_<id>` | 6 |
| `multiple_choice` | checkboxes, no name | 2 |

- Where the job is outside the profile's location preferences (10 of 16 employers), a checkbox "This role doesn't match your location preferences. Check here if you're open to relocating — we'll update your profile so companies can contact you.", ticked when the dialog opens. Sent ticked, it changes the account profile, so atsfill always unticks it: relocation is the profile's `relocate`, set by `profile`, never by an application.
- Otherwise, on some jobs, a note that the qualifications might not match, with no control.
- Nothing is sent while the dialog is filled: no draft, no autosave.

## Submitting

Send posts `POST www.workatastartup.com/apply`, JSON `{company_id, job_id, message, custom_question_answers, open_to_relocation}`, `custom_question_answers` keyed by question id (a choice by option id, several choices as a list of ids). The page reads the answer as:

- success: `company_id` and `job_ids`, the jobs applied to at that employer, the job among them: the evidence. Without the job, `unknown`.
- 422 with `success: false` and a `question_id` and `error`: an answer refused, shown under its question: `blocked: answer_refused`.
- `limit` (with a `reason`): the account's weekly application limit, shown in an alert: `blocked: rate_limited`.
- `success: false` otherwise: `error`, shown above the questions: `blocked: answer_refused`.

No send has been observed.
