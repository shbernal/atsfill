# Recruiterflow

Recruiterflow's posting pages, observed on live postings, last on 2026-10-28. The fixture page is [tests/pages/recruiterflow/form.html](../../tests/pages/recruiterflow/form.html), served as `recruiterflow.com` in `tests/test_recruiterflow.py`: one example of each rendering below.

## Where the form is

- Every posting is `recruiterflow.com/<employer>/jobs/<id>`, the form under the description on the same page; freehire adds a `?utm_source=` that is dropped. The double-send key is that URL without its query. Its employers are mostly recruiting agencies.
- The page declares the form in one JSON object, `var convertedToJSON = {...}` (`window.applyJobData`), and a React app draws it. `request_read` fetches the page over HTTP, `browser_read` takes the page's HTML, and one parser lists the fields from the JSON: parity holds by construction.
- A posting is open while its `job_visibility_id` is one of `ACTIVE_JOB_VISIBILITY`; a closed one, an unknown id (404) or a page without the JSON is `posting_gone`.
- A file is uploaded as soon as it is attached (`/api/file/generate-file-id`, then `/api/file/upload`): it reaches Recruiterflow before the submit. The résumé is parsed and fills the empty fields, the last name among them.
- A cookie banner sits over the lower questions: the adapter's cosmetic filter hides it.

## Reading the form

The JSON's `application_form` has three lists, drawn in this order: `personal_info`, `candidate_profile` and `questions`. An item's `requirement_type` is `mandatory`, `optional` or `off`; an `off` item is not drawn. `footer_notes`, when set, is drawn above the submit with a checkbox that must be ticked.

A field's id is the key the page posts it under where it has one (`personal_info.email`, `candidate_profile.company-name.0`), a question's `question.<question_id>`. A question's kind is its `view_field_id`, or its `custom_field_view_field_id` for a custom field; its label the `question_text`. The questions are drawn one per child of `.additional-info-inputs-container`, in the JSON's order: a question's control is found by its place there, and `browser_read` fails when the count differs.

Each rendering, and in how many of 43 employers' forms it shows (2026-10-28):

| Section | Rendering | Field type | Forms |
|---|---|---|---|
| personal | Full Name: first and last name inputs | `text` ×2 | 43 |
| personal | Email, LinkedIn Profile: text inputs | `text` | 43, 24 |
| personal | Phone: intl-tel-input, typed in E.164 and shown formatted | `phone` | 42 |
| personal | Location: City, State, Zip code inputs, Country a react-select; all four required when it is | `text`, `typeahead` | 24 |
| profile | Resume, Cover Letter: a drop zone over a file input, the file listed by name once uploaded | `file` | 42, 8 |
| profile | Summary: textarea | `text` | 8 |
| profile | Experience, Education: a list's first entry, two text inputs and a start and end month picker; required, the first three are checked | `text`, months `MM/YYYY` | 28, 9 |
| questions | short text (1), long text (2) | `text` | 11, 11 |
| questions | number (4), money (5): an amount input, the currency select left at its first currency | `text`, labelled `(a number)` | 3, 5 |
| questions | yes/no (9): two buttons, the picked one `selected` | `choice` `Yes`/`No` | 12 |
| questions | single select (10), multi select (11): react-select, its blank and repeated choices dropped | `choice`, `multi_choice` | 8, 2 |
| questions | money range (12), number range (13): Min and Max inputs | `text` ×2, labelled `(a number)` | 1, 0 |
| questions | file (14): a drop zone | `file` | 2 |
| questions | parent-child (19): an accordion per group, a checkbox per item | `multi_choice`, an item by its name, `<group>: <name>` where two groups share it | 1 |
| questions | date (6): a day picker | `date`, typed `DD Mon YYYY` | 0 |
| footer | the footer notes' checkbox (`mandatory-opt-in-checkbox`) | `choice` `Yes`/`No`, required | 10 |

A question of another kind, rich text (3) among them, and a personal or profile item outside this table are `unhandled_rendering`; none was seen.

## Filling

- A section (Personal Information, Candidate Profile, Additional Questions) is a header over its inputs, which some employers' pages draw collapsed (`display: none`, 1 of 10 in the gate's sample): a collapsed one is opened by its header first.
- Files first, so the résumé's parse never overwrites an answer; each is waited for until its upload answers and its name is listed.
- A react-select is opened and the option whose text is the answer clicked; the country is typed first to narrow its long list. A multi select's menu stays open between picks and is closed with Escape.
- A month picker takes `Jan 2020` typed and Tab; a question's day picker `31 Jan 2020`. A yes/no button and a checkbox are clicked. A parent-child item's group is opened by its plus icon and the item's checkbox clicked.
- Number inputs take only digits; the read-back drops the thousands separators an amount shows.

## Read-back and submit

- The read-back is each input's value, a react-select's shown value or values (a multi value by its `Remove <value>` button), the `selected` yes/no button, a checkbox's state as `Yes`/`No`, a file's listed name, a parent-child question's ticked items. It is the page, not the server: nothing but the files reaches Recruiterflow before the submit.
- Submit clicks Submit Application. The page first checks the required fields itself and, when one is empty, posts nothing and shows its message: that is the failure. Otherwise it posts a `FormData` to `/<employer>/jobs/<id>/submit-application`; the evidence is that response's URL and status, unless it is not 2xx or its JSON says `RESULT: FAILURE`, whose `message` is the failure. No send has been made through this adapter.

## Known gaps

None.
