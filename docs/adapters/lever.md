# Lever

Observed on live postings, last on 2026-10-20. The fixture page is [tests/pages/lever/apply.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/lever/apply.html), served as a posting's application page in `tests/test_lever.py`.

## Where the form is

- Postings: `jobs.lever.co/<account>/<uuid>` (`jobs.eu.lever.co` for EU accounts); the form is `…/<uuid>/apply`, which atsfill opens. The double-send key is the posting URL without `/apply` or query.
- A posting that is gone answers 404 with no `form#application-form`, read as `posting_gone`.
- The page is server-rendered HTML; no cookie banner was seen. Lever's postings API (`api.lever.co/v0/postings/…`) leaves the application's questions out, so there is no request read.

## Reading the form

Every field is a named control, and the name is the field's id: `name`, `urls[LinkedIn]`, `cards[<card>][field<n>]`, `eeo[gender]`, `surveysResponses[<survey>][responses][field<n>]`, `consent[store]`. A question sits in a `.application-question`; its label is `.application-label .text` or `.application-label`. Required ones carry `<span class="required">✱</span>` or a `required` attribute (a consent box has only the attribute). Each rendering and on how many of 40 postings (2026-10-20):

| Rendering | Field type | Postings |
|---|---|---|
| `name`, `email`, `org`, `urls[…]`: `input[type=text\|email]` | `text` | 40 |
| `phone`: plain `input` | `phone` | 40 |
| `location`: `#location-input`, suggestions in `.dropdown-results .dropdown-location`; the pick goes to hidden `selectedLocation` as JSON; required on 17 | `typeahead` | 40 |
| `resume`: `input[type=file]` in an `<a>` showing `.filename`; required on 33 | `file` | 40 |
| card `input[type=text]` | `text` | 19 |
| card `textarea` | `text` | 17 |
| card radios in `ul[data-qa=multiple-choice]`, option text in `.application-answer-alternative` | `choice` | 25 |
| card checkboxes in `ul[data-qa=checkboxes]` | `multi_choice` | 13 |
| card `select` | `choice`, by option text | 12 |
| card `input[type=file]` | `file` | 3 |
| `pronouns`: checkboxes, and a hidden custom text input of the same name, left out | `multi_choice` | 8 |
| `opportunityLocationId`: `select` valued by location id | `choice`, by option text | 4 |
| `eeo[gender\|race\|veteran\|disability]`: `select`, or radios for race on 3; an option's text can differ from its value | `choice`, by option text | 19 |
| `eeo[disabilitySignature]`, `eeo[disabilitySignatureDate]` (placeholder `MM/DD/YYYY`) | `text`, `date` | 7 |
| `surveysResponses[…]`: radios and checkboxes, as cards | `choice`, `multi_choice` | 5 |
| `consent[store\|marketing]`: one checkbox beside a hidden `0` | `choice`, `Yes`/`No` | 10 |
| `comments`: `textarea`, labelled by its placeholder | `text` | 1 |

"Apply with LinkedIn" (`.awli-application-row`) is on every posting and never used.

## Filling

- Attaching the resume uploads it and Lever parses it ("Analyzing resume…", then "Success!" or "Couldn't auto-read resume."), filling fields it reads. The resume goes first and is waited for, so the answers overwrite the parse.
- The location is typed and Lever's first suggestion clicked; a typed value that is not a pick is not the field's value.
- Radios and checkboxes are checked by their option text; selects pick by option text.
- The disability signature date is typed as `MM/DD/YYYY` and read back as `YYYY-MM-DD`.

## Read-back and submit

- Nothing but the resume reaches Lever before submit, so the read-back is the page.
- Every form carries an hCaptcha (`#h-captcha`), run on submit (`#btn-submit`); the form then posts to `…/apply` and Lever shows `…/thanks`, the evidence. Not observed on a send yet.
- hCaptcha renders two `hcaptcha-enclave.html` frames in `#h-captcha`, both titled "Widget containing checkbox for hCaptcha security challenge" and hidden. A challenge makes one of them visible, fixed over the whole page: any visible frame there, with no thanks page, is `blocked: captcha`. Seen on 2 sends (2026-10-28).
