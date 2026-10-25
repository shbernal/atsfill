# Greenhouse

Observed on live postings, last on 2026-10-25. The fixture page is [tests/pages/greenhouse/job.html](../../tests/pages/greenhouse/job.html), served as Greenhouse's hosted page and an embedding employer page in `tests/test_greenhouse.py`.

## Where the form is

- Job boards: `job-boards.greenhouse.io`, `boards.greenhouse.io` and their EU hosts (`job-boards.eu.greenhouse.io`), at `/<board>/jobs/<id>`, the form on the job page itself. The double-send key is `https://job-boards.greenhouse.io/<board>/jobs/<id>`.
- `grnh.se/<code>` short links 301 to a job board posting, `gh_src` added; the adapter is the one the landing URL names.
- An employer page with `?gh_jid=<id>` loads Greenhouse's embed, `iframe#grnhse_iframe` on `job-boards.greenhouse.io/embed/job_app?for=<board>&token=<id>`. atsfill opens that URL itself, keeping `gh_src`. A posting that is gone shows the embed without a form.
- Many employer career sites list Greenhouse jobs on pages of their own with no embed, `?gh_jid=` in the URL all the same: a form the employer built, or another system's apply flow (Phenom's `/apply?jobSeqNo=`, behind a Cloudflare challenge on a first visit). Those are not claimed.

## Reading the form

The Job Board API answers without a key: `boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>?questions=true` (EU boards too). Its `questions` give the field names the page uses as input ids, a file's paste-in textarea second (`resume_text`, `cover_letter_text`), and its `location_questions` the location, `candidate-location` on the page beside hidden latitude and longitude. Its other sections do not map to the page for certain, so `request_read` returns `None` on a posting with any of them and `read` falls back to the browser read:

- `education` and `data_compliance` consents: the page adds education rows (`school--0`, …) and consent boxes the API does not list;
- `compliance` (US EEOC): the page shows race only once Hispanic/Latino is answered No;
- `demographic_questions`: rendered under their own ids.

On the page, `form#application-form`, each field rendering and on how many of 18 postings' pages (2026-10-19):

| Page | Field type | Postings |
|---|---|---|
| text input | `text` | 18 |
| textarea | `text` | 9 |
| `input[type=number]` (education years) | `text`, filled only with a number | 4 |
| `fieldset.phone-input`: a Country react-select and `input[type=tel]#phone` running intl-tel-input | one `phone`, answered with its dial code | 18 |
| react-select (`role=combobox`) whose options show on opening | `choice` | 18 |
| react-select taking several values (`.select__value-container--is-multi`) | `multi_choice` | 2 |
| react-select that suggests only as you type (location, some schools) | `typeahead` | 12 |
| `fieldset.checkbox` with a box per option | `multi_choice` | 6 |
| a lone checkbox (GDPR consent, an employment row's "Current role") | `choice`, `Yes`/`No` | 3 |
| `.file-upload` group, with an "Enter manually" textarea behind a button | `file` and `text` alternatives, `one_of` the file's id | 18 |

Sections built of these: employment rows (company, title, month selects, four-character year inputs) on 2, education rows on 5, US EEOC selects on 9, demographic selects with a consent box on 1. A radio input has not been seen; one is listed as `unhandled_rendering`.

Required fields carry `aria-required="true"`. Labels hold a `*` marker hidden from screen readers.

An education row's School, Degree and Discipline selects open on the first page of the board's `boards-api.greenhouse.io/v1/boards/<board>/education/{schools,degrees,disciplines}` (100 per page, `meta.total_count`; schools run to thousands) and load the rest as you type. Their options are read from every page of that endpoint, falling back to the opened menu when it does not answer.

The form is server-rendered and goes live a second or two later. Until then a select does not open and late sections (the consent box) are missing, so the read waits until the first select opens.

## Filling

- React-selects: type, then move the focus (`.select__option--is-focused`) to the option with the exact text with ArrowDown and press Enter, which every select takes. Once typed into, a select's options ignore a hover.
- The phone is typed with its dial code: intl-tel-input picks the country from it (by area code too: `+1 514` is Canada), spaces the number and sets the Country select, which then shows only `+1` and a flag.
- Location wants one of its suggestions; the first one for the typed text is taken.
- The resume goes to `#resume`, which an attach replaces with the file's name. Attaching uploads the file to Greenhouse's S3 at once.
- A resume or cover letter as text: `button[data-testid=<id>-text]` ("Enter manually") adds `textarea#<id>_text` inside the `.file-upload` group, beside the file input, which stays. The textarea carries `aria-required=false`; the group's flag is the pair's. Whether the server keeps the text or a file when it gets both has not been observed: the core never sends both.
- "Autofill my application" is never used.

## Read-back and submit

- Nothing but the files reaches Greenhouse before submit, so the read-back is the page: input values, each select's shown value, the checked boxes' labels, the attached file's name. The phone reads back as the Country select's dial code then the number; it is empty until a country is set.
- Submit runs an invisible reCAPTCHA Enterprise check. The evidence is the redirect to `…/confirmation`, titled "Thank you for applying".
- An emailed OTP can stop the send. Its markup has not been observed, so such a send ends `unknown`.
