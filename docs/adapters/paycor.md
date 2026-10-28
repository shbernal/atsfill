# Paycor

Paycor Recruiting's apply pages (the former Newton), observed on live postings, last on 2026-10-20. The fixture pages are [tests/pages/paycor/](https://github.com/shbernal/atsfill/tree/main/tests/pages/paycor/), served as `recruitingbypaycor.com` in `tests/test_paycor.py`: the self-identification pages, the qualifying questions and a form with one example of each rendering below.

## Where the form is

- Every employer is on `recruitingbypaycor.com/career/`. A job is `JobIntroduction.action?clientId=<client>&id=<job>`, its form `SubmitResume.action` with the same parameters. The double-send key is the job id, unique across employers.
- Opened at the top level, a page of an employer with a career site sends the browser on to that site (`?gnk=job&gni=<job>`), which frames it; that site may never finish loading. With `m=t` (Paycor's own layout) the page stays put; it drops only the employer's styles, which hide job description details, never fields. The adapter opens `SubmitResume.action?…&m=t`, and claims an employer page by its Paycor iframe and `gni`.
- The pages are server-rendered, each a form posting to the next, which carries the earlier answers as hidden inputs:
  1. a federal contractor's self-identification (VEVRAA): an introduction, then gender and race, then veteran status; Continue is disabled until each group is answered;
  2. the qualifying questions: Yes/No buttons (`#y_<id>`, `#n_<id>`) writing `true`/`false` into a hidden input; a No still leads to the form;
  3. the form, posted to `ApplyJob.action` on Submit.
- The form is reached only by posting the pages before it in one session, so there is no request read.
- A closed posting says "this job is no longer active".
- The form's third-party scripts (Dropbox, Google) can hold its `load` event back for good; the page is used once its DOM is in and its script has filled the state and country lists.

## Reading the form

A field's id is the control's `name`; a qualifying question's is `question.<id>`, a self-identification group's `eeo.<name>`, the resume upload's `resume`. Labels: a standard field's `label[for]`, a custom question's `.gnewtonQuestionLabelClass label`, else the control's `title`, else its section title. A control under `display: none` is left out, and so is the `fax_number` honeypot.

Each rendering, and in how many of 40 postings it shows (2026-10-20; 30 forms read, 9 behind the self-identification pages, 1 closed):

| Rendering | Field type | Postings |
|---|---|---|
| self-identification pages: radio groups in `.sub_section`, each option's `.radio-label` | `choice` | 9 |
| qualifying questions page | `choice`, `Yes`/`No` | 16 |
| resume upload box (`#uploadResumeInput`): stored and parsed at once, the hidden `attachmentA` input under `display: none` | `file` | 29, 24 required |
| other attachments (`attachmentB`…`E`, cover letter) | `file` | 4 |
| first, preferred, last name, email, address, city, zip code, comments: inputs by id | `text` | 30 |
| mobile phone (`tel`, formatted as typed) | `phone` | 30 |
| state select of the contact block, codes | `choice` | 30 |
| country select of the contact block | `choice` | 1 |
| custom text, email, textarea | `text` | 19, 2, 8 |
| custom number (`type=number`, amount of a salary) | `text`, label `(a number)` | 6 |
| custom phone (text with `gnewtonPhone`) | `phone` | 3 |
| custom select (currency, frequency, degree…) | `choice` | 14 |
| custom radios, the option's text after the input | `choice` | 8 |
| custom checkboxes, one or several (an acceptance, shifts, "currently attend") | `multi_choice` | 13 |
| custom date (`_answer_date`): a text `mm/dd/yyyy` turned by the page into a native date input | `date` | 13 |
| custom country select with its state select (`data-state-id`): the page's script holds every country's state list (`var ca_country_states = [...]`) and refills the state on a change; a country without one disables the state and drops its `required` | the state a dependent list: `choice`, one option set per country with a list and one for no country, the list the page starts with; `options` empty under any other country; required only under a country with a list | 1 |
| employer's self-identification inside the form: gender, race, veteran radios | `choice` | 13 |
| text message opt-in (`candidateTextMessagingOptIn`), enabled once a mobile number is typed | `choice` | 14 |
| privacy policy acknowledgement (`ccpaDisclaimerAgree`) | `multi_choice` | 30 |
| a disclaimer's date (`current_date`), signature date | not a field: hidden | 30 |

A control of another type is listed as `unhandled_rendering`; a page before the form other than these is an `adapter_error`.

Work history, education and references are flat custom questions (three blocks each at most), not entries to add.

## Filling

- The pages before the form are walked again from the start with the answers; reading walks them picking the last option and Yes.
- The resume is uploaded first and waited for (`ajaxResumeFilename`): its parse fills fields, which the answers then override.
- Controls are filled in page order, so a country comes before its state and the mobile number before the opt-in. Text is filled, a native date takes `YYYY-MM-DD`, a text date `MM/DD/YYYY`, a select its option's text, radios and boxes are clicked.
- A state answered under a country without states is refused before typing; a state select the page still leaves disabled is `answer_not_taken`.

## Read-back and submit

- Each control's value, a select's shown text, the checked options' labels, a date as `YYYY-MM-DD`, the stored resume's name. The answers of the pages before the form are read from the form's hidden inputs (`true`/`false`, a self-identification option's value).
- Submit is `button.gnewtonSubmitBtn`, posting `ApplyJob.action`; the evidence is that POST. No send has been made through this adapter, so the end of a send is unobserved.
