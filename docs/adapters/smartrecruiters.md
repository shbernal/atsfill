# SmartRecruiters

Observed on live postings, last on 2026-10-24. The fixture page is [tests/pages/smartrecruiters/apply.html](../../tests/pages/smartrecruiters/apply.html), served behind a job page in `tests/test_smartrecruiters.py`.

## Where the form is

- Job pages: `jobs.smartrecruiters.com/<employer>/<job id>-<slug>`. Their "I'm interested" link opens the One Click form, `jobs.smartrecruiters.com/oneclick-ui/company/<employer>/publication/<uuid>` (also served on `www.smartr.me`), which atsfill follows. The double-send key is `https://jobs.smartrecruiters.com/<employer>/<job id>`, or the form's URL when that is what was given.
- A job page with no One Click link is read as `posting_gone`.
- The form's host answers 403 (Cloudflare) to anything but a browser, so there is no request read. The public Posting API (`api.smartrecruiters.com/v1/companies/<employer>/postings/<id>`) answers but carries no questions.
- The page loads DataDome's tag; it has not stopped a dry run.
- OneTrust's cookie banner would cover the form; the blocker's lists hide it.

## Reading the form

The form is two pages, an Angular app over SAP's Spark web components (`spl-*`, open shadow roots):

1. **Profile**: name, email twice, city, phone, experience, education, social profiles, resume, a message, then Next.
2. **Questions** (`…/screening`): the employer's screening questions, then the consents, then Submit.

The profile is read from the page: which of its sections show and which are required varies by posting, and the `/config` endpoint does not say (it lists education where none shows). The questions and consents come from the `window.__OC_CONTEXT__` the page embeds (`screeningConfiguration`, `consent`), read as the script's text since page globals are out of reach, so `browser_read` lists both pages without leaving the first. Question ids are SmartRecruiters' question UUIDs; a question with several fields (EEO) gives `<uuid>.<field>`; consents are `consent.<scope>`.

Each rendering and on how many of 14 postings (2026-10-20):

| Page | Rendering | Field type | Postings |
|---|---|---|---|
| profile | `spl-input` (names, email and its confirmation, social profiles) | `text` | 14 |
| profile | `spl-textarea`, message to the hiring team | `text` | 14 |
| profile | `spl-autocomplete` city search, required on 6 | `typeahead` | 14 |
| profile | `spl-phone-field`: a country `spl-select` and a national number, required on 6 | `phone` | 14 |
| profile | `spl-dropzone`, resume, required on 6 | `file` | 14 |
| profile | `oc-avatar` photo, an image upload, optional | `file`, id `photo`, a known gap | 5 |
| profile | Easy Apply: a parsing dropzone, LinkedIn, Indeed, SEEK | not used | 14 |
| questions | `info`, text only | not listed | 5 |
| questions | `text` in `spl-input` | `text` | 11 |
| questions | `textarea` | `text` | 1 |
| questions | `currency`, an `spl-number-field` with its currency beside | `text`, a number | 1 |
| questions | `radio`, `spl-radio-group` | `choice` | 13 |
| questions | `select`, `spl-autocomplete` showing its options on focus | `choice` | 10 |
| questions | `select` with `multipleChoice`, `spl-multiselect-autocomplete` | `multi_choice` | 4 |
| questions | `checkbox`, one "Confirmed" box | `choice`, `Yes`/`No` | 4 |
| questions | `eeo`, gender and ethnicity selects in one question | one `choice` per select | 1 |
| questions | consent boxes (`SINGLE`, or `RECRUIT`, `CRM`, `TEXTING`, `WHATSAPP` under a "Select all") | `choice`, `Yes`/`No` | 12 |
| questions | a consent notice with no box | not listed | 3 |

Diversity questions (5 postings) are ordinary questions flagged `diversity`, shown inline.

**Conditional questions** (4 postings): `conditionalConfigurations` shows a question only when another is answered with given options. Such a question has no container on the page until then. Its `shown_when` is `Is` the trigger's field on those options' labels (a box's on `Yes`), `AnyOf` them where several configurations show it; its `required` is its own. Once filled, the questions page's containers are the fields it shows; a container matching no listed question counts as unlisted.

Each question renders as a `[data-test=question-container]` in `sr-screening-questions-form`'s shadow root, in definition order, hidden questions left out. A text, select or checkbox carries `question_<uuid>` as id or name; a radio group or an EEO pair does not and is matched as the next question of its type.

## Filling

- Text: typed into the inner `input`/`textarea`.
- City: typed, then the first `spl-select-option` starting with the answer is clicked. Some postings label the same field "Postal code / zip code" from the start: it is still `location`, its label says what to type, and a city there finds no suggestion (`20001` lists "20001, Washington, DC, USA"). Clicking anything else (an empty search's last row) switches the field to a country search and a postal code; atsfill presses Escape instead, and the city reads back empty.
- Phone: typed with its dial code, which sets the country (`+1…` is US).
- Resume: set on the profile's own dropzone, never Easy Apply's, which parses the file and fills the profile. Attaching uploads it at once (`POST …/oneclick-ui/api/company/<id>/attachment/resume`).
- Next validates the profile in the browser and sends nothing. A click right after the last answer can go unheeded; atsfill clicks up to three times. The footer's buttons are named in the posting's language ("Suivant", "Siguiente"), so they are found by `data-test`: Next is `footer-next`, Submit `footer-submit`. Only `footer-next` is clicked: Submit is never pressed by `fill`.
- Select: typed to filter, then the `spl-select-option` whose `value` is the option's id is clicked; a multi-select repeats this per value.
- Radio: the `spl-radio` whose `value` is the option's id. Checkbox and consents: the box is clicked when its state differs. Consent boxes are matched to scopes by order, after the "Select all" box when there is one.
- The page validates social profiles: LinkedIn must be a linkedin.com URL, Facebook and X a profile or handle. An invalid one keeps Next from going on, and every question reads back empty.

## Read-back and submit

- Nothing but the resume reaches SmartRecruiters before submit. The profile's read-back is taken from the page just before Next: input values, the city's picked suggestion, the phone field's `value` JSON (`internationalFormat`), the attached file's name. The questions page is read after filling: input values, the checked radio's `label`, the multi-select's `value` (option ids, comma separated, mapped back to labels), each box's state.
- Submit has not been observed. The evidence is the URL the page moves to after Submit; DataDome and SEON integrations show in the context's features and may stop it.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `entries` | profile Experience, Education: "Add" opens an entry sub-form (title, company, dates) | `unsupported_rendering` | 4 |
| `attachments` | `files` question, additional attachments | `unsupported_rendering` | 2 |
| `languages` | `languages` question, repeatable language and level | `unsupported_rendering` | 2 |
| `photo` | profile photo (`oc-avatar`), an image upload | `unsupported_rendering` | 5 |
