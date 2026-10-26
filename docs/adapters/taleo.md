# Taleo

Oracle Taleo Enterprise career sections, observed on live postings, last on 2026-10-26. The fixture pages are [tests/pages/taleo/apply.html](../../tests/pages/taleo/apply.html) and [review.html](../../tests/pages/taleo/review.html), served as an Acme zone in `tests/test_taleo.py`: one wizard page with one example of each rendering below, and Review and Submit.

The adapter (`atsfill.adapters.taleo`) is not in `adapters()` yet: the registry has Taleo `unsupported` for its `location_levels` gap, and it is registered once its adapter gate passes.

## Where the form is

- A posting is `<zone>.taleo.net/careersection/<section>/jobdetail.ftl?job=<id>`; apply is the same path with `jobapply.ftl`. The double-send key is the detail URL with `job` alone in its query.
- An employer can have several zones and many sections per zone. One of 17 zones sampled sends apply to the employer's own site (a page the employer built): a skipped posting.

## Account

Applying needs an account, one per zone: the account scope is the zone. The sign-in pages live under `/careersection/iam/accessmanagement/` (`login.jsf`, `register.jsf`), JSF forms named `dialogTemplate-dialogForm`.

- Apply sends a visitor to `login.jsf?redirectionURI=<apply>`: User Name, Password, Login, and New User. On 7 of 16 zones apply shows a privacy agreement first, to a visitor too.
- New User shows a privacy statement (`…StatementBeforeAuthentificationContent-ContinueButton`), then User Name, Password and its confirmation, Email and its confirmation. atsfill uses the email as the user name and fills the fields the zone shows. One zone asks no password (it mails one): `unhandled_rendering`.
- Once signed in, a zone may ask security questions (`changeQuestionsAnswers.jsf`): a question the candidate types and its answer. atsfill types its own question and a random answer it does not keep; a lost password goes through the email reset.
- The pages carry `…:flowHeader:Logged` (`true`/`false`). The signed-in check asks for the last posting's apply page: signed in where it neither redirects to the sign-in or the security questions nor says `Logged` false. A sign-in or sign-up that leaves the sign-in pages still signed out stops with `unhandled_rendering`, naming the page.
- No zone sent a verification email, showed a captcha or asked for a code. On the 7 zones with a privacy agreement first and on one other, the sign-up returned to the agreement signed out and no account existed after: not understood yet.

## Reading the form

A wizard of server-rendered pages (`flow.jsf`, or `jobapply.ftl` itself), one in the DOM at a time, with a tracker (`…-<n>-dtGotoPageLink` for the pages reached). On one section: Resume Parsing, Personal Information, Work Experience, Education & Certifications, Application Questionnaire, Job-Specific Questionnaire, Attachments, Diversity Information, eSignature, Review and Submit; other zones start with "Initial information". Save and Continue (`…-saveContinueCmdBottom`) saves the page and moves on only once it is valid, else the page comes back headed "Errors occurred on this page" with its messages; loading apply again resumes at the last page reached.

- **Walking.** `browser_read` opens apply, goes to the first page through the tracker, reads it and presses Save and Continue, for as long as a page saves with what it holds: a fresh draft's first page does not, so a first read lists that page only; each fill saves the pages it answers, and the next read walks them. Reading saves pages, as a fill does. Review and Submit ends the walk.
- A field's id is `<page title>/<control's DOM id>`, the id without its generated segments (`j_id_id16pc9`, `page_0`): `personal_information/cpi-cfrmsub-frm-dv_cs_candidate_personal_info_FirstName`. The page title is the last `h1` that is not the site's own or "Authentication". A label is its `label[for]` without its hidden "Required", which marks the field required, as a red star does a question's legend.
- Resume Parsing's upload, the job alert box and the attached files' list are not read.

Each rendering, from 4 zones walked:

| Rendering | Field type | Zones |
|---|---|---|
| text input, `textarea`: name, address, city, zip, a list entry's fields, eSignature's full name | `text` | 4 |
| a phone input (`…Phone`), validated by the zone's mask `(999)999-9999\|+999999999999999` | `phone` | 4 |
| `select`: advance notice, education level, a Diversity question (`…questionSingleList`), a question's list (`…-qsl`) | `choice` | 4 |
| source type (`…recruitmentSourceType`), without its "Ad or event ID" option, which takes an ID the employer gives | `choice` | 4 |
| a place: country, state, nearest city (`…Location-0`, `-1`, `-2`), each level's options loaded by the one above | `choice`; a level below the first, `prefilled` with the profile's place where it holds one | 3 |
| a month and a year `select` (`…BeginDate.month`, `.year`): a list entry's dates | `text`, label `(MM/YYYY)` | 3 |
| one checkbox: Current Job, Completed Degree, the supervisor may be contacted | `choice` `Yes`/`No` | 3 |
| a question's radios (`…-qr`, a fieldset with its legend) | `choice` | 3 |
| a question's boxes (`…PossibleAnswer__<id>`), a Diversity race question | `multi_choice` | 3 |
| a question's explanation (`…questionExplanationText`), shown once it is answered Yes | `text`, `shown_when` Yes | 2 |
| Attachments' file input, with its Attach button | `file` | 2 |
| a date picker button (`….display`): date of availability | known gap | 3 |

## Filling

- `fill` opens the wizard on its first page and, page by page, fills the answered fields the page shows, then presses Save and Continue, until Review and Submit. A place's levels are filled top down, each waiting for the next level's options; picking the option already held is skipped, since it reloads the levels below. A question's explanation comes after its answer. The page is read again for a field an answer shows.
- A select is picked by its option's text; a radio or a box clicked to the answer; a month and a year picked from `MM/YYYY`. A North American number is typed in the zone's mask, `(202)555-0143`; another as given, for the `+` mask.
- A file is attached with Attach and waited for by its name.
- A page that does not save stops the fill: `refused: answer_not_taken` with the page's messages, "read again" where the page held fields the read had not listed.

## Read-back and submit

- Each page's values are read before it is saved: an input's value, a select's option text, the checked options' labels, a box as `Yes`/`No`, a month and a year as `MM/YYYY`, a masked phone as `+1 (202)555-0143`.
- Submit is Review and Submit's `Submit` button, expected to leave the page; no send was made.

## Development

The Test user's accounts are one per zone, created by the round (ADR 0013), with the adapter registered: `atsfill login taleo --user test --url <posting> --create --email <address>`.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `location_levels` | a place's levels below the country list their options only once the level above is picked, from a servlet each; a fresh profile holds none, so they are not listed | `unsupported_rendering` | 3 of 4 zones walked |
| `date_picker` | the date of availability is a date picker button, not filled | `unsupported_rendering` | 3 of 4 zones walked, optional |

Not handled yet, `unhandled`: the Sources list some source types show once picked ("You must select a value in the field Sources", 2 of 4 zones); the sign-up on the zones above that returns signed out.
