# Paylocity

Paylocity Recruiting's public apply page, observed on live postings, last on 2026-10-24. The fixture page is [tests/pages/paylocity/apply.html](../../tests/pages/paylocity/apply.html), served as `recruiting.paylocity.com` in `tests/test_paylocity.py`: a two-step form with one example of each rendering below.

## Where the form is

- Every employer is on `recruiting.paylocity.com`. A job is `/Recruiting/Jobs/Details/<id>`, its form `/Recruiting/Jobs/Apply/<id>`; the double-send key is the details URL.
- The apply page carries the form's configuration inline as `window.pageData`: the sections (`customJobApplication.sections`, each field `isIncluded`, `isRequired`), the screener questions, the option lists (`statesOptions`, `genderOptions`…). It is served without a session, so `read_api` reads the page over HTTP and `read_form` reads the same script from the loaded page (Camoufox's page scripts do not see `window`), through one function: parity holds by construction.
- The form is one page with Submit, or a wizard of steps (`Step 2 of 5`): candidate information, screener questions, references, EEO, then a last step with the conviction and work authorisation questions, the acknowledgement and Submit. Only the current step is in the page; the last step repeats earlier answers in `<p>`s with the controls' ids.
- Next (`[data-automation-id=btnNext]`) posts the whole application to `ValidateApplication` (no id is assigned) and the address to an address check; nothing reaches the employer before Submit. Next stays on the step when an answer is refused.
- A posting with `requireResume` opens a modal asking for a resume first (`#btn-forceResume`). Any resume is uploaded at once (`FileUpload?parse=true`) and parsed into the form; a resume Paylocity cannot parse leaves a banner and the form empty. On a page without the modal, the "Fill out application with my resume" box is unchecked before the upload.
- OneTrust's cookie banner would cover the bottom of the page; the blocker's lists hide it.

## Reading the form

A field's id is its control's DOM id (`info.firstName`, `workHistory.companyName.0`), `resume`, `coverLetter`, `additionalFiles`, or `screener.<question id>`. Labels are the page's, a reference's prefixed `Reference 1: `; a field shown only after another's answer (worked here before, may we contact the supervisor, graduated: `Yes`) has that `shown_when`. Once filled, the fields shown are the listed ones each step showed.

Each rendering, and in how many of 60 postings' configurations it shows (2026-10-20):

| Rendering | Field type | Postings |
|---|---|---|
| `input`, `textarea` by id (name, email, LinkedIn, referred by, company website…) | `text` | 60 |
| mobile, home, company, reference phones: plain text inputs | `phone` | 60 |
| react-widgets dropdown (`.rw-dropdownlist`, list `[id=<id>__listbox] [role=option]` once clicked): Yes/No questions (the SMS consent's Yes reads `Yes*`), school type, degree, EEO, reference type, years known | `choice` | 60 |
| address block (`public-site-address-*`, a work entry's `public-candidate-work-history-address-0-*`): line 1 is a suggestion combobox that drops keys while it loads, county, city, zip, line 2 | `text` | 60 |
| state in the address block: a `pcty-input-select` listing codes (`[role=listitem][title=DC]`) | `choice`, the code | 60 |
| radio group by `name` (how did you hear) | `choice` | 54 |
| date (`info.dateAvailableToStart`, prior employment dates): a native date input, `MM/DD/YYYY` typed | `date` | 43 |
| month (`txt-workHistory-startDate-0`, end date, `txt-educationHistory-graduationDate-0` once graduated): `MM/YYYY` typed | `text`, label `(MM/YYYY)` | 53 |
| tags input (`.react-tagsinput`, Enter adds one): skills, certifications | `text`, one tag | 45 |
| file: resume (`#btn-resume`, the modal's `#btn-forceResume`), cover letter, additional files; the name shows once uploaded | `file` | 60 |
| entries: work history, education (one shown when the section `isOneRequired`), references (`referencesRequiredCount` shown when `referencesRequired`) | the entry's fields, index 0… | 19, 16, 35 required |
| optional entry sections; their section-level certifications field still shows | left out | 35, 35 |
| desired salary: one text (`desiredSalaryInputType` 2), or minimum and maximum (1) after a salary type dropdown (`info.desiredSalaryType`, Hourly or Yearly), required with the salary | `text`, the type `choice` | 35 |
| screener: radios or checkboxes (`multiQuestionAnswer<id>_<n>`, labelled by `label[for]`), text (`screener-question-<n>-textarea`) | `choice`, `multi_choice`, `text` | 44 |
| acknowledgement checkbox (`#applyAcknowledgement`), currently working here | `choice`, `Yes`/`No` | 60 |
| felony, misdemeanor questions: shown only when `priorConviction` is on the form; supervisor fields only when `mayWeContact` is | `choice`, `text` | 15, 27 |

A field of a shown section outside this table is listed as `unhandled_rendering`, as is a screener question of another type. The acknowledgement, the E-Verify message and the conviction switch in the configuration are no field of their own.

## Filling

- The resume is uploaded first: a parsed resume fills fields, which the answers then override.
- A step is filled in field order, each answer whose control is on the step (a control, not the last step's copy), so a question comes before the fields it shows. Then its read-back is kept, and Next is clicked until the last step.
- Text is filled; address line 1 is typed and checked, refilled when keys were lost; dates and months are typed into their masks.
- A dropdown answer is its option's text without the `*` mark.

## Read-back and submit

- The read-back is each step's controls, read before leaving it: input values, a dropdown's shown text, the checked radio or boxes, a date as `YYYY-MM-DD`, a tag's text, a file's name as listed.
- Submit is `[data-automation-id=btnSubmit]`; the evidence is the URL the page leaves for. No send has been made through this adapter, so the end of a send is unobserved.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `country` | country in the address block, United States until changed | `unsupported_rendering` | 39 |
| `expanded_identity` | expanded identity questions (gender identity, pronouns…) | `unsupported_rendering` | 30 |
| `date_of_birth` | date of birth | `unsupported_rendering` | 1 |
| `salary_history` | a work entry's starting and ending salaries | `unsupported_rendering` | 2 |
| `reference_address` | a reference's address | `unsupported_rendering` | 1 |
| `gpa` | an education entry's GPA | `unsupported_rendering` | 1 |
