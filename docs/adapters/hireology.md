# Hireology

Hireology's careers sites, observed on live postings, last on 2026-10-20. The fixture page is [tests/pages/hireology/description.html](../../tests/pages/hireology/description.html) with its form API answer [form.json](../../tests/pages/hireology/form.json), served as `careers.hireology.com` and `api.hireology.com` in `tests/test_hireology.py`: one example of each rendering below.

## Where the form is

- Every employer is on `careers.hireology.com`. A job is `/<careers site>/<job id>/description`; the same job can be listed under a parent's and a location's careers site, so the double-send key is the form API URL, by job id.
- The job page renders the form below the description, from `api.hireology.com/v2/public/application_forms/<job id>`: a public API, no session, listing the template's sections, fieldsets and fields with each one's type, `required`, `maxlength`, options and, for a custom question or the work authorisation question, its label. `request_read` fetches it with a request, `browser_read` from the browser as the page does, and both list the fields from it: parity holds by construction.
- Apply Now only scrolls to the form. A closed posting has no form.
- Nothing reaches Hireology before Submit: an attached file stays in the page.

## Reading the form

A field's id is its API id (`first_name`, `custom_<question slug>`). Every field renders as one element whose DOM id is `<field id>-<n>`, a radio group's and a month's wrapper included; `n` is 0, 1 for the education entry's school fields. Labels for the standard fields are the page's, kept in the adapter, since the API names them by id only; fields of the work history, education and military sections are prefixed with the section. A field's API `value` is what the page shows picked before any answer (the referred question's No, a state, the SMS box ticked): its `default`. Once filled, the fields shown are those whose control the page holds.

Each rendering, and in how many of 166 postings' configurations it shows (2026-10-20):

| Rendering | Field type | Postings |
|---|---|---|
| text input (`text`, `email`): name, email, address, referrer, company, supervisor, signature | `text`, `maxlength` | 166 |
| `tel` input, plain | `phone` | 166 |
| checkbox (`sms_opt_in` checked by default, `current`, `currently_serving`), clicked through its `label[for]` | `choice` `Yes`/`No`, optional: a clear box is an answer | 166, 36, 7 |
| native `select`: state (codes), compensation type, start timeframe, education, EEO, `legal`, yes/no questions | `choice`, the option's text | 164 |
| radio group (`<id>-yes-0`, `<id>-no-0`, the custom radio drawn over the input, so its label is clicked) | `choice` | 142 |
| file (`resume`, `cover`): `.pdf`, `.doc`, `.docx`; once attached the button gives way to the name (`[data-qa-id=careers-filename]`) | `file` | 163, 14 |
| `textarea`: custom questions (always free text, 255 characters), explanations (2048) | `text` | 63 |
| native date input (`earliest_availability`), `YYYY-MM-DD` | `date` | 34 |
| month (`start_date`, `end_date`, military dates): an `MM` and a `YYYY` input in a wrapper | `text`, label `(MM/YYYY)` | 39 |
| a field shown once another is answered: the referrer's name (referred Yes), the school fields (any education but Did Not Complete High School), military details (served Yes), non-compete, relocation, school work, activities and honours explanations | its `shown_when`, `Is` the question on `Yes`, the school fields `Is` the education not on Did Not Complete High School (hidden until one is picked); the API's `required` kept | 139, 20, 7, ≤5 each |
| a repeatable fieldset (compensation, "Add another amount"), a work history section shown three times | the first entry only | 20, 1 |

A type outside this table, or a field the API lists and the page does not show, is listed as `unhandled_rendering`.

The `select`'s value holds only when React sees a native `change`: Playwright's `select_option` leaves the value empty, so the option is set through the element's value setter and a `change` event.

## Filling

- Fields are filled in the API's order, so a question comes before the fields it shows; a shown field is waited for briefly, and skipped when the answer keeps it hidden.
- Text, phone and date inputs are filled; a month's answer `MM/YYYY` goes into its two inputs; a radio or a checkbox is clicked through its label.

## Read-back and submit

- The read-back is each present control: its value, a select's chosen text, the checked radio's label, a box as `Yes`/`No`, a month as `MM/YYYY`, a file as the name the page shows.
- Submit is `#submit`; the evidence is the answer to the first POST on `api.hireology.com/v2/public/`. No send has been made through this adapter, so the endpoint and the page after a send are unobserved; whether entries after the first, or a shown field the API marks required, are checked on submit is unknown: such a field keeps the API's `required`, unverified.
