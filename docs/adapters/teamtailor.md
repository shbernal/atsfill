# Teamtailor

Observed on live postings, last on 2026-10-18; sends were made by hand through the same page on 2026-10-14. The fixture page is [tests/pages/teamtailor/new.html](../../tests/pages/teamtailor/new.html), served as a company's career site in `tests/test_teamtailor.py`.

## Where the form is

- Career sites live at `<company>.teamtailor.com` or on the company's domain (`careers.<company>.com`, `jobs.<company>.fr`), sometimes under a locale (`/fr/jobs/…`). A job is `/jobs/<id>-<slug>`; the double-send key is `https://<host>/jobs/<id>`.
- On a custom domain, the page shows itself by the "powered by" link to `www.teamtailor.com` in its footer. The footer text is translated ("Career site by Teamtailor", "Outil de recrutement de Teamtailor"), the link is not.
- The form has its own page, `<job>/applications/new`. "Apply for this job" on the job page opens the same form in an overlay; "Apply with LinkedIn" sits above it and is never used.

## Reading the form

- `form#job-application-form`. Standard fields are named `candidate[first_name]`, `candidate[last_name]`, `candidate[email]`, `candidate[phone]`, `candidate[job_applications_attributes][0][cover_letter]`, and the consent boxes `candidate[consent_given]` and `candidate[consent_given_future_jobs]`, which atsfill reads as `Yes`/`No` choices. Which of them a form shows, and which are required, depends on the company.
- A required field's label holds `<sup data-asterisk>`; the translated "Required" next to it is screen-reader text. Inputs carry no `required` attribute.
- The resume is a drop zone over `input[type=file]#candidate_resume_remote_url`; "Additional files" is `#candidate_file_remote_url`.
- Custom questions are `.question` blocks with `data-question-mandatory`, a hidden `[question_id]` (the field id is `question:<question id>`) and an answer input whose name ends in its kind:

| Answer input | Field type |
|---|---|
| `[text]` (input or textarea), `[number]` | `text` |
| `[date]` (`input[type=date]`) | `date` |
| `[boolean]` radios, labels translated | `choice` |
| `[choice]` radios, or a dropdown of `menuitemradio` buttons over hidden radios | `choice` |
| `[choices][]` checkboxes, or a dropdown | `multi_choice` |

  Range, file and video answers are not handled: a required one makes the form `unsupported`, an optional one is left out.

## Filling

- The cookie banner is a modal `<dialog>`. While it is open the rest of the page is inert: typing into a field does nothing and raises nothing. Decline it (`[data-action*="cookies--alert#disableAll"]`) before filling. It can come back over the consent box and the submit button, so clicks check what is on top first.
- Click the consent box's label.
- A picked file uploads at once: `POST /uploads/presigned_data`, then to Teamtailor's S3. The form then holds the file's address in `candidate[resume_remote_url]`, whose last path segment is the file name.
- The phone field (intl-tel-input) takes an international number and keeps it as `+<digits>`.
- In a dropdown choice the hidden radios still have labels: pick from the dropdown, not the hidden label.

## Read-back and submit

- Nothing reaches the company before submit, so the read-back is the form's `FormData`: each value, each checked option's label (or its dropdown item's text), and for a file the name at the end of its stored address.
- Submit is `[type=submit]` in the form, under a window at least 900px tall: in a short one the button sits below the fold and the click sends nothing. The evidence is the redirect to `…/applications/<id>/thanks…`, titled "Applied to <role>". An optional "Connect" step that follows is skipped.
