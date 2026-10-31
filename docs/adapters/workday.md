# Workday

Workday's candidate sites and their apply wizard, observed on live postings, last on 2026-10-24. The fixture pages are [tests/pages/workday/login.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/workday/login.html) and [apply.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/workday/apply.html), with the posting, apply flow and questionnaire JSON beside them, served as an Acme tenant in `tests/test_workday.py`: the login page's outcomes, and a wizard with one example of each rendering below.

## Where the form is

- A posting is `<tenant>.wd<n>.myworkdayjobs.com/[<locale>/]<site>/job/<location>/<slug>`, or the same site on a shared host, `wd<n>.myworkdaysite.com/[<locale>/]recruiting/<tenant>/<site>/job/…`; `/details/<slug>` serves it too. The double-send key is `https://<tenant>.wd<n>.myworkdayjobs.com/<site>/details/<slug>`, whatever the host, locale or location (which Workday rewrites, `London-UK` to `London-(UK)`).
- The posting's JSON is public: `/wday/cxs/<tenant>/<site>/job/<slug>` (`posted`, the job's `country`, `questionnaireId`, `secondaryQuestionnaireId`). `canApply` is false while the account has a draft for it, so it does not tell a closed posting.
- Apply opens a choice (Autofill with Resume, Apply Manually, Use My Last Application); atsfill loads `…/apply/applyManually`.

## Account

Applying needs an account, one per tenant: the account scope is the tenant, the same on both hosts. A session holds cookies for one host only, so a tenant reached on the other host signs in again from the password source.

- `login_url` is the posting's site's `/<site>/login`: sign-in, with Create Account behind `createAccountLink`. Some tenants offer Google, LinkedIn or Apple first, the email form behind `SignInWithEmailButton`.
- The buttons take clicks through a `click_filter` overlay. The first press after typing often posts nothing: atsfill presses again until `POST /login` or `POST /register` goes out. A `beecatcher` input is a honeypot, left empty.
- The signed-in check is `GET /wday/cxs/<tenant>/<any site>/userprofile`, which holds `emailAddress` for an account and is `{}` for a visitor, asked of the hosts the session's cookies are for. Sessions are cookies without an expiry; Workday ends them server side.
- Sign-up answers `POST /register` with a redirect: to the posting or the site's home, signed in (20 of 29 tenants); to `/login/warning` after sending a verification email (7 of 29), `blocked: email_link`; to `/login`, signed out and saying nothing, when the email has an account, `refused: email_in_use`. Clicking the link lands on `/login/ok`, "Account Activated".
- A wrong password shows `[data-automation-id=errorMessage]`; an account not verified yet, "Verify your account before you sign in", `blocked: email_link`; a password the tenant's rules refuse, an `inputAlert` under the field, `password_rule`. atsfill's generated passwords meet every tenant's rules seen.
- No tenant asked for an OTP.

## Reading the form

The wizard is My Information, My Experience, Application Questions (or `Application Questions 1 of 2` and `2 of 2`), Voluntary Disclosures, Review, in the order `/wday/calypso/cxs/jobapplication/<tenant>/jobpostings/<slug>/applyflowpages?applyDetail=applyManually` lists; of 40 postings, 37 had questions and 35 disclosures. Only the current page is in the DOM. Loading the wizard creates a draft application (`POST …/jobapplications`); the next load resumes it from My Information, with what its pages saved.

