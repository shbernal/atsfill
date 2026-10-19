# Ashby

Observed on live postings, last on 2026-10-19. The fixture page is [tests/pages/ashby/](../../tests/pages/ashby/), served by a stand-in for Ashby's API in `tests/test_ashby.py`.

## Where the form is

- Hosted postings: `jobs.ashbyhq.com/<org>/<job id>`, the form at `/application`. Every URL of a posting gives the double-send key `https://jobs.ashbyhq.com/<org>/<job id>`.
- Career pages on the employer's domain load Ashby's embed script (`jobs.ashbyhq.com/<org>/embed?version=2`), which opens `?ashby_jid=<job id>` in an iframe at `jobs.ashbyhq.com/<org>/<job id>?embed=js`. The adapter claims such a page once that iframe appears and fills the hosted posting it names.
- Some employers render their own form from Ashby's data with no Ashby iframe (field names like `apply:_systemfield_name`). Those pages are not claimed.

## Reading the form

The page asks `POST /api/non-user-graphql?op=ApiJobPosting` with `organizationHostedJobsPageName` and `jobPostingId`. Its `applicationForm` and `surveyForms` are form renders: sections of field entries, each with the field (`path`, `title`, `type`, `selectableValues`), `isRequired` and `isHidden`. The field id is the `path`.

The endpoint answers without a session, so `read_api` sends the same query over HTTP. Cloudflare turns away Python's default user agent (error 1010), not a named one.

Each type, how the page renders it, and on how many of 15 companies' postings (2026-10-19):

| Ashby type | Field type | Rendering | Postings |
|---|---|---|---|
| `String`, `Email`, `Url`, `Number`, `Phone` | `text`, `phone` for `Phone` | `input` of the matching `type` | 15 |
| `LongText` | `text` | `textarea` | 7 |
| `Date` | `date` | text input with a date picker | 3 |
| `File` | `file` | hidden file input in a drop zone | 15 |
| `Location` | `typeahead` | combobox of suggestions | 5 |
| `Boolean` | `choice` (`Yes`/`No`) | two buttons | 8 |
| `ValueSelect` | `choice` | radio group, up to 8 options seen | 5 |
| `ValueSelect` | `choice` | combobox, from 10 options seen | 2 |
| `MultiValueSelect` | `multi_choice` | checkbox group | 6 |

A type outside this table is `form_shape`.

Survey forms come after the application form on the same page, on about half the postings sampled. Their questions seen so far are optional demographic selects with an "I prefer not to answer" option.

## Filling

- Each change posts `ApiSetFormValue` with the form render's id, the path and the value, and the response is the whole render as Ashby holds it. That response is the read-back.
- A file goes in three requests: `ApiCreateFileUploadHandle` (name, type, size) returns a handle and a signed S3 form, the file is posted to S3, then `ApiSetFormValueToFile` sets the path to the handle and returns the render. On a real posting this last response comes after the saves of fields filled later.
- Saves in flight together overwrite each other at Ashby: a later render can drop values an earlier one held, or come back under a new id holding one field. The adapter makes one change at a time and waits for its save.
- A survey answer comes back as the survey's render alone, under an id that differs from the one `ApiJobPosting` gave it. The adapter puts each response in place of the form holding the same fields.
- Radio, checkbox and yes/no inputs are hidden: click the option's text.
- A combobox select saves only a picked option. Typing lists every option containing the text, so the pick is by exact name.
- The location combobox saves only a picked suggestion.
- The date field parses every keystroke, so typing it a character at a time saves a wrong year; filling it in one go works. It reads `11/02/2026` as month/day and saves `2026-11-02`, so date answers are ISO.

## Submitting

- The submit button sends `ApiSubmitSingleApplicationFormAction`, or `ApiSubmitMultipleFormsAction` when there are survey forms (the same result plus `surveyFormResults`).
- `applicationFormResult` is `FormSubmitSuccess` when sent, else a form render whose `errorMessages` and `formErrors` say why. `messages.blockMessageForCandidateHtml` is Ashby refusing the candidate.
- Submit runs reCAPTCHA Enterprise. A low score answers `RECAPTCHA_SCORE_BELOW_THRESHOLD`, which the page shows as "flagged as possible spam", and sends nothing. Camoufox passes it, headless; stock Playwright does not (ADR 0002).
