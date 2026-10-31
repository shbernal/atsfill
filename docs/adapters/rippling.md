# Rippling

Rippling Recruiting's job boards, observed on live postings, last on 2026-10-31. The fixture page is [tests/pages/rippling/apply.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/rippling/apply.html), served as `ats.rippling.com` in `tests/test_rippling.py`: one example of each rendering below, Rippling's components reduced to their markup and a few lines that act as they do.

## Where the form is

- Every board is on `ats.rippling.com`. A job is `/<board>/jobs/<job uuid>`, a locale prefix (`/en-GB/…`) accepted; its form is at `…/apply`, which `browser_read` opens. The double-send key is the job's URL without the locale or `/apply`.
- The apply page is a Next.js page whose data (`<script id="__NEXT_DATA__">`, `props.pageProps.apiData`) holds the form: the standard questions (`basicQuestions`, by `oid` and `fieldType`), the employer's question groups (`additionalQuestions`, each question by `uniqueKey`, `questionType`, `isRequired`, `strChoices`, `maxChars`, `isOtherEnabled`, `allowComments`), whether the EEO questionnaire is asked (`eeocQuestionnaireEnabledForJobPost`), the voluntary disability form (`showCc305Form`) and video questions (`videoScreeningQuestions`). No session is needed. `request_read` fetches the apply page for this hydration data, `browser_read` reads the data of the page it opened, and both list the fields from it: parity holds by construction. A closed posting's page has no `jobPost`.
- The EEO questions and the SMS consent are not in the data: their labels and options are the same on every form, kept in the adapter, and `browser_read` checks each is on the page.
- A cookie banner (`#transcend-consent-manager`) shows a moment after the form, in a closed shadow root a click cannot reach, and its layer takes the clicks on the lower part of the page. The blocker's lists miss it; the adapter's filter hides it, and nothing is answered to it. The Apply bar is fixed at the bottom of the window, so a control is scrolled to the middle before it is clicked.
- Attaching a file uploads it to Rippling's storage at once (`/api/v1/utility/file_upload_url`, then S3); a résumé is then parsed (`POST /api/v1/board/<board>/jobs/<job>/parse_resume`), and a moment after the response what the parse finds is written into the fields and a "Résumé parsing was successful." notice covers the middle of the page for a few seconds. The résumé is attached first, then the notice waited for and out, so the answers are typed over the parse.

## Reading the form

A field's id is the element's `data-testid`: a standard question's `oid` (`first_name`, `phone_number`), an employer's question `customQuestions.<group id>.<uniqueKey>` (the same question can sit in two groups), an EEO question `eeoc.<name>`, `sms_opt_in`. Pronouns render as `pronouns_strategy`. A question's comment is `<id>_comment`, its "Other" text `<id>_other`. The rendering is read from the element, not from the question type: a `KNOCKOUT` question shows as a dropdown on one form and the same type as boxes on another.

Each rendering, and in how many of 79 employers' forms it shows (2026-10-22):

| Rendering | Field type | Forms |
|---|---|---|
| text input: name, email, current company, links, `SHORT_ANSWER` questions (`maxChars`) | `text` | 79 |
| file (`input[type=file]` in a `label`): résumé and cover letter (`.doc`, `.docx`, `.pdf`), `DOCUMENT_UPLOAD` questions (any type); once uploaded the name shows as a chip, a stem over 15 characters cut to `<first 15>...<extension>`, and whole in a screen-reader status line, "File <name> uploaded successfully" | `file` | 79, 4 |
| phone: a dial-code search box (`+1 US` by default, options `+44 GB - United Kingdom`) beside the number; the code picked by the number's, the longest that is listed, the shown one kept when it fits | `phone` | 79 |
| location: a place typeahead (Google Places) that keeps the suggestion picked | `typeahead` | 79 |
| pronouns: a search box of six choices ("Enter my pronouns", which opens a text box, left out) | `choice` | 72 |
| EEO questionnaire: gender, Hispanic/Latino and veteran status as dropdowns, race as a search box; optional | `choice` | 52 |
| SMS consent: a radio group of two sentences | `choice`, optional | every form surveyed |
| dropdown (`div[role=combobox]`, its list of `role=option` opened by a click): `SINGLE_SELECT_DROPDOWN`, `KNOCKOUT` | `choice` | 17, 23 |
| radio group (`role=radiogroup` of `role=radio`): `SINGLE_SELECT_RADIO` | `choice` | 22 |
| boxes (`role=group` of `role=checkbox`): `MULTI_SELECT_CHECKBOX`, also when its choices are Yes/No | `multi_choice` | 7 |
| "Add comment" (`allowComments`): a button that gives way to an optional textarea | `text`, optional | 19 |
| "Other" (`isOtherEnabled`): a last choice whose pick shows a text box: a second element of the same `data-testid` under radios, `<id>_others` under boxes | `choice` with `Other`, then `text` `shown_when` it is Other, required as its list is (the box marked `*`) | 9 |
| `textarea`: `LONG_ANSWER` | `text` | 10 |
| four-step scale (`YES_NO_SCALE_4`): a radio group named by `aria-label` (Strong No, No, Yes, Strong Yes) | `choice` | 6 |
| date (`DATE`): `MM`, `DD` and `YYYY` inputs | `date` | 3 |

A type outside this table, or a field the data lists and the page does not show, is listed as `unhandled_rendering`. A hidden `aiOptOut` input is not a field.

A form's data carries a `skipLogic` list, empty on all 25 forms read on 2026-10-31. Once filled, the fields shown are the page's `[data-testid=field]` blocks, so a question skip logic showed would stop the fill as not listed.

Out of scope: a video question (`videoScreeningQuestions`, recorded in the page with the microphone), `out_of_scope: video.<id> is a video recorded live`. None was seen.

Not observed: the voluntary disability form (`showCc305Form`), listed as `unhandled_rendering` until it is.

## Filling

- The résumé first, then the fields in the page's order. Text, textarea and date parts are filled; a dropdown or a search box is opened and its option clicked; a radio, a box or a scale step is clicked; a place is typed and the first suggestion picked once the list has stopped changing (each key typed renders its own suggestions, replacing the shown ones); a comment's button is clicked before its textarea is filled.

## Read-back and submit

- The read-back is each present element: an input's value, a dropdown's text (none while its placeholder shows), the checked radio's text or `aria-label`, the checked boxes, a date's parts as ISO, a file's name from its status line (its chip without one), a phone as `<dial code> <number>`.
- Submit is the Apply button (`[data-testid=Apply]`); the evidence is the answer to the first POST on `ats.rippling.com/api/…/apply`. The page carries a Cloudflare Turnstile site key. No send has been made through this adapter, so the endpoint, whether Turnstile challenges the send, and the page after a send are unobserved.
