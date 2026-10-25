# Workable

Observed on live postings, last on 2026-10-22. The fixture page is [tests/pages/workable/apply.html](../../tests/pages/workable/apply.html), served as a posting's application page in `tests/test_workable.py`.

## Where the form is

- Postings: `apply.workable.com/j/<shortcode>`, redirecting to `apply.workable.com/<account>/j/<shortcode>/`; the form is the posting's "Application" tab, `…/j/<shortcode>/apply/`, which atsfill opens. The shortcode is ten hex characters. The double-send key is `https://apply.workable.com/j/<shortcode>`.
- A posting that is gone shows no `form[data-ui=application-form]`, read as `posting_gone`.
- A cookie banner (`[data-ui=cookie-consent]`), a modal dialog in a wrapper styled `--color-cookie-consent`, shows a moment after the page; the blocker's lists hide it on `apply.workable.com`.
- Cloudflare's challenge script runs on the page; it has not stopped a dry run.

## Reading the form

The form API answers without a key: `apply.workable.com/api/v1/jobs/<shortcode>/form` lists the sections (Personal information, Profile, Details) and their fields, each with its id, type and `required`. `request_read` maps it; it declines a posting with a type it does not know.

On the page, every field sits under an element whose `data-ui` is that id, so `browser_read` reads the page: each outermost `data-ui` element of a `[data-ui=section-fields]`, by what it holds. Required fields carry a `<strong>*</strong>`, optional ones "(Optional)". Each rendering and on how many of 15 postings (2026-10-20):

| API type | Rendering | Field type | Postings |
|---|---|---|---|
| `text`, `email` | `input` with an `id` | `text` | 15 |
| `paragraph` | `textarea` | `text` | 10 |
| `number` | `input` with no `id`; keeps digits and `.`, shows thousands with commas | `text`, read back without commas | 6 |
| `phone` | `[data-ui=phone]`: a country button (intl-tel-input, set from the visitor's location) and `input[type=tel]` | `phone` | 15 |
| `file` | `[data-role=dropzone]` holding `input[type=file][data-ui]`: resume on 15, photo (images only) on 5, a question on 2 | `file` | 15 |
| `boolean` | `fieldset[role=radiogroup]`, YES/NO options holding radios valued `true`/`false` | `choice`, `Yes`/`No` | 11 |
| `boolean` (`gdpr`, `onlyTrueAllowed`) | one `[role=checkbox]` whose label links the privacy notice; no `*`, required all the same | `choice`, `Yes`/`No` | 1 |
| `multiple`, `singleOption` | `fieldset[role=radiogroup]`, an option per `[role=radio]`, up to 10 | `choice` | 3 |
| `multiple`, several | `div[role=group]` of `[role=checkbox]` | `multi_choice` | 2 |
| `dropdown` | `[data-input-type=select]`: a read-only combobox opening `#input_<id>_listbox` | `choice` | 4 |
| `date` | `input` with a `MM/DD/YYYY` placeholder and a calendar icon | `date` | 1 |

`address` comes prefilled from the visitor's location ("Paris, France"); hidden `city`, `postcode` and `country` inputs beside it have no `data-ui` and are not fields. "Autofill application" from a resume is never used.

## Filling

- An option's state is its `aria-checked`. Its hidden input is remounted on each render and catches up only on a later one, so options are clicked and read back by `aria-checked`.
- A dropdown is opened and the option with the exact text clicked. Its list stays on the page about 200 ms after closing and takes clicks meant for the field below, so filling waits for it to go.
- The date input takes typed digits in its placeholder's order and adds the slashes.
- The phone is typed with its dial code, which sets the country button; the read-back is the button's dial code then the number.
- Attaching a file uploads it to Workable's S3 (`workable-application-form.s3…`) at once. The dropzone shows its name, a progress bar and an abort button (`[data-action=abort]`) while it uploads, about a second, then a delete button (`[data-action=delete]`). Attaching another file while one uploads empties the one uploading, so filling waits for the delete button, and a file reads back only once it shows.
- A photo opens a crop dialog, confirmed by its `[data-ui=crop-image]` button; the dropzone then shows a preview whose `alt` is the file's name, and uploads it. Attaching the resume afterwards empties the photo, so the resume goes first.

## Read-back and submit

- Nothing but the files reaches Workable before submit, so the read-back is the page.
- Submit runs a Cloudflare Turnstile check (`#turnstile-container-…`), then the page posts `{candidate: …}` to `/api/v1/jobs/<shortcode>/apply`; the evidence is that call's answer. The page then shows "Thank you! Your application has been submitted successfully." Not observed on a send yet.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `entries` | `group` (education, experience): "+ Add" opens an entry (school or title, dates, …) | `unsupported_rendering` | 11, required on none |
