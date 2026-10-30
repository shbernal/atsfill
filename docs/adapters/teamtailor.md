# Teamtailor

Observed on live postings, last on 2026-10-29. The fixture page is [tests/pages/teamtailor/new.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/teamtailor/new.html), served as an employer's career site in `tests/test_teamtailor.py`.

## Where the form is

- Career sites live at `<employer>.teamtailor.com` or on the employer's domain (`careers.<employer>.com`, `jobs.<employer>.fr`), sometimes under a locale (`/fr/jobs/…`). A job is `/jobs/<id>-<slug>`; the double-send key is `https://<host>/jobs/<id>`.
- On a custom domain, the page shows itself by the "powered by" link to `www.teamtailor.com` in its footer. The footer text is translated ("Career site by Teamtailor", "Outil de recrutement de Teamtailor"), the link is not. Some sites drop the link; they still load Teamtailor's `careersite-*.js` from `teamtailor-cdn.com`.
- The form has its own page, `<job>/applications/new`. "Apply for this job" on the job page opens the same form in an overlay; "Apply with LinkedIn" sits above it and is never used.
- `applications/new` is server-rendered HTML, served without a session, so `request_read` fetches it and `browser_read` takes the loaded form's HTML, and one parser lists the fields from both. The request read covers `*.teamtailor.com` hosts only, since a custom domain is known only by its page. Scripts add two things the server's HTML lacks: a drop zone's `input[type=file]` (the drop zone is read from its `forms--inputs--upload` controller, its label and `data-forms--inputs--upload-accepted-files-value`), and a video question's accepted types, which the request read leaves `null`.
- A career site can send its applications elsewhere: `applications/new` then redirects to another host, and the posting is `unsupported` as applying on another site.

## Reading the form

