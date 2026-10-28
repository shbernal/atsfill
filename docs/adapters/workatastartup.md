# Work at a Startup

Y Combinator's job board. Observed on live postings on 2026-10-28, headless Camoufox, on 16 employers signed in and 139 postings read by request.

## Where the form is

- Postings come as `www.ycombinator.com/companies/<employer>/jobs/<slug>-<title>`. The page is an Inertia page: its `data-page` attribute holds the props (component `WaasShowJobPage`), among them `job.id`, `customQuestions`, `signedIn` and `showDirectApply`. The same job is `www.workatastartup.com/jobs/<job id>`, which keys the posting.
- A closed posting answers 404.
- Plain HTTP clients are served the posting page, props included.

### Signed out

"Apply to role" on ycombinator.com opens a modal that needs no account: first and last name, email, a LinkedIn URL (required, `linkedin.com/...`), a phone with a country select, the current city (a typeahead of `City, ST, USA`), `us_authorized` and `us_visa_sponsorship` radios on US jobs only, a resume (PDF, DOC or DOCX), a message, the custom questions, and an hCaptcha checkbox. The checkbox brings up an image challenge (3 of 3 runs, headless and headed), so this form cannot send and atsfill does not fill it.

## Account

One account per user for the whole system, a Y Combinator account (`account.ycombinator.com`), shared by every YC site.

- Sign-in: `account.ycombinator.com/?continue=<url>`, the email or username (`#ycid-input`), Continue (`POST /start_sign_in`), then the password (`#password-input`) and "Log in with password" (`POST /sign_in`). A wrong password is 422, "Password doesn't match". "Send login link" signs in by email instead.
- A password sign-in from a device the account has not seen ends on "We've sent an email to <address>. Open the link in the email we just sent, or enter its code": a six-digit code, posted to `POST /sign_in/email_code`, valid ten minutes. A later sign-in with the same browser identity skips it.
- Sign-up: `account.ycombinator.com/?continue=<url>&defaults[signUpActive]=true` asks first name, last name, email, username and password. No email verification: it lands signed in on the profile wizard.
- The signed-in check is `GET www.workatastartup.com/user/current_user.json`: the user for an account, `null` for a visitor. Signed out, `/application/<step>` redirects to `account.ycombinator.com/authenticate`.

## Account profile

Applying needs the account's profile complete and visible. Signed in with the profile incomplete, the job page shows "Finish profile to apply" (to `/application/next`, the first incomplete step); with the profile hidden, "Change status to apply".

The profile is a wizard at `www.workatastartup.com/application/<step>`: `personal`, `location`, `role`, `experience`, `skills` (Engineering only), `career`, `share`. Each step saves on its submit button (Continue, Save & next once the profile is complete). Every step of a complete profile loads directly, with its saved values; the page's props (`ProfileWizardPage`, `sectionAppProps`) hold them too.

- The job search status is on `personal`, radios `share`: `yes` "I'm actively looking for a job", `passive` "I'm open to new opportunities", `no` "I'm not looking for a job / not ready to meet founders yet. HIDE MY PROFILE." `passive` is enough to apply.

## Reading and filling

Signed in with the profile complete and visible, Apply on `/jobs/<job id>` opens a dialog "Reach out to <founder> at <employer>":

- The message, a textarea whose placeholder names the account ("Hi! My name is Jane Doe and…"), at least 50 characters. Send stays disabled until it has them and every required question is answered.
- The custom questions, from the props' `customQuestions` or `GET /jobs/<job id>/custom_questions.json`, each with `id`, `question_type`, `label`, `required`, `archived_at` and `options` (`id`, `label`); archived ones are not shown. A required one's label ends in ` *`. Renderings, from the page's `QuestionsForm` and seen live:

| Question type | Rendering | Questions (on 23 postings of 139) |
|---|---|---|
| `long_text` | textarea, 5 rows, up to 5000 characters, no name | 21 |
| `text` | textarea, 1 row, up to 250 characters, no name | 15 |
| `url` | textarea, 1 row, up to 2000 characters, no name | 0 |
| `single_choice` | radios named `custom_q_<id>` | 6 |
| `multiple_choice` | checkboxes, no name | 2 |

- Where the job is outside the profile's location preferences (10 of 16 employers), a checkbox "This role doesn't match your location preferences. Check here if you're open to relocating — we'll update your profile so companies can contact you.", ticked when the dialog opens. Sent ticked, it changes the account profile.
- Otherwise, on some jobs, a note that the qualifications might not match, with no control.
- Nothing is sent while the dialog is filled: no draft, no autosave.

## Submitting

Send posts `POST www.workatastartup.com/apply`, JSON `{company_id, job_id, message, custom_question_answers, open_to_relocation}`, `custom_question_answers` keyed by question id (a choice by option id, several choices as a list of ids). The page reads the answer as:

- success: `company_id` and `job_ids`, the jobs applied to at that employer, the job among them: the evidence;
- 422 with `success: false` and a `question_id` and `error`: an answer refused, shown under its question;
- `limit` (with a `reason`): the account's weekly application limit, shown in an alert;
- `success: false` otherwise: `error`, shown above the questions.

No send has been observed.
