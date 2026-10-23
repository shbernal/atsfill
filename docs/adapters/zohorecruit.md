# Zoho Recruit

Zoho Recruit's career sites, observed on live postings, last on 2026-10-24. The fixture page is [tests/pages/zohorecruit/job.html](../../tests/pages/zohorecruit/job.html) with its form API answer [form.json](../../tests/pages/zohorecruit/form.json), served as `acme.zohorecruit.com` in `tests/test_zohorecruit.py`: one example of each rendering below, Zoho's Lyte components reduced to their markup and a few lines that act as they do.

## Where the form is

- A job is `<account>.zohorecruit.<tld>/jobs/<page>/<job id>[/<slug>]` (`.com`, `.eu`, `.in`, `.ca`, …). The double-send key is the job's public API URL, by host and job id. A career site on the employer's own domain is not detected.
- The job page carries its form's id (`"webform_id"`) in its page data. "I'm interested" (`#detail-page-applybtn`, its text set per site) renders the form in place from `/recruit/v2/public/Job_Openings/<job>/apply/<form>?pagename=<page>`: a public API, no session, listing the sections and their fields with each one's type, `required`, length and options, the site's display rules, and the pre-screening questions a form asks with it. `read_api` fetches the job page and the API over HTTP, `read_form` from inside the page, and both list the fields from it: parity holds by construction.
- A cookie banner (`<career-cookie-consent>`) shows a moment after the page, at times several seconds after, and its layer takes every click. The blocker's lists miss it; the adapter's filter hides it.
- Above the form, an "Autofill Application" uploader (`rec-easyresume_file`, its type `RESUME` like the attachment's) sends a resume to Zoho at once (`POST /recruit/v2/public/parseresume`), behind a loading overlay, and what the parse finds overwrites the fields already filled. The adapter attaches files through each attachment's own input (`rec-form_<id>_file`), which keeps them in the page: nothing reaches Zoho before submit. Enter in a text field does not submit.

## Reading the form

A field's id is its API name (`First_Name`, `Current_Salary`); a repeatable section's field is `<section>.<field>`, a month range's ends `<…>.from` and `<…>.to`; a pre-screening question is its numeric id; a file is its attachment type (`RESUME`, `COVERLETTER`, `$photo_id`). Each renders as a component whose element id is `rec-form_<numeric id>` (a `<template>` with the same id beside it), `<numeric id>_1` in a section's first entry; a file as the `lyte-fileupload` holding `input[name=rec-form_<numeric id>_file]`.

Each rendering, and in how many of 36 employers' forms it shows (2026-10-21):

| Rendering | Field type | Forms |
|---|---|---|
| text input (`text`, `email`, `website`): name, email, address, links, custom text; the address inputs suggest places as typed, the typed text kept | `text`, its length | 36 |
| single dropdown (`lyte-dropdown`, `picklist`): its list rendered at the end of the page when opened, `-None-` the empty choice | `choice` | 36 |
| file (`lyte-fileupload`): resume (36), photo (15), cover letter (11), others; the accepted extensions from the API | `file` | 36 |
| phone: a country dropdown (default the browser's locale) beside the number; the country picked by the number's dial code, the longest that fits, the first listed | `phone` | 33 |
| image CAPTCHA (`captcha`) | not a field; `blocked` on submit | 27 |
| repeatable section (education, experience) behind "Add": text, textarea, a "currently" box, a month range of four dropdowns (month by index, its names in the site's language, and year) | the first entry; the range's end asked when the box is clear | 18 |
| skill set (`textarea` rendered as tags): a tag per comma-separated skill, added on Enter after a lookup, then a proficiency popover closed with Escape; a tag shows its name shortened | `text`, `(comma-separated)` | 17 |
| number input (`double`, `currency`, `integer`) | `text`, `(a number)` | 14 |
| a pre-screening questionnaire answered after submit (`assessment_id`, not mandated) | not read: it follows the send | 8 |
| checkbox (`boolean`) | `choice` `Yes`/`No`, optional: a clear box is an answer | 5, 18 in a section |
| date input (`lyte-input` of type date), typed in its `lt-prop-format` (`MM/DD/YYYY`, `DD/MM/YYYY`) | `date` | 5 |
| multiple dropdown (`multipicklist`): a chip per choice | `multi_choice` | 5 |
| textarea | `text`, its length | 4 |
| pre-screening questions in the form (`mandate_assessment`): text (type 1), textarea (2), radio (3), dropdown (6) | `text`, `choice` | 2 |
| consent (`gdpr`): in the API, not rendered | not a field | 2 |
| a field a display rule shows once another is answered (`layout_rules`, `Show_Fields`) | optional, the label says `(asked when '<label>' is [not] <value>)` | 1 |

A component's own state lives in page scripts that Camoufox's isolated evaluation does not reach, so every control is driven and read through its markup.

## Filling

- The first entry of each repeatable section answered is added, then the fields are filled in the API's order. A field a rule shows is waited for briefly, and skipped when the answer keeps it hidden.
- Text, number and date inputs are filled; a dropdown is opened and its item clicked; a box or a radio is clicked through its label.

## Read-back and submit

- The read-back is each present control: its value, a dropdown's shown choice (none while its placeholder shows), a multiple dropdown's chips, a phone as `<dial code> <number>`, a date back to ISO from its format, a tag by its `aria-label`, a month range end as `MM/YYYY` from the selected items, a file as the name its upload list shows.
- Submit is `#cw-submit-btn` ("Next" when a questionnaire follows); the evidence is the answer to the POST on `/recruit/v2/public/Job_Openings/<job>/apply/<form>`. A form with an image CAPTCHA is not sent: `blocked: captcha`. No send has been made through this adapter, so the page after a send is unobserved.
