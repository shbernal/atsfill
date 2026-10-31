# HiringThing

HiringThing's career sites, observed on live postings, last on 2026-10-22. The fixture page is [tests/pages/hiringthing/job.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/hiringthing/job.html), served as `acme.hiringthing.com` in `tests/test_hiringthing.py`: one example of each rendering below.

## Where the form is

- Every employer is on `<employer>.<domain>`, where the domain is `hiringthing.com` or one of the white-label domains HiringThing serves for HR and payroll resellers (`oasisrecruit.com`, `prismhr-hire.com` and the others listed in the adapter). A job is `/job/<id>/<slug>`; the double-send key is `/job/<id>` on its host. A host outside the list is claimed from the page, by the props of its `HiringThing.Components.ApplyButtonGroup`.
- The job page is rendered on the server with the posting's configuration in that component's `data-react-props`: the standard fields switched on or off, required or not, and the questions with their type and options. The Apply button, rendered by React, renders the form below it in the page from these props.
- There is no request read: which controls render is the page's logic over the props (the referral source, a country's state list, the history entries' subfields), not the props alone. `browser_read` lists the rendered controls.
- A closed posting answers 404 and has no Apply button.
- The country's state list is fetched from `/regions?parent_region=<country>` once the form shows; until it arrives the state is a plain input, so `browser_read` waits for its combobox.
- A file is posted to `/client_api/add_file` as soon as it is attached: it reaches HiringThing before the submit.
- A cookie banner (`.cookie-banner`) covers the bottom of the page; the blocker's lists hide it.

## Reading the form

A field's id is its control's `name` (`user.first_name`, `job_assessment.question_<id>.response`, `job_assessment.question_<id>.response.0.<part>` for a history entry's part, `disclosure_agree`), a combobox's input id (`user.state`, `user.country`, `app.where_from`), or a file area's id (`files.Resume`). Its label is the element `<id>_label`, a box's own label for a single box; ` (required)` at its end makes it required and is dropped. A history entry's parts are labelled with their question's text from the props. A control outside the table below is `unhandled_rendering`.

Each rendering, and in how many of 384 postings' props it shows (2026-10-22, one posting per employer), the renderings checked on 20 forms dry-run:

| Rendering | Field type | Postings |
|---|---|---|
| MUI text input: name, preferred name, email (`type=text`), address, city, zip, LinkedIn; `textq` questions | `text` | 384; `textq` 78 |
| `textarea`: `essay` questions | `text` | 113 |
| `tel` input with a country `select` (react-phone-number-input), `+1` by default; a history entry's phone | `phone` | 384 |
| text input with a date picker (`datePicker-field-label`), typed `MM/DD/YYYY`: `dateq` questions, history dates | `date` | 38; history 53 |
| radio group: `yes-no`, `single-choice`, a history entry's "Degree obtained?" | `choice`, the option's value | 150, 30 |
| boxes sharing a name, `value=""`, their label beside them (`multi-field-label`): `multiple-choice` | `multi_choice`, the options' labels | 49 |
| one box: SMS opt-in (`user.contact_preference`), the employer's disclosure (`disclosure_agree`), the privacy policy (`gdpr_disclosure_agree`), job alerts (`subscribe`) | `choice` `Yes`/`No`; the two agreements required | 307, 141, 42, 367 |
| react-select (`input[role=combobox]`): country, preset; the referral source (`app.where_from`, the employer's list) | `choice`, the options read by opening the menu | 274; 373 |
| react-select of the country's states (`user.state`) | `typeahead` | 274 |
| text input `app.where_from_other`, "Please Specify Source", required: rendered only once the source is `Friend/Colleague` or `Other`, no other option of 3 forms' lists showing it | `text`, a follow-up of `app.where_from`, listed when its options hold one of these | 3 of 3 tried |
| Dropzone area per document (`files.Resume`, `files.CoverLetter`, `files.Other`), its file input hidden on the body | `file` | 381, 363, 375 |
| employment (`employmentq`) and education (`educationq`) history: one entry of text, phone, date and radio parts, "Add Another" for more | the first entry's parts | 53, 37 |

A `subheadq` question is a heading, not a field (37 postings). The referral source is a hidden input on some forms and left alone there. No native select other than the phone's country was seen.

## Filling

- Text and phone inputs are filled; a date is typed as `MM/DD/YYYY`; a radio or a box is checked.
- A react-select is typed into and its option clicked: the one equal to the answer, or for the state the first that starts with it. The state is filled last, since its options are the chosen country's.
- Once filled, the form's controls are read again by the same rule as `read`: one an answer showed that `read` did not list stops the fill (`ListsShownFields`), and so does the source detail under a source not in its condition.
- A file is attached through its area's file chooser and waited for until its name shows as complete.

## Read-back and submit

- The read-back is each control's value, the checked radio's value, the checked boxes' labels, a box as `Yes`/`No`, a react-select's shown value, a file area's last complete file name; a date back to ISO.
- Submit is the form's submit button; the evidence is the answer to the first POST on the posting's host other than a file upload. The props carry `captcha_enabled?` (53 postings) and a reCAPTCHA site key, but no challenge renders in the form during a dry run; a challenge shown on submit is `blocked`, `captcha`. No send has been made through this adapter, so the endpoint and the page after a send are unobserved.

## Known gaps

None.
