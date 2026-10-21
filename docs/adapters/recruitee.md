# Recruitee

Observed on live postings, last on 2026-10-21. The fixture page is [tests/pages/recruitee/apply.html](../../tests/pages/recruitee/apply.html), served as a posting's application page in `tests/test_recruitee.py`.

## Where the form is

- Postings: `<account>.recruitee.com/o/<slug>`, or the same career site on the employer's own domain (`jobs.example.com/o/<slug>`), told apart by the career site's scripts (`/js/PublicApp/…`, `recruiteecdn.com`). The form is `…/o/<slug>/c/new`, which atsfill opens. The double-send key is the posting URL without `/c/new` or query.
- A posting that is gone answers 404 with no `form#offer-application-form`, read as `posting_gone`.
- The career site is a React app; no cookie banner was seen. Its offers API (`/api/offers/<slug>`) lists the questions (`open_questions`, `options_cv`, `options_photo`…) and could back a `read_api`; there is none yet.

## Reading the form

Every field is a named control, and the name is the field's id: `candidate.name`, `candidate.cv`, `candidate.openQuestionAnswers.<question>.content|flag|multiContent|file`, `candidate.locations.value`, `candidate.agreements.<n>.consent`. A control's label is its `label[for]`, a radio or checkbox group's its `legend`; required ones carry `required` and a `*` the label hides from screen readers. Each rendering and on how many of 62 postings (offers API, 2026-10-21; each rendering checked on the page of 18):

| Rendering | Field type | Postings |
|---|---|---|
| `candidate.name`, `candidate.email`: `input[type=text\|email]` | `text` | 62 |
| `candidate.phone`: `input[type=tel]` behind a country picker, prefilled with the site's country code; required on 60, absent on 1 | `phone` | 61 |
| `candidate.cv`, `candidate.coverLetterFile`, `candidate.photo`: `input[type=file]`, accepted types as MIME types | `file` | 62, 57, 8 |
| `string` question: `input[type=text]` | `text` | 25 |
| `text` question: `textarea` | `text` | 16 |
| `boolean` question: radios `…flag` valued `true`/`false`, shown Yes/No | `choice` | 28 |
| `single_choice` question: radios `…content` | `choice` | 12 |
| `multi_choice` question: checkboxes `…multiContent` | `multi_choice` | 7 |
| `salary`, `number` question: `input[type=number]`, a currency beside salary | `text`, labelled "(a number)" | 7, 2 |
| `date` question: `input[type=date]` | `date` | 4 |
| `file` question: `input[type=file]` `…file` | `file` | 2 |
| `legal` question: one checkbox `…flag` (`data-testid=legal-input-field-value-input`), labelled by `aria-labelledby` | `choice`, `Yes`/`No` | 7 |
| legal agreement: one checkbox `candidate.agreements.<n>.consent` labelled by rich text; not in the offers API, on 1 of the 15 surveyed | `choice`, `Yes`/`No` | — |
| preferred location: checkboxes `candidate.locations.value` valued by location id | `multi_choice`, by label | 4 |
| `infobox`: text only, no control | — | 5 |

Out of scope here: a video recorded live (Ziggeo), see ADR 0014.

"Write it here instead" turns the cover letter upload into a textarea; atsfill keeps the upload.

## Filling

- Text, numbers and dates are filled; the date input takes `YYYY-MM-DD`.
- The phone is typed after clearing the prefilled code; the country picker follows the number's prefix.
- Radios and checkboxes are drawn hidden under styled boxes: atsfill finds the option by its label and dispatches a click on the input itself.
- Files are attached and stay in the page; nothing reaches Recruitee before submit.

## Read-back and submit

- The read-back is the page: a choice reads its checked option's label, a file its attached name.
- Every form carries an invisible hCaptcha served from `recruiteecdn.com`, run on submit (`[data-testid=submit-application-form-button]`). The page then shows a thanks panel, the evidence, or a panel saying the address already applied (with a `mailto:` link), read as `blocked: already_applied`. A visible hCaptcha challenge is `blocked: captcha`. Not observed on a send yet.
