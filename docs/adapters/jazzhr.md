# JazzHR

Observed on live postings, last on 2026-10-25. The fixture page is [tests/pages/jazzhr/apply.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/jazzhr/apply.html), served as a posting's page in `tests/test_jazzhr.py`.

## Where the form is

- Postings: `<account>.applytojob.com/apply/<code>/<slug>`, the code ten letters and digits. The posting's page holds the form, `form#form_submit_new_resume`, under the description. The double-send key is `https://<account>.applytojob.com/apply/<code>`.
- A posting that is gone answers 410 with the career page and no form, read as `posting_gone`.
- A tracking banner (`#tracking-consent-banner`) shows a moment after the page, over its foot; the blocker's lists hide it on `applytojob.com`.
- JazzHR's API takes the employer's key, so there is no request read.

## Reading the form

The form is server-rendered HTML in a Bootstrap layout: each field a `.form-group` with a `label.control-label`, required ones marked `<i class="asterisk">*</i>`. A field's id is its element's id without `resumator-`, `questionnaire-` and `-value` (`firstname`, `q2293318`, `eeo_gender`). Each rendering and on how many of 15 postings (2026-10-20):

| Rendering | Field type | Postings |
|---|---|---|
| `input[type=text]` with a `label[for]`: name, salary, LinkedIn, referral, college | `text` | 15 |
| `input[type=email]` | `text` | 15 |
| `input[type=tel]` | `phone` | 15 |
| Address: one label over four inputs (address, city, state, postal), the last three named by their placeholder; its star stands for all four | `text` each | 13 |
| Resume: `input[type=file]` behind "Attach resume", beside "Paste resume" (`resumator-resumetext-value`, a textarea) | `file` (`.pdf .doc .docx .odt .txt .rtf`) and `text` alternatives, `one_of` `resume`, both under the resume's star | 15 |
| `textarea`: cover letter, references, a questionnaire question | `text` | 6 |
| `select` with a placeholder option (`resumator_no_selection` or "No answer", left out): citizenship, over 18, education, questionnaire questions | `choice` | 10 |
| `select` with `optgroup`s (citizenship by country) | `choice`, the groups flattened | 4 |
| `input.resumator-datepicker`: start date, a questionnaire date | `date`; jQuery UI's datepicker, set up with `yy-mm-dd`, takes the ISO date typed | 4 |
| Questionnaire checkboxes: a hidden `.resumator-questionnaire-checkbox-answer` and a box per option | `multi_choice` | 3 |
| EEO `select` inside its label, "Decline to answer" a real option (gender, race) | `choice` | 3 |
| Veteran status radios (`resumator-eeoc_veteran-value`) | `choice` | 1 |
| Disability radios (form CC-305) and its name and date, labelled "Disability status" | `choice`, `text`, `date` | 1 |
| SMS consent, one checkbox | `choice`, `Yes`/`No` | 2 |
| reCAPTCHA v2 checkbox ("Human Check") | not a field; ticked on submit | 12 |

Every other control is hidden and not a field. The disability name and date are required by the page only when a disability is declared: their `required_when` is the status on its `YES` option.

## Filling

- Plain inputs, selects (by the option's text), radios and boxes, set directly; nothing reaches JazzHR before submit.
- "Attach resume" is clicked to show the file input before the resume is attached; "Paste resume" (or the file side's own "Paste resume" link) to show the textarea before a pasted resume is typed.
- A questionnaire's boxes are joined into its hidden answer only on submit, so they read back by which are checked.

## Read-back and submit

- The read-back is the inputs.
- Submit ticks the reCAPTCHA box and waits for its token; an image challenge in its place is `blocked`. Clicking "Submit Application" runs the page's own check, joins the questionnaire boxes and posts the form to the posting's URL; the evidence is the page that post answers. Not observed on a send yet.
