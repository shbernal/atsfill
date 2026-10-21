# Breezy HR

Observed on live postings, last on 2026-10-21. The fixture page is [tests/pages/breezy/apply.html](../../tests/pages/breezy/apply.html), served as a position's application page in `tests/test_breezy.py`: one example of each rendering below.

## Where the form is

- Positions: `<employer>.breezy.hr/p/<position id>-<slug>`; some employers serve the same portal on their own domain, detected from the page by Breezy's portal scripts. The form is `…/apply`, which atsfill opens. The double-send key is `https://<host>/p/<position id>`.
- A closed position redirects to the employer's openings, with no form: `posting_gone`.
- Two page engines serve the portal, an AngularJS one and a React one (about half each); the React one copies the AngularJS markup, `ng-model` attributes included, so one reading covers both. The form's configuration sits in the page (`#portal-data` JSON on React, `data-position` and `#questions` on AngularJS), in two shapes and without the EEO and consent sections on AngularJS, so there is no `read_api`.

## Reading the form

A field is a named control and the name is its id (`cName`, `section_<section>_question_<n>`, `race_ethnicity`); its label is the `h3` before it, a lone checkbox's its own text. Required ones carry a visible `span.required` in the heading or a `required` attribute. The configuration's `headline` and `profile_photo` are never rendered on the form.

Each rendering, and on how many of 200 postings from 115 employers (2026-10-21):

| Rendering | Field type | Postings |
|---|---|---|
| `cName`, `cEmail`: text, email input | `text` | 200 |
| `cPhoneNumber`: text input | `phone` | 200 |
| `cResume`: hidden file input, "Upload Resume" opens it; required on 167 | `file` | 199 |
| `smsConsent`: checkbox under the phone, labelled by its text | `choice` `Yes`/`No` | ~190 |
| `cAddress`: text input with Google Places attached; no suggestions showed in the browser, and the page's own address check is never called, so a typed address stands | `text` | 105 |
| `cSummary`, `cCoverLetter`: `textarea` | `text` | 138, 142 |
| work history, education: "Add Position" / "Add Education" adds an entry of unnamed inputs (`ng-model` `candidatePosition.*`, `candidateSchool.*`: company or school, title or field of study, two `date` inputs, summary); when required Breezy checks the first entry's first two | `text`, `date`, the first entry only | 138, 142 |
| desired salary: currency `select` (US Dollar preselected), `cSalary` text, period `select` without a name (`ng-model` `candidate.salary.period`) | `choice`, `text`, `choice` | 78 |
| `cLocation`: `select` of the position's locations | `choice` | 25 |
| question `select` (dropdown), a blank option first | `choice` | 37 |
| question radios (multiple choice), option text in `li.option` | `choice` | 101 |
| question checkboxes, one per option, the same name; a single one for an acknowledgement on 7 | `multi_choice` | 38 |
| question text input, `textarea` (paragraph) | `text` | 76, 45 |
| question `date` input | `date` | 2 |
| question file: hidden file input behind "Upload File" | `file` | 1 |
| reference check: two or three `li.reference` of name, email, phone and a Professional/Personal radio, only the name input named; ids `<question>.reference<n>.<part>` | `text`, `phone`, `choice` | 3 |
| video question (Ziggeo recorder): not filled; required is `form_shape` | none | 1 |
| EEO radios: `race_ethnicity`, `gender`, `eeoc.veteran_status`, each with a "don't wish to answer" option, required by the page | `choice` | 52 |
| `gdprAgreement`, `ccpaAgreement`: checkbox, required | `choice` `Yes`/`No` | 8, 16 |
| a questionnaire of more than one section: the form goes in steps (`[data-section]`), Continue checks a step's required answers before showing the next | | 12 |

Breezy can send a step to another on an answer (`next_section` actions); not seen on the page's data, so a step skipped that way leaves its answers unfilled and the read-back shows it.

## Filling

- The step on show is filled, then Continue clicked while answers are left; a step Breezy keeps (a required answer missing) ends the fill there.
- Attaching a file uploads it at once to `app.breezy.hr/api/portal/<employer>/upload` (`…/upload/file` for a question's); the name then shows in its `.file-input-container`, waited for.
- An entry's link is clicked once before its first answer; selects pick by option text, radios and checkboxes are checked by their option's text.

## Read-back and submit

- The read-back is the page: a control's value, a select's option text, the checked options' text, a box as `Yes`/`No`, a file as the name Breezy shows.
- Submit is "Submit Application" (`apply()`), posting to `app.breezy.hr/api/apply/<position id>`; the evidence is that answer. No send has been made through this adapter, so the answer and the page after a send are unobserved.