- **Walking.** `browser_read` reads the page it is on and presses Save and Continue, for as long as a page saves with what it holds: a fresh draft's My Information does not, so a first read lists My Information, then My Experience and the questions from the API, and no disclosures. A fill saves the pages it can and is refused on the first one with a field the read did not reach, naming them; the next read walks the saved pages and lists them. Reading saves pages, as a fill does. The draft keeps a page's answers but not a consent's tick (`termsAndConditions--acceptTermsAndAgreements`), so a read never gets past that page: its own fields are read, and a page after it (Self Identify, seen on a US posting's disability form) never is.
- **My Information and the disclosures** are read from the page: each `[data-automation-id^=formField-]` with a `data-fkit-id`, which is the field's id (`name--legalName--firstName`, `address--countryRegion`, `source--source`, `personalInfoPerson--gender`, `termsAndConditions--acceptTermsAndAgreements`); a label ending in `*` is required. The address follows the country chosen (France: Department, the United States: State and County), and the disclosures the job's country (a non-US Personal Data Statement, a tenant's own fields), so they are read as the page shows them. The country holds the browser's locale's before any answer, the user's own country on their machine: it is the field's `default`, not `prefilled`, and its address fields are the ones read; so is the country phone code's. A field holding the account's value before any answer is `prefilled` with it.
- **My Experience** comes from the apply flow's sections, where a list's own fields are sections too, each hidden or required: the resume (`resumeAttachments--attachments`, required on 35 of 40), skills (`skills--skills`), and the first entry of Work Experience and Education, required where the list and the part are (`workExperience--jobTitle`, `education--degree`; required on 5 of 13 tenants walked). The school is a search prompt where the tenant lists schools (`education--school`), a text input elsewhere (`education--schoolName`): one field, `education--school`, a `typeahead` either way, since the schools API (`/wday/calypso/cxs/jobapplication/<tenant>/schools?search=`) answers on both. Websites, Languages and Certifications are lists atsfill does not fill (`myExperience--<label>`). Once a read reaches the page, it also lists what the page shows that no section names, such as an optional LinkedIn profile (`socialNetworkAccounts--linkedInAccount`).
- **The questions** come from `/wday/calypso/cxs/common/<tenant>/questionnaire/<id>`, public: `primaryQuestionnaire--<question id>`, `secondaryQuestionnaire--<question id>`. An answer's `branchingQuestion` is asked only after it: the follow-up's `shown_when` is `Is` that question on the answers branching to it, `AnyOf` them where several questions do, and it keeps its own `required`. A work experience's end date shows while "I currently work here" is unticked, the box's `default` `No`.
- **Lists from the API**, public under `/wday/calypso/cxs/jobapplication/<tenant>/values/`: the country phone codes (`phone/countryCodes`), degrees (`educations/degrees`), fields of study (`educations/fieldsOfStudy`, 324). The sources list there is empty: the source prompt is read by opening it.

Each rendering, from 40 postings' questionnaires and the pages walked:

| Rendering | Field type | Postings |
|---|---|---|
| text input; `textarea` (a Text question, a role description) | `text` | 40; 94 questions |
| a Number question | `text`, label `(a number)` | 4 questions |
| dropdown: a button (`aria-haspopup=listbox`) opening the listbox it `aria-controls`, named on some tenants only once it opens, options a moment later; a Single Select question, a degree | `choice`, the options read by opening it | 40; 259 questions |
| one checkbox: preferred name, terms, "I currently work here" | `choice` `Yes`/`No` | 40 |
| radio group: `previousWorker--candidateIsPreviousWorker` | `choice` | 13 |
| checkboxes, a Multi Select question | `multi_choice` | 18 questions |
| search prompt (`selectinput`) of one value: the source ("How Did You Hear About Us?", a tree of categories and items), the country phone code, a field of study | `choice`: the items, a category's in its place; the long lists from the API | 40 |
| search prompt of many values: skills; of one, searched only: a school, a city (`address--countryCity`, the cities of the form's country) | `typeahead` | 40; a city, 4 of 14 |
| phone number beside the country phone code (`phoneNumber--phoneNumber`), typed without the code | `phone` | 40 |
| date sections (`dateSectionMonth-input`, `Day`, `Year`): a Date question | `date` | 1 question |
| month and year sections: a work entry's From and To | `text`, label `(MM/YYYY)` | 13 |
| year section: an education entry's From and To | `text`, label `(year)` | 13 |
| file upload (`file-upload-input-ref`), an Upload Attachments question | `file` | 35; 5 questions |

## Filling

- `fill` loads the wizard again, from My Information, and on each page fills the answered fields the page shows, in its order: a country first, which renders its address fields. A page is read once each answered field shows its control; an upload's comes last.
- Text is typed key by key and the field left: some pages miss a value filled at once, showing it but saving none. A click right after typing can go unheard: a dropdown is clicked again until it opens.
- A dropdown is opened and its option clicked; a box or a radio clicked when it differs; a date section's content selected and typed over.
- On a questions page, a choice posts the questionnaire's definition (`…/questionnaire/<id>/definition`), and its reply renders the page again over the answers given meanwhile: the next answer waits for the reply, and one the reply still dropped, its post going out late on a busy page, is given again.
- A search prompt is driven from the keyboard, its input lying under the picked item and the page's sticky footer; a list is closed by Escape and a click outside it. Enter on an empty search opens the tree; after typing, it picks the only item that matches, at any depth, or lists them, and only the open list's rows count (`activeListContainer`), not the picked items. A search can miss an item the list holds (one with brackets in its name): the item is then looked for in the whole list. A one-value prompt has its item deleted (`DELETE_charm`) before another is picked.
- The phone number is typed without the country code the form shows; a number with another code is refused, naming the code's field.
- A list's first entry is added with its Add button when answers are given for it, in the group its name labels (`[role=group][aria-labelledby=Work-Experience-section]`, `Work-History-(Optional)-section` on some tenants). The page numbers each entry's fields its own way (`workExperience-8--jobTitle`), so the first entry's are read as `workExperience--jobTitle`.
- An upload first deletes the files the draft or the account profile holds (`delete-file`), then waits for the new file's name.
- A page showing a field the read did not list, optional or required, stops the fill before saving, so none is left empty on a `ready`. On a page the read did not reach, `refused: answer_not_taken` asks to read again, the pages before it now saved: a normal further round, and the next read lists the field. Where the read stopped on a page lacking only a required tick, no read can get there: `unhandled_rendering`, not read again. On a page the read reached, an answer opened the field, the page stays unsaved and no read would list it: an address field on My Information is the `other_country` known gap; any other is `unhandled_rendering`, such as a source item asking who referred the user, or having worked there before, which shows a required manager field and an optional employee id. A later entry of a list, which the draft holds, does not count. The wizard keeps only its last page and numbers entries its own way, so the adapter makes this check page by page and does not list the fields shown to the core (`ListsShownFields`).
- Save and Continue (`pageFooterNextButton`) is pressed again when the page neither moves nor shows an error. A page that shows errors (`errorHeading`, `inputAlert`) stops the fill: `refused: answer_not_taken`, with the page's message.

## Read-back and submit

- Each page's values are read before it is saved, once each answered field shows one (a picked item can leave the page for a moment): an input's value, a dropdown's text, the picked prompt items, the checked option's label, a box as `Yes`/`No`, the uploaded file's name, a date as `YYYY-MM-DD`, the phone with the country code beside it (`+44 1234567890`). Every page is saved as it is left, so the read-back is what the draft holds, read from the page, not from the server. Review repeats it without field ids.
- Submit is Review's `pageFooterNextButton`, which atsfill expects to post the application (`…/submit`); no send was made.

## Development

The Test user's accounts are one per tenant, created by the round (ADR 0013): `atsfill login workday --user test --url <posting> --create --email <address>`. Each posting is surveyed up to three times with `--data-dir`: a fresh draft's first dry run stops at the first page the read did not reach, My Experience where it shows a field no section names, else the disclosures.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `experience_entries` | a required list of entries on My Experience other than work experience, education and skills: websites, languages, certifications | `unsupported_flow` | 0 of 13 |
| `long_wizard` | a wizard of more than ten pages | `unsupported_flow` | 0 |
| `other_country` | a country answered other than the form's, whose address shows fields the read did not list | `unsupported_flow` | |
