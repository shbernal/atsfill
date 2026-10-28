# JobAffinity

JobAffinity's apply pages, observed on live postings, last on 2026-10-28. The fixture pages are [tests/pages/jobaffinity/](../../tests/pages/jobaffinity/): `old.html` and its `questionnaire.html`, and `new.html`, served as `jobaffinity.fr` in `tests/test_jobaffinity.py`: one example of each rendering below.

## Where the form is

- Every posting is `jobaffinity.fr/apply/<token>`, whatever the employer; LinkedIn adds a `?src=` that is dropped. The double-send key is that URL without its query. Freehire lists no JobAffinity source: postings are found through LinkedIn and on employers' career sites, which link to the apply page or frame it (the page's `frame-ancestors` names them).
- The page is rendered on the server, in one of two generations, chosen per employer: the older form (18 of 41 postings, 5 of 9 employers) and the newer one (23, 4). `request_read` fetches it over HTTP, `browser_read` takes the page's HTML, and one parser lists the fields from both: parity holds by construction.
- An unknown token answers 404 with no form: `posting_gone`. A closed posting is unobserved.
- A document is uploaded as soon as it is attached: it reaches JobAffinity before the submit.

## The older form

`form#identity`, its documents in two forms of their own (`js-cv-form`, `js-other-form`) above it, each a file input named `file`. The page's own stylesheet hides whole sections per employer (`.adress-fields`, `.phone-fields`, `.question-fields`, `.salary-field`, `.motivation-field { display: none }`), and JobAffinity's hides `.experience-field` everywhere: a control inside a hidden class is not listed.

A stepper naming a page after the first (`#stabilo-haut .step-separator`, 9 postings, 2 employers) means a questionnaire follows. Its page is `/newcandidate/questionnaire/<wizard_id>/<job_id>?session_id=<session>&ui=False`, built from the first page's hidden inputs and action; it answers a GET before the first page is posted, without cookies, so both reads list its fields after the first page's. The first page's Valider posts it (303) to the same URL.

On the first upload of a CV, JobAffinity analyses it and fills the name, email, postal code and town that are empty and shown.

## The newer form

`form.candidate-application-form`, inside a panel whose `data-cap-wizard` says whether a questionnaire step follows on the same page (`1`, none seen: the `questionnaire_step` known gap). Documents are upload widgets (`.js-file-upload`, `data-form-field-name`), whose uploaded files' metadata the widget keeps in a hidden input of that name. A row with the class `none` is hidden. Selects are drawn by select2 over the native select. The page posts the form by XHR to `/application_form/submission/candidate_application?publication_token=<token>`, which answers JSON `{ok, errors}`.

## Reading the form

A field's id is its control's `name` (`lastName` on the older form, `last_name` on the newer, `question_<n>` on a questionnaire), a document's the form or widget name (`cv`, `other`, `other_documents`). Its label is its `label[for]`, a radio group's its block's `.input-control-label`, without the marker span and the help popup; a `span.required` without the class `none` makes it required. A control outside the table below is `unhandled_rendering`.

Each rendering, and in how many of 41 postings it shows (2026-10-28):

| Form | Rendering | Field type | Postings |
|---|---|---|---|
| both | document: CV (required), other documents | `file` | 41 |
| both | text input: names, email, address, postal code, town, salary, experience; textarea: comment | `text`; `phone` for a name holding `phone` | 41; phone 14 |
| both | native select: civility, country, availability | `choice`, the option's text | 41 |
| older | text date, `(JJ/MM/AAAA)` under it: available from | `date`, typed `DD/MM/YYYY` | 10 |
| newer | `type=date`: available from; `type=number`: salary | `date`; `text` | 6, 6 |
| both | consent checkbox `gdpr_agreement` | `choice`, `Yes`/`No`, required | 15 |
| questionnaire | text input, native select, radio group | `text`, `choice` | 9, 2, 1 |

"Available from" shows only when the availability is its date option: `shown_when` that option's text, on both forms.

## Filling

- Documents first, then, on the older form, until the CV analysis is done, so a filled answer is never overwritten and an unanswered field holds what the analysis gave.
- A text, phone or date input is filled, an older form's date as `DD/MM/YYYY`; a number input takes only a number, else it is left empty and reads back as a mismatch. A select is set to the value of the option whose text is the answer. A radio and the consent box are drawn under their labels: the label is clicked.
- A dry run fills the older form's questionnaire in a second tab of the same session, opened on its URL.

## Read-back and submit

- The read-back is each control's value, a select's or radio's option text, the consent box as `Yes`/`No`, and a document's last uploaded name (the older form's listed names, the newer's hidden metadata).
- The newer form: submit clicks Envoyer ma candidature; the evidence is the URL and status of the XHR post, whose JSON must say `ok`, else its errors are the failure.
- The older form: submit closes the dry run's tab, clicks Valider and waits for the next page. Still on the first page is a failure. On the questionnaire, it fills the questionnaire again with the dry run's answers and clicks Valider; the evidence is the URL the browser lands on. The first page's post was observed once by hand stopped at the questionnaire; whether it leaves a candidate behind, and the page after the questionnaire, are unobserved. No send has been made through this adapter.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `questionnaire_step` | the newer form's questionnaire step (`data-cap-wizard="1"`), shown after the first step validates | `unsupported_flow` | 0 |
