# Eightfold

Eightfold's career sites, observed on live postings, last on 2026-10-31. The fixture page is [tests/pages/eightfold/apply.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/eightfold/apply.html) with its questions API answer [questions.json](https://github.com/shbernal/atsfill/blob/main/tests/pages/eightfold/questions.json), served as `acme.eightfold.ai` in `tests/test_eightfold.py`: one example of each rendering below, Eightfold's controls reduced to their markup and a few lines that act as they do.

## Where the form is

- A job is `/careers/job/<pid>[-<slug>]` on `<employer>.eightfold.ai` or on the employer's own domain (`jobs.<employer>.com`), with at times a `domain` or `microsite` parameter that picks the site; the adapter keeps those two. The double-send key is `https://<host>/careers/job/<pid>`. A URL is claimed by its path alone, a page by its `_csrf` meta and `_EF_GROUP_ID`.
- The position API (`/api/pcsx/position_details?position_id=<pid>`) says how the posting is applied to: `allowed` on Eightfold's own form, `link_off` on another system (Workday, Oracle, SuccessFactors: `external_step`), `log_in` behind the employer's sign-in (`login_required`). Of 27 employers, 15 apply on Eightfold, 8 link off, 4 ask to sign in.
- The form is `/careers/apply?pid=<pid>`, one page. It renders from `POST /api/application/v2/questions?pids=<pid>&user_mode=logged_out_candidate` with `{"answers":{}}` and the page's `_csrf` token in `X-CSRF-Token`: sections of questions with each one's id, type, `required`, options, and `parentDependencies`, the questions whose answers render it. No session is needed. `request_read` fetches the apply page for its token and cookie, then this undocumented API; `browser_read` calls it from the browser; both list the fields from it, so parity holds by construction.
- The employer's cookie banner (OneTrust, TrustArc, Didomi, or its own) covers the lower half of the form and takes the clicks there; the blocker's lists hide it. A sticky footer holds the submit button, so a control is scrolled to the middle of the window before it is clicked.
- A resume opens a "Data Privacy Agreement" dialog: job recommendations from the employer follow by email. Cancel drops the file. "I Agree" uploads it to `/api/application/v2/resume_upload` at once, where it is stored and parsed, and the parse's findings can land in the contact fields, so the resume is attached first. A "Save my answers for future applications" box, ticked by default, is left as it is.

## Reading the form

A field's id is the question's `questionId`, which is a name (`firstname`, `Q_HEAR_ABOUT_US`), a number, or, where Eightfold generated it, the label itself with its markup. Each question renders under a label or legend, its control's element id is the section's title and the question's id joined by `_`, every character but letters, digits and `_` an underscore, and an error line beside it has that id and `_error`. An untitled section takes a title the page gives it, so the adapter finds a control by the shortest error id that ends with the question's id.

Each rendering, and in how many of the 13 employers whose forms were read it shows (2026-10-22):

| Rendering | Field type | Forms |
|---|---|---|
| text input (`text`, `text-row`, `numeric`, `location_input`): name, email, address, links, custom text; a number input says `(a number)` | `text` | 13 |
| resume (`resumeUpload`), behind the privacy dialog; accepted extensions from the API | `file` | 13 |
| phone: a country code combobox (`🇺🇸 (+1) United States of America`) beside the number; the code picked by the number's dial code, the longest that fits, where several countries share it its main one | `phone` | 13 |
| dropdown (`select`: `dropdown`, `dropdown-textbox`): a combobox whose listbox renders at the end of the page when opened | `choice` | 13 |
| a question another one's answer renders (`parentDependencies`), up to three levels deep, several parents at times: any of them shows it, any answer where it lists no values; variants of one question for different countries are told apart by their conditions | as its kind, its condition `Is` each parent's values, `AnyOf` them for several, its `required` kept | 7 |
| state (`select` with an `apiEndpoint`): options loaded for the country picked | `typeahead`, the option that starts with the answer | 6 |
| radio group (`radio`, `choice`, `section-radio`): the input lies over its label | `choice` | 8 |
| other file (`fileUpload`): cover letter, attachments; listed by name once attached | `file` | 5 |
| checkbox group (`checkbox`): consents, mostly one box | `multi_choice` | 4 |
| date (`date`, `date-today`, `date-advanced`): a read-only combobox opening a month picker whose cells are titled by ISO date; shown as `21 Nov 2026`; `date-today` is today's date, read-only | `date`; `date-today` `prefilled` with today's date, the only one the page takes | 5 |
| position location (`positionLocation`): location pills, the only one preselected | `multi_choice`, the pill its `default` | 1 |

Without an account, a control showing a value before any answer holds the page's: it is the field's `default`. Instructions (`instruction`) are not fields. A question of another type is listed `unhandled_rendering`.

## Filling

- The resume first, then the questions in the API's order. A follow-up is filled once the answers filled before it meet its condition, and waited for briefly. 
- The API lists every question whatever the answers, and `shown_fields` holds the page to it: each error line's element id is the question whose id it ends with, the longest such (`q_town` over `town`), one no question ends as its element id.
- Text inputs are filled; a dropdown is opened and its option clicked; a radio or a box is clicked through its input; a date is paged to in the picker and clicked; a pill not in the answer is removed and one missing picked.

## Read-back and submit

- The read-back is each rendered control: its value, a dropdown's shown choice, the checked inputs' labels, the pills, a phone as `+<dial code> <number>`, a date back to ISO from the way the picker shows it, a file as the name its list shows.
- Submit is `[data-test-id=submitApplicationButton]`; the evidence is the answer to the application POST under `/api/application/`. No send has been made through this adapter, so the request and the page after a send are unobserved.

## Without an adapter

- `link_off` postings apply on the system their URL names, and `log_in` sites (4 of 27 employers) need an account on the employer's own Eightfold site, which atsfill does not sign in to.
