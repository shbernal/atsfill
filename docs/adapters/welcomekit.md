# WelcomeKit

WelcomeKit, Welcome to the Jungle's ATS (sold as Welcome to the Jungle Solutions), observed on live postings, last on 2026-10-29. Its forms need no account. The fixture pages are [tests/pages/welcomekit/](../../tests/pages/welcomekit/): `old.html`, served as `welcomekit.co`, and `new.html` with its job's `new.json`, served as `ats.welcometothejungle.com` and its API in `tests/test_welcomekit.py`: one example of each rendering below.

## Where the form is

Two forms, on two hosts, chosen per employer:

- The older form: `www.welcomekit.co/candidates/new?o=<organization>&j=<job>`, both references required; a missing or unknown one answers 200 with only `ERROR, missing organization or job`: `posting_gone`. The double-send key is that URL with only `o` and `j`. Freehire lists no WelcomeKit source: the postings are on job boards and career sites WelcomeKit hosts, whose Postuler button carries both references (`.btn-job-apply[data-organization-reference][data-job-reference]`) and opens the form in a frame. The postings surveyed came from one such board.
- The newer form: `ats.welcometothejungle.com/board/<organization>/job/<id>/apply`, the job's page without `/apply` showing its description instead; the adapter goes to `/apply`. The double-send key is the `/apply` URL without its query. Freehire lists these postings under its `wttj` source, whose apply URL is this one.

## The older form

`form#new_candidate`, a Rails `simple_form` rendered on the server. `request_read` fetches it over HTTP, `browser_read` takes the page's HTML, and one parser lists the fields: parity holds by construction. A question's text is posted with its answer from a hidden input; hidden inputs and a `.hidden` group are not listed.

A field's id is its name without `candidate[` and `profile_attributes`: `firstname`, `country_code`, `resume`, `media_linkedin`, `cover_letter`, `consent`; a question's `answers.<n>`. Its label is its `label[for]`, without the `*`; an address part has none, so it is `Adresse: ` and its placeholder, the country list `Adresse: Country`. A control's `required` attribute makes it required.

Each rendering, and in how many of 29 postings (29 employers) it shows (2026-10-28):

| Rendering | Field type | Postings |
|---|---|---|
| text input: first and last name, current position, street, postal code, town, LinkedIn, website, Twitter, Behance, Dribbble; email input | `text` | 29 (website 26, Twitter 24, Behance and Dribbble 1) |
| `type=tel` | `phone` | 29 |
| native select, the country, France selected, countries in English | `choice`, `default` France | 29 |
| file input: CV (`accept` PDF), profile photo (no `accept`) | `file`; the photo accepts PNG, JPEG and GIF | 29; photo 23 |
| textarea: cover letter, each question | `text` | 26; questions 12 |
| consent checkbox, drawn over by an iCheck box | `choice`, `Yes`/`No`, never required | 29 |

Required varies per employer: the CV in 28, LinkedIn in 8, the cover letter in 7, every question in 12 of 12; the names, the position, the email and the phone in all.

## The newer form

A React app. Its job comes from `GET api.ats.welcometothejungle.com/api/v1/board/organizations/<organization>/jobs/<id>`: a job whose `status` is not `published`, or a 404, is `posting_gone`. The form is the same personal fields on every posting, then the job's `questions`, sorted by `order`, each with a `label`, a `type` and `required`. Both reads list the fields from that JSON, the request read over HTTP and the browser read by a fetch from the page; the browser read then waits for each question's control.

| Rendering | Field | Field type | Postings |
|---|---|---|---|
| text input: `firstname`, `lastname`, `email` (required) | its name | `text` | 19 |
| `type=url`: `linkedin_url`, `personal_website_url` | its name | `url` | 19 |
| `type=tel`: `phone_number` | its name | `phone` | 19 |
| upload widget: `cv`, PDF, required | `cv` | `file` | 19 |
| question `text`: textarea `custom_questions.<n>.answer` | `custom_questions.<n>` | `text` | 4 (7 questions) |
| question `boolean`: Yes and No buttons, `data-selected` on the chosen one | `custom_questions.<n>` | `choice`, `Yes`/`No` | 4 (8 questions) |
| question `document`: an upload widget like `cv` | `custom_questions.<n>` | `file` | 0, from the app's code |

A question of another type is `unhandled_rendering`; the app draws any type but `boolean` and `document` as a textarea.

## Filling

- Older form: a text input or textarea is filled, the select set by its option's text, a file attached to its input (it travels with the post). The consent box is under its iCheck box: its label is clicked.
- Newer form: an input is filled. A file is uploaded as soon as it is attached (a presigned URL from `/api/v1/assets/presigned-url`, then storage); the widget then empties its input and shows the name and size in a ticked badge, which the fill waits for. A boolean question's Yes or No button is clicked unless already chosen; a click on the chosen one clears it.

## Read-back and submit

- The read-back is each control's value, a select's option text, the box as `Yes`/`No`, the older form's attached file's name, the newer form's badge's name without its size, and a boolean question's chosen button.
- Older form: a reCAPTCHA v2 box sits above the submit button. `submit` ticks it, as BambooHR's does, `Blocked` on an image challenge, then clicks Je valide ma candidature, which posts the form to `/candidates?j=<job>&o=<organization>`. The evidence is the page it lands on; the form shown again is a failure, with its messages. The page after a send is unobserved.
- Newer form: the page warns that a job takes one application. Submit application runs an invisible reCAPTCHA Enterprise check, then posts the answers as JSON to `/api/v1/board/organizations/<organization>/jobs/<id>/apply`; the evidence is that POST's URL and status, a status other than 2xx a failure. Its answer is unobserved.
- No send has been made through this adapter.

## Known gaps

None met.
