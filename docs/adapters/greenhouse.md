# Greenhouse

Observed on live postings, last on 2026-10-18; sends were made by hand through the same page on 2026-10-13. The fixture page is [tests/pages/greenhouse/job.html](../../tests/pages/greenhouse/job.html), served as a job board and an embedding company page in `tests/test_greenhouse.py`.

## Where the form is

- Job boards: `job-boards.greenhouse.io`, `boards.greenhouse.io` and their EU hosts (`job-boards.eu.greenhouse.io`), at `/<board>/jobs/<id>`, the form on the job page itself. The double-send key is `https://job-boards.greenhouse.io/<board>/jobs/<id>`.
- A company page with `?gh_jid=<id>` loads Greenhouse's embed, `iframe#grnhse_iframe` on `job-boards.greenhouse.io/embed/job_app?for=<board>&token=<id>`. atsfill opens that URL itself, keeping `gh_src`. A posting that is gone shows the embed without a form.
- Many company career sites list Greenhouse jobs on pages of their own with no embed; those are not claimed.

## Reading the form

The Job Board API answers without a key: `boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>?questions=true` (EU boards too). Its `questions`, `location_questions`, `compliance` (US EEOC) and `demographic_questions` give the field names the page uses as input ids, except:

- the phone's Country select (`country`), which every page shows and the API does not list;
- the location, `location` in the API and `candidate-location` on the page;
- the hidden latitude and longitude;
- education rows (`school--0`, `degree--0`, …) and GDPR consent boxes, which the page adds when the API's `education` or `data_compliance` asks for them. `read` then falls back to the page.

On the page, `form#application-form`:

| Page | Field type |
|---|---|
| text input, textarea | `text` |
| `input[type=tel]#phone` | `phone` |
| react-select (`role=combobox`) whose options show on opening | `choice`, `multi_choice` when multi |
| react-select that suggests only as you type (location, some schools) | `typeahead` |
| `fieldset.checkbox` with a box per option | `multi_choice` |
| a lone checkbox (GDPR consent) | `choice`, `Yes`/`No` |
| `.file-upload` group | `file` |

Required fields carry `aria-required="true"`. Labels hold a `*` marker hidden from screen readers.

The form is server-rendered and goes live a second or two later. Until then a select does not open and late sections (the consent box) are missing, so the read waits until the first select opens.

## Filling

- React-selects: type, then hover the option with the exact text and press Enter. Country saves only on Enter, and its options carry the dialing code (`France +33`).
- Location wants one of its suggestions; the first one for the typed text is taken.
- The resume goes to `#resume`, which an attach replaces with the file's name. Attaching uploads the file to Greenhouse's S3 at once.
- "Autofill my application" is never used.

## Read-back and submit

- Nothing but the files reaches Greenhouse before submit, so the read-back is the page: input values, each select's shown value, the checked boxes' labels, the attached file's name. Country shows only `+33` and a flag; the flag (`iti__fr`) maps back to the option, since `+1` alone does not tell the United States from Canada.
- Submit runs an invisible reCAPTCHA Enterprise check. The evidence is the redirect to `…/confirmation`, titled "Thank you for applying".
- An emailed verification code can stop the send. Its markup has not been observed, so such a send ends `unknown`.
