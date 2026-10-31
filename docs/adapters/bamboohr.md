# BambooHR

BambooHR's careers sites, observed on live postings, last on 2026-10-31. The fixture page is [tests/pages/bamboohr/job.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/bamboohr/job.html) with its detail JSON [detail.json](https://github.com/shbernal/atsfill/blob/main/tests/pages/bamboohr/detail.json), served as `acme.bamboohr.com` in `tests/test_bamboohr.py`: one example of each rendering below.

## Where the form is

- Every employer is on `<employer>.bamboohr.com`. A job is `/careers/<job id>`; that URL is the double-send key.
- The job page renders the form behind Apply for This Job, from `/careers/<job id>/detail`: an undocumented API, no session, listing each standard field by key (`firstName`, `countryId`, `resumeFileId`) with its label, `isRequired` and options, then the custom questions with their type. A demographic key the employer does not ask is an empty list. `request_read` fetches it with a request, `browser_read` from the browser, and both list the fields from it: parity holds by construction.
- A closed posting answers 404 on `/detail`.
- An attached file is uploaded at once (`/ajax/files/attachTemporary.php`), and its id written to a hidden input; nothing else reaches BambooHR before Submit.

## Reading the form

A standard field's id is its key; a custom question's is `question_<id>`. A control is found by its `name`: the key, `<key>.value` for the address fields, `customQuestionAnswers.<type>_<id>` for a question. The page carries a hidden honeypot input, left empty.

Each rendering, and in how many of 325 postings' detail answers it shows (2026-10-21):

| Rendering | Field type | Postings |
|---|---|---|
| text input: name, email, address, desired pay, URLs, referrer, school, custom `short` | `text` | 325 |
| text input for the phone | `phone` | 325 |
| file: a button over a hidden `input[type=file]`, a hidden `*FileId` input the upload fills, the name shown in `[data-fabric-component=FileUploadList]` once done; a custom `file` question takes several | `file` | 325, 74 cover letter, 39 custom |
| Fabric select: a button that opens a `role=menu` (`#<data-menu-id>`), over a hidden native `select`: country, education, gender, ethnicity | `choice`, the menu item's text | 300, 69, 38 |
| state: a Fabric select of full names for the United States, Canada, Australia and the United Kingdom, a text input for any other country; the page refetches it when the country changes | a dependent list: `choice` with one option set per country the form lists that has a state list, `options` unknown under any other, typed | 241 select, 59 text |
| masked date input, its format the employer's (`yyyy-mm-dd`, `mm/dd/yyyy`, `dd/mm/yyyy`, `dd mon yyyy`), read from its placeholder | `date` | 295 |
| radio group, the input inside its `label`: custom `yes_no`, custom `multi` (option ids as values) | `choice` | 528, 110 questions |
| a `multi` question's Other: an `other` radio that shows a `<name>_other` text input | `choice` with `Other`, then a `text` `shown_when` it is Other, optional as the page marks it | 36 |
| checkbox, custom `checkbox` (an acknowledgement, usually required) | `choice` `Yes`/`No` | 34 |
| `textarea`: custom `long`, references | `text` | 200, 41 |
| veteran status: a radio (Decline to Answer, Not a Veteran, Veteran); Veteran shows a multi-select `role=listbox` of the kinds the JSON lists | `choice`: the two radios, then each kind | 38 |

A key or question type outside this table, or a field the JSON lists and the page does not show, is listed as `unhandled_rendering`.

The page shows a country before any answer, the employer's: the country field's `default`, which a required country still needs answered. The Test user answers it, and the state from that country's list.

## Filling

- The country first, then the rest in the JSON's order; the state is picked from the list the chosen country shows, or typed.
- A Fabric select is filled by opening its menu and clicking the item; a radio or a checkbox through its label; a date typed in the placeholder's format; a file attached and waited for until its id is set.

## What the form shows

`shown_fields` names each of the form's controls as the read does: by its `name`, the date by its placeholder, the veteran radios, named by React, by their ids. A control that came after the read and is no field's, visible, is reported by its name; the honeypot, an input moved off screen under a generated name, is there from the start. The state and ZIP relabel by country (Province, Postal Code) over the same controls.

## Read-back and submit

- The read-back is each present control: a text's value, the Fabric button's text, the checked radio's label, a box as `Yes`/`No`, the date back to `YYYY-MM-DD`, a file as the name the page lists, a veteran's first kind.
- A reCAPTCHA checkbox sits under the form; `submit` ticks it like JazzHR's, `Blocked` on an image challenge, then clicks Submit Application. The evidence is the answer to the first POST on the employer's host outside `/ajax/files/`. No send has been made through this adapter, so the endpoint and the page after a send are unobserved.
