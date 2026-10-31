# Personio

Personio's career sites, observed on live postings, last on 2026-10-31. The fixture page is [tests/pages/personio/apply.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/personio/apply.html), served as `acme.jobs.personio.com` in `tests/test_personio.py`: one example of each rendering below.

## Where the form is

- Every employer is on `<account>.jobs.personio.com` (`.de` serves the same pages). A job is `/job/<id>`, its form `/job/<id>/apply`, which the adapter opens from the job page. The double-send key is the job URL on `.com`.
- The apply page is rendered on the server with the whole form in its HTML, in the career site's language (a `?language=` in the URL picks another; `Accept-Language` does not). `request_read` fetches that server-rendered HTML, `browser_read` takes the page's form, and one parser lists the fields from both: parity holds by construction.
- The page builds the form from `/api/v1/jobs/<id>/application-form`, a public JSON API with English labels. It answers 429 for a while after some 180 requests in a burst from one address, while the pages keep answering, so the adapter does not use it.
- A closed posting's apply page has no form.
- A document is posted to `/api/v1/documents` as soon as it is attached: it reaches Personio before the submit.

## Reading the form

A field's id is its control's `name`: `first_name`, `custom_attribute_<n>`, and `documents.<category>` for a document category's file input. Its label is its `label[for]`, else its group's label (`role=group`, `aria-labelledby`: the name pair, a document category), without the marker spans; a starred label, own or the group's, makes it required. A control outside the table below is `unhandled_rendering`.

Each rendering, and in how many of 441 postings' apply pages it shows (2026-10-22; the form API of 178 of them listed only the types `text`, `option` and `date`):

| Rendering | Field type | Postings |
|---|---|---|
| `text` input: name, phone, available from, salary, location, current title and employer, profile, custom questions | `text`, `maxlength`; `phone` for `phone` | 441; custom 217 |
| `email` input: `email` | `text` | 441 |
| native `select`: custom questions, `gender`, `years_of_experience`; the standard fields' option texts in the page's language | `choice`, the option's text | 200, 94, 25 |
| native date input: `birthday`, custom dates; `YYYY-MM-DD` | `date` | 53, 16 |
| file input per document category (`cv`, `other`, `cover-letter`, `employment-reference`, `certificate`, `work-sample`), `multiple`; once uploaded, the input is cleared and the name shown as a pill in the category | `file` | 441, 389, 227, 141, 51, 39 |

No checkbox, radio, textarea or consent box was seen: the privacy notice is text.

## Filling

- A text, phone or date input is filled; a select is set to the value of the option whose text is the answer.
- A document is attached to its category's input and waited for until its name shows as a pill.
- Once filled, the form is parsed again as the read parsed it: a field the read did not list stops the fill. None showed on 3 postings, three answer sets each (2026-10-31).

## Read-back and submit

- The read-back is each control's value, a select's chosen option's text, and a document's last pill name.
- The apply page carries a Cloudflare Turnstile widget, and its submit button is disabled while the form is empty; what enables it is unobserved. Submit waits for it to enable, else it is `blocked`, `captcha`; the evidence is the answer to the POST on `/api/v1/jobs/<id>/application` (JSON, with an `Idempotency-Key`). No send has been made through this adapter, so the answer and the page after a send are unobserved.

## Known gaps

None.
