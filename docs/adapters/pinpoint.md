# Pinpoint

Pinpoint's career sites, observed on live postings, last on 2026-10-22. The fixture page is [tests/pages/pinpoint/apply.html](../../tests/pages/pinpoint/apply.html), served as `acme.pinpointhq.com` in `tests/test_pinpoint.py`: one example of each rendering below, a small script standing in for React.

## Where the form is

- An employer is on `<account>.pinpointhq.com` or on its own career domain; either way a posting is `/<lang>/postings/<uuid>` and its form `/<lang>/postings/<uuid>/applications/new`, which the adapter opens from the posting page. The adapter claims a URL by that path, on any host. The double-send key is the posting's `/en/` URL on its host.
- The apply page is rendered on the server (Rails, React on Rails). The standard fields are native controls; every other field is a React component whose props sit in a `script.js-react-on-rails-component` JSON next to its empty root, the scripts staying in the page once React has rendered. `request_read` fetches that HTML and its React on Rails props (hydration data), `browser_read` takes the page's form, and one parser lists the fields from both, from the controls outside the components and from the props: parity holds by construction.
- A document is uploaded as soon as it is attached (an Active Storage direct upload to S3): it reaches Pinpoint before the submit.
- The country of the address starts on the visitor's country, guessed by the page: `browser_read` lists it as the country's `default`; `request_read` cannot.
- Some career domains carry the employer's OneTrust banner, whose backdrop would cover the form; the blocker's lists hide it.
- 3 of 35 forms load an invisible reCAPTCHA Enterprise that puts its token in a hidden input; the others none.

## Reading the form

A field's id is its control's attribute (`first_name`, `phone`, `cv`, `country`), `question_<question id>` for a custom question, and `equality_monitoring_<name>` for a diversity question. A label is the control's `label[for]`, a question's title, or a document's label block; a required one carries `required` or the `--required` label class. A question shown only after an earlier answer (`conditionalQuestionParentQuestionId`, `equals` or `not_equals` an option, `true` or `false` for a yes/no) has `shown_when` `Is` that question on the options' labels, negated for `not_equals`, and keeps its own `required`. Its component renders nothing until then: a `not_equals` one stays hidden while the earlier question is unanswered (observed on a live posting). An option is its label with runs of whitespace as one space: some labels end in a space. A control or a question component outside the table below is `unhandled_rendering`.

Each rendering, and in how many of 35 postings it shows (2026-10-22):

| Rendering | Field type | Postings |
|---|---|---|
| `text` or `email` input, `textarea`: names, email, LinkedIn URL, personal summary | `text` | 35; summary 29 |
| `Phonenumberinput`: an intl-tel-input whose country follows the number's `+` prefix | `phone` | 35 |
| a Rails date of birth: day, month and year selects named `date_of_birth(3i)`, `(2i)`, `(1i)`, years 1900 to 15 years ago | `date` | 5 |
| consent checkbox `process_information`, required | `choice`, `Yes`/`No` | 35 |
| `Address`: a searchable react-select country, then text lines; with the United States the second line is the city and the town a react-select of states | `choice` country and state, `text` lines | 22 |
| `Filefield`: the résumé, a cover letter | `file` | 34, 3 |
| `Shorttext`, `Longtext`, `Url` questions; `Numberinput`, its label ending `(a number)` | `text`, `url` for `Url` | 29; 5 |
| `Boolean` question: two radios, `true` and `false`, labelled in the page's words | `choice` | 31 |
| `Singleselect` question: a react-select | `choice` | 20 |
| `Multiselect` question: a react-select taking several | `multi_choice` | 9 |
| `Dateselect` question: day, month and year selects | `date` | 4 |
| `Document` and `Multipledocuments` questions | `file` | 4 |
| `Equalitymonitoring::Tsselect`: a diversity react-select, not searchable | `choice` | 28 |
| a question shown after an earlier answer | as its rendering | 23 |

The address lines are listed once each with both layouts' labels: `address1` (Address Line 1, Street Address), `address2` (shown when the country is not the United States, or none is picked) and `city` (when it is), both on the second line, `town` and `state` on the town's input, each with its `shown_when`, `postcode` (Postcode, Zipcode).

## Filling

- A text input is filled. A react-select is opened and the option that is the answer clicked; a state is typed first to narrow the list. No such option is `NotTaken`. A multi-select's menu stays open over the fields below it, and is closed with Escape.
- A yes/no is the click on its option's label, the consent the checkbox set to the answer.
- A date is its day, month and year selects; a year the select lacks is `NotTaken`.
- A document is attached to its component's file input and waited for until its name shows as a tag.
- A question kept hidden by an earlier answer is skipped, after waiting a moment for it to show.
- The address lines of the layout the country does not show are skipped.
- Once filled, the fields shown are the form's controls outside the components, each component that renders a control, and the address lines of the layout shown.

## Read-back and submit

- The read-back is each input's value, a react-select's shown value(s), the checked radio's label, the consent `Yes` or `No`, a date's three selects, and a document's last tag name.
- Submit clicks `Submit Application`, a form POST to `/applications`. The evidence is the page it leads to; the form shown again on `/applications` (a rejected answer) is an error. No send has been made through this adapter, so the page after a send, and what a low reCAPTCHA score does, are unobserved.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `job_preferences` | `Jobpreferences`, an optional second and third job to apply for at once, each a location then a job react-select; left empty | `unsupported_rendering` | 3, never required |
