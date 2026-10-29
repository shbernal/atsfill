# WeRecruit

WeRecruit's apply form, observed on live postings, last on 2026-10-29. The fixture page is [tests/pages/werecruit/posting.html](https://github.com/shbernal/atsfill/tree/main/tests/pages/werecruit/), served as `careers.werecruit.io` and as an employer's own domain in `tests/test_werecruit.py`.

## Where the form is

- A posting is `careers.werecruit.io/<fr|en>/<company>/<offres|offers>/<slug>-<id>`, the id six hex digits. The slug and the language do not matter: `/fr/<company>/offres/<id>` redirects to the posting, which is the double-send key. An unknown id answers 404.
- An employer can serve its career site on its own domain, as `/<fr|en>/offres/<slug>-<id>` (1 employer of 14). Its URL does not tell, so the page does: WeRecruit's form, `form#form` holding an `isEmployerPortal` input.
- Freehire's postings are almost all in France, in French. The language in the URL changes the standard labels, not the employer's questions or document names.
- The form is on the posting page, rendered on the server: `request_read` fetches it over HTTP and `browser_read` takes the page's HTML, one parser for both. The custom domain is read by the browser only.
- No account, no captcha, no OTP. Nothing reaches WeRecruit before the submit.

## Reading the form

A field's id is its control's `name`, trimmed (the photo's is ` photo`). Its label is its `.form-group`'s first label, without the `span.required` marker; a radio option's is its `label[for]`. A control carrying `required` is required, which is what the marker shows. A control outside the table is `unhandled_rendering`.

Each rendering, and in how many of 11 employers' forms it shows (2026-10-29):

| Rendering | Field type | Forms |
|---|---|---|
| radio group: civility (Madame, Monsieur, Mx) | `choice` | 11 |
| text, email: names, email; textarea: a question | `text` | 11; 1 |
| `type=tel`: phone | `phone` | 11 |
| native select: availability, salary band (gross/year) | `choice`, the option's text | 10, 11; required in 6, 7 |
| file: CV (required), cover letter, photo (`.jpg`) | `file`, with the input's `accept` | 11, 6, 5 |
| radio group Oui/Non: a question | `choice` | 2 |

The consent checkbox is in every page's markup but commented out. The CV and the other documents say 2 MB at most; the page does not check it.

## Filling and submit

- A text is filled, a select set to the value of the option whose text is the answer, a radio's label clicked, a document set on its file input.
- The read-back is each control's value, a select's or radio's option text, a document's file name.
- Submit clicks Je postule. jQuery Validate checks the form, then posts it as multipart to the posting's URL. The evidence is that post's URL and status; no post is a failure, with the errors the page shows. What the page answers to the post is unobserved: no send has been made through this adapter.
- The phone is checked by a pattern that takes E.164 (`+33612345678`) but not a spaced `+33 6 12 34 56 78`.