- `form#job-application-form`. Standard fields are named `candidate[first_name]`, `candidate[last_name]`, `candidate[email]`, `candidate[phone]`, `candidate[job_applications_attributes][0][cover_letter]`, and the consent boxes `candidate[consent_given]` and `candidate[consent_given_future_jobs]`, which atsfill reads as `Yes`/`No` choices. Which of them a form shows, and which are required, depends on the company.
- A required field's label holds `<sup data-asterisk>`; the translated "Required" next to it is screen-reader text. Inputs carry no `required` attribute.
- The resume is a drop zone over `input[type=file]#candidate_resume_remote_url`; "Additional files" is `#candidate_file_remote_url`.
- Custom questions are `.question` blocks with `data-question-mandatory`, a hidden `[question_id]` (the field id is `question:<question id>`) and an answer input whose name ends in its kind. A qualifying question is a `[data-careersite--form-target=qualifyingAnswers]` block of the same make, always required. A follow-up question carries `data-question-show-if-uuid` (its earlier question's `data-question-uuid`) and `data-question-show-if-value` (the option value that shows it); its condition is `Is` that question and that option, its `data-question-mandatory` kept. A follow-up stays in the page hidden until it shows.

Each rendering, and on how many of 15 postings' forms (2026-10-19):

| Page | Field type | Postings |
|---|---|---|
| `candidate[first_name]`, `[email]`… text inputs, the cover letter textarea | `text` | 15, the cover letter on 9 |
| `candidate[phone]` (intl-tel-input) | `phone` | 15 |
| consent checkboxes | `choice`, `Yes`/`No` | 15 |
| resume drop zone `#candidate_resume_remote_url` | `file` | 15 |
| "Additional files" `#candidate_file_remote_url`, several files allowed | `file`, one | 13 |
| address autocomplete `candidate[location][query]` (`role=combobox`) | `typeahead` | 7 |
| office locations, `fieldset` of `candidate[location_ids][]` checkboxes | `multi_choice` | 2 |
| question `[text]`, input or textarea | `text` | 11 |
| question `[number]` | `text`, a number | 4 |
| question `[boolean]` radios, labels translated | `choice` | 8 |
| question `[choice]` radios | `choice` | 4 |
| question `[choice]` dropdown of `menuitemradio` buttons over hidden radios | `choice` | 3 |
| question `[choices][]` checkboxes | `multi_choice` | 2 |
| question `[choices][]` dropdown of `menuitemcheckbox` buttons, with a search box | `multi_choice` | 1 |
| question `[range]`, a slider with `min`, `max`, `step` | `choice` of each step | 0, seen before |
| question `[date]` (`input[type=date]`) | `date` | 0, seen before |
| qualifying question | `choice` | 1 |
| follow-up question | as its kind, with its condition | 2 |
| video question (`[picked_video_attributes]`), its "Upload video" input; recording is never used | `file`, a clip | 3, required on 2 |

## Filling

- The cookie banner is a non-modal `<dialog data-controller="common--cookies--alert">` over the form. The blocker's lists hide it on every domain, custom ones included. A career site can make it a takeover (`data-common--cookies--alert-takeover-modal-value="true"`), opened with `showModal()`: hidden, it still leaves the rest of the page inert, so keys typed into the address go nowhere and clicks find the page overlaid. Its "decline optional cookies" button (`#disableAll`) closes it, clicked before the fill.
- Qualifying questions are answered first. While one is unanswered, or answered with what the employer does not accept, the rest of the form sits under `[data-careersite--form-target=formContentWrapper].cursor-not-allowed` and takes no clicks; atsfill then stops with `not_qualified`.
- A follow-up question shows once its earlier question has the answer it waits for, and is filled once shown. Once filled, the adapter lists the questions the page shows (`checkVisibility()`), for the core to compare with its conditions.
- Click the consent box's label, or the box itself when the employer left the label empty (an inline `<p><br></p>`, no box to click).
- A picked file uploads at once: `POST /uploads/presigned_data`, then to Teamtailor's S3. The form then holds the file's address in `candidate[resume_remote_url]`, whose last path segment is the file name as Teamtailor stores it: ASCII, lowercase, each run of characters other than letters, digits, `.` and `-` one `_` (`CV Élodie (2026).pdf` is `cv_elodie_2026_.pdf`). The read-back gives the answer's name back when the stored one is its rewrite.
- The phone field (intl-tel-input) takes an international number and keeps it as `+<digits>`.
- In a dropdown choice the hidden radios still have labels: pick from the dropdown, not the hidden label, the item whose `data-search-text` is the answer (an apostrophe in it is common in French). A multiple choice dropdown stays open after a pick, over the fields below it, so it is closed once its options are picked.
- The address is typed key by key, and the first suggestion (`[role=option]`, its text in `data-description`) matching it is picked; only a pick fills `candidate[location][place_id]` and the city, country and coordinates beside it.
- A video question offers to record a clip or to upload one; atsfill uploads the answer's file through its hidden `input[type=file]`, which takes the extensions in its `accept`. The page posts `/uploads/presigned_data` (`"type":"video"`), puts the clip on S3, then `POST /videos` with its storage address answers `{id, sgid}`. The form keeps the id in `[picked_video_attributes][video_id]`, enabled only then; `[video_attributes][file]` stays disabled. Teamtailor transcodes the clip and `GET /videos/<sgid>` gives it back with `file_file_name`, the stored name, and its `transcode_status`. The page checks only the extension and a `video/*` type, no size or length: a file it rejects (its "not supported" message) posts no video, `answer_not_taken`. One that does not decode is posted, its transcode ends `error` and the form keeps no video id; one grey second of 64x64 H.264, without audio, is enough (the Test user's clip).
- A range question is set on its slider, `input[type=range]`.
- A number question takes only a number; any other answer leaves it empty, a mismatch.

## Read-back and submit

- Nothing reaches the employer before submit, so the read-back is the form's `FormData`: each value, each checked option's label (or its dropdown item's text), for a file the name at the end of its stored address, for a video question the `file_file_name` Teamtailor gives back for the video id the form keeps, and the address only once a suggestion filled its `place_id`. Each additional file is stored under its own `candidate[uploads_attributes][<n>][file_remote_url]`, a text input the upload adds: an address, not a field.
- Submit is `[type=submit]` in the form, under a window at least 900px tall: in a short one the button sits below the fold and the click sends nothing. The evidence is the redirect to `…/applications/<id>/thanks…`, the id a UUID, titled "Applied to <role>". An optional "Connect" step that follows is skipped.
- Some employers hold the application until the applicant confirms their email: submit then redirects to `<job>/applications/email_verification_needed` ("Vérifiez votre adresse e-mail"), and the application completes only once the link emailed to the address in the form is clicked. The mailbox is outside atsfill (ADR 0015): the send ends `submitted` with `held: …`, and the double-send guard stops another.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `file_question` | file question: a drop zone inside a `.question` | `unsupported_rendering` | 0 |
