# Oracle

Oracle Recruiting Cloud's Candidate Experience, observed on live postings, last on 2026-10-20. The fixture pages are [tests/pages/oracle/](../../tests/pages/oracle/), served as an employer's site in `tests/test_oracle.py`: the email step, and one single-page form with one example of each rendering below.

## Where the form is

- Each employer has its own tenant, `<tenant>.fa.<region>.oraclecloud.com` (`fa.ocs.oraclecloud.com`, `fa.oraclecloud.com` too). A job is `/hcmUI/CandidateExperience/<lang>/sites/<site>/job/<id>`; the same requisition shows under several sites, so the double-send key is `https://<tenant host>/hcmUI/CandidateExperience/job/<id>`.
- Applying starts at `<job>/apply/email`: an email address, on most tenants the employer's terms (`legal-disclaimer`, a checkbox styled over a hidden input), and Next (`button[type=submit].apply-flow-pagination__button`). Past it, `<job>/apply/section/1`. Some tenants first offer to import a LinkedIn profile (`/easy-apply`); its "manually" link (`.quick-apply-flow__manually--link`) goes on to the form.
- An address the tenant has seen before, even once on the email step, gets "Confirm Your Identity" (`.apply-flow-pin-code-required`) instead: a six-digit code is emailed to it. atsfill stops with `blocked`, `emailed_code`. A user's address meets it on every employer they applied to or dry-ran before; the test persona takes a new address each run.
- Some tenants put an hCaptcha on the email step: `blocked`, `captcha`.
- The form's metadata (`recruitingCECandidateProfileBlocksMetadata`, `recruitingCEQuestions`) answers 401 until the email step is passed, so there is no `read_api`. Reading passes the step with a throwaway `atsfill.read.<random>@example.com`; filling clears cookies and storage and passes it again with the answer's address.
- From the email step on, Oracle keeps a draft (`POST recruitingCEJobApplicationDrafts`, then a `PATCH` as fields change); a picked file is attached to it at once.
- A flow is one page with Submit (`.apply-flow-pagination__submit-button`), or several pages with Next and page numbers (`.apply-flow-navigation-pages__link`). Every page stays in the document, the others hidden. The page numbers move to any page without checking the page left, unlike Next, so atsfill reads and fills page by page through them; Submit is on the last page.

## Reading the form

- The form is a list of `apply-flow-block`s, the kind in the class of its child (`apply-flow-block--disqualification-questions`…). A required block's title has `apply-flow-block__title--required`.
- A field is an innermost `.input-row`. Its id is its label's DOM id without the counter Oracle appends on each load: `labelText-lastName-14` is `lastName`, a question `labelText-300005169285088-8` is `300005169285088`; a row without a `labelText-` span uses its label's `for` (`country-12`, `city-33`). Required: `input-row__label--required`, or `aria-required`/`required` on the control.
- The email row in the form repeats the email step's address; it is read back as `email`.
- Dropdowns are `input[role=combobox]` whose list (`[role=grid]` named by `aria-controls`) fills with `[role=gridcell]`s once clicked, and filters as one types. A dropdown still loading is a skeleton (`.cx-select--loading`) that loads only once scrolled into view. Most of the address block appears only after its country has loaded, in that country's format; the country is prefilled from the visitor's location.

Each rendering, and on how many of the 12 forms reached out of 15 postings (2026-10-20):

| Page | Field type | Postings |
|---|---|---|
| `input.input-row__control` text, url, `textarea` | `text` | 12 |
| phone: country code dropdown (`+44 (United Kingdom)`, on some tenants `United Kingdom (+44)`) and `input[type=tel]` | `phone` | 12 |
| `ul[role=radiogroup]` pills (`.cx-select-pill-section`, `--selected`) | `choice` | 12 |
| `.cx-multi-select-pills`, pills in `ul[role=list]` | `multi_choice` | 1 |
| hidden `input.apply-flow-input-radio-control` under `label.apply-flow-input-radio` | `choice` | 5 |
| checkbox list, `.input-row__control-item` inputs with their labels | `multi_choice` | 6 |
| dropdown with a list (country, gender, veteran status, a question) | `choice` | 12 |
| address geography (`.geo-hierarchy-form-element`): state, city, postal code dropdowns, each filtering the others; an item names the levels above it (`0200, Acton, ACT`), and once picked shows only its own (`0200`) | `choice`, the most specific level only; required when any level is | 3 of 10 at the gate |
| dropdown listing only what is typed | `typeahead` | 0 |
| address search in the address block (`addressLine1`, US format): suggestions fill ZIP, city, state and county | `text`, typed and left unpicked | US tenants |
| date: `.datepicker-row`, three dropdowns, month name, day, year | `date` | 2 |
| resume, cover letter (`resume-upload-button`, `cover-letter-upload-button`) | `file`, id `resume`, `coverLetter` | 12 |
| `apply-flow-block--misc-attachments`, a file block listing what is attached (`.misc-attachment-list__item-name`) | `file`, id `block:<block id>` | 4 |
| agreements block: job alerts, marketing opt-in checkboxes | `choice`, `Yes`/`No` | 9 |
| e-signature, full name | `text` | 11 |
| tiles to add (education, experience, timeline, skills, licenses, languages) | not handled | 12, required on none |
| profile import (LinkedIn, Indeed, a resume to parse) | not read | 9 |

  A required tiles block makes the form `unsupported`; an optional one is left out.

## Filling

- Some tenants show a cookie banner (`.cookie-consent`) over the form; it is declined when it covers a click.
- Every click checks what is on top: the app's loading spinner (`.app-loading-spinner`, always in the page, `visibility: hidden` when idle) comes over it while the app loads, and some tenants fix a header over the top of the page, so a covered target is brought to the middle of the window.
- Text is filled, then the field blurred: Oracle's bindings take the value on change.
- A dropdown answer is typed into it and its exact item clicked. Some lists search on part of an item only (a postal code list on its code): when typing the whole answer finds nothing, the item is looked for in the whole list. A phone number picks the longest country code its digits start with (the first country listed under that code) and types the rest; the number reads back as code and number.
- The address search keeps what is typed when no suggestion is picked: picking one would overwrite the ZIP, city, state and county answers.
- The country is filled first: the address below it changes with it.
- Picking an item of the address geography fills the levels above it, so only the most specific level is asked; it reads back as the picked item when the field shows that item's first part.
- A field can show twice (the e-signature on some tenants); it is filled and read back where it shows first.

## Read-back and submit

- The read-back is the page: each input's value, the selected pills, the checked inputs' labels, the dropdown's text, a date's three dropdowns as `YYYY-MM-DD`, a file as the name Oracle shows once attached (`.attachment-upload-button__download`). The terms read back `Yes`: the form opens only once they are accepted.
- Submit is `.apply-flow-pagination__submit-button`; the evidence is the URL the page leaves `/apply/` for. No send has been made through this adapter, so the end of a send is unobserved.
