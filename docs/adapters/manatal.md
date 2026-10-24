# Manatal

Observed on live postings, last on 2026-10-22. The fixture page is [tests/pages/manatal/apply.html](../../tests/pages/manatal/apply.html), drawing the fields of [form.json](../../tests/pages/manatal/form.json), served behind a job page in `tests/test_manatal.py`.

## Where the form is

- Job pages: `www.careers-page.com/<client>/job/<code>`; the form is on `…/job/<code>/apply`, which atsfill opens. The double-send key is `https://www.careers-page.com/<client>/job/<code>`.
- The apply page embeds the job's numeric id (`const selectedJobId`) and whether the terms box shows (`const isTCPPRequired`). `GET /api/v1.0/jobs/<id>/application-form/` lists the fields as JSON, with no session or token: `read_api` reads the id from the apply page, then that endpoint.
- An AWS WAF sits in front; a challenge answer (`x-amzn-waf-action: challenge`) is read as `captcha`. It has not shown on a dry run.

## Reading the form

A Vue 2 app draws the endpoint's entries in their order, each by `field_type`, then the candidate field's `client_field_slug` and `client_field_field_type`, then `response_type`. Field ids are the entries' numeric ids; the terms box is `terms_and_condition`.

Each rendering and on how many of 100 postings from 25 employers (2026-10-20, over the endpoint):

| Rendering | Field type | Postings |
|---|---|---|
| text input: full name, email, custom `char` fields, `other` `char` fields | `text` | 100 |
| phone, a text input (`phone_number`) | `phone` | 92 |
| file input, resume (PDF, DOC, DOCX, RTF) | `file` | 92 |
| textarea: cover letter (`description`), `longtext` fields | `text` | 94 |
| text input, social profile (`social_media`) | `text` | 7 |
| number input with currency and frequency selects (`expected_salary`, `current_salary`) | `text` labelled `(a number)`, the amount; `<id>.currency` and `<id>.frequency`, `choice`, required with the amount | 11 |
| number input (`integer`) | `text` labelled `(a number)` | 2 |
| select2 multiple select from a list of its own (`nationalities`, `industries`, also `languages`) | `multi_choice` | 7 |
| select with a fixed list (`current_notice_period`, `years_of_experience`, `gender`) | `choice` | 6 |
| select (`dropdown`) | `choice` | 4 |
| radio list (`multiple_choice`) | `choice` | 6 |
| single checkbox (`boolean`) | `choice`, `Yes`/`No` | 1 |
| flatpickr date, `Y-m-d` (`datetime`) | `date` | 1 |
| multiple file input (`attachment`) | `file`, one file | 1 |
| checkbox list (`checkbox`), select2 multiple (`multiple_select_dropdown`) | `multi_choice` | 0, from the template |
| terms and privacy box | `choice`, `Yes`/`No`, required | 100 |

The options of a select, a radio or a checkbox list are read from the page, labels rather than values (`30 days`, not `30 00:00:00`). `read_api` takes them from the entries' `client_field_answer_choices`, the template's fixed lists (gender, notice period, years of experience, salary frequency) and the lists the page fetches apart (`/api/v1.0/nationalities/`, `languages/`, `clients/<client>/industries/`, `currencies/`), labelled as the page labels them.

A salary's currency and frequency selects (`#expected_currency`, `#expected_frequency`, `#current_…`) have no placeholder: unanswered, they send their first option, a currency that depends on the order `currencies/` returns.

A radio or checkbox list with an `Other` option opens a text box (`.custom-other-val-input`) under it once `Other` is picked; Apply sends that text in place of `Other`, and refuses an empty one on a required list. It is listed as `<id>.other`, `shown_when` the list holds `Other`, required where the list is, a known gap.

A number input (`min="0"`) holds no text: an answer that is not a number is left out, reads back empty and the dry run refuses it.

## Filling

Nothing is saved before Apply: the app reads the inputs only when Apply is clicked, so `fill` sets inputs and `read_back` reads them as the app will.

- Page globals are out of reach (Camoufox's isolated world), so select2 and flatpickr are not called: a multiple select gets its options selected and a `change` event, which select2 follows; a date is written into flatpickr's read-only input with a `change` event.
- Radios and checkboxes are hidden behind styled marks, at times outside the viewport, and are clicked from the page's script when their state differs.

## Read-back and submit

- Read-back: input values, selected options' labels, checked values, the file input's file name, the boxes' state.
- Submit has not been observed. Apply uploads each file to S3 over a presigned POST (`…/apply-by-career-page-resume-get-presigned-url/`), then posts the answers to the same `application-form/` endpoint with the page's CSRF token; the evidence is that response.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `entries` | "Add Education" / "Add Experience" entry sub-form (`educations`, `experiences`) | `unsupported_rendering` | 1 |
| `other` | the text box an answer of `Other` opens on a radio or checkbox list | `unsupported_rendering` | 1 of 25 (2026-10-22) |
