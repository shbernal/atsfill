# Dayforce

Dayforce's career sites (`jobs.dayforcehcm.com`) and their guest application, observed on live postings, last on 2026-10-22. The fixture page is [tests/pages/dayforce/apply.html](../../tests/pages/dayforce/apply.html), served with the JSON it and the adapter read in `tests/test_dayforce.py`: one example of each rendering below.

## Where the form is

- A job is `jobs.dayforcehcm.com/<culture>/<namespace>/<career site>/jobs/<id>`; the culture redirects to the site's own (`en-US` to `en-AU`), and the wizard's steps drop it. The double-send key is `https://jobs.dayforcehcm.com/<namespace>/<career site>/jobs/<id>`.
- Apply offers "Apply without an Account" (`…/apply?flowSelection=true`); atsfill goes straight to the guest form, `…/jobs/<id>/apply/manualApplication?applicationSource=Manual`. A closed posting shows no form: `posting_gone`.
- The site context (`/api/geo/<ns>/sitecontext/<ns>/<site>/<culture>?jobPostingId=<id>`) and the application template (`/api/geo/<ns>/jobapplication/<ns>/<culture>/<job board id>/<id>`) are public JSON and list every section, field and question. They leave out the options of the country and contact method dropdowns, bundled in the page, so there is no `read_api`: the Test user, and a user, need those options.
- The page keeps its draft in `sessionStorage` (`jobPostingApplication-<id>-<ns>`); `fill` clears it and loads the form again.
- Dialogs cover the form on load: the employer's privacy policy where `enablePrivacyPolicy` (an agree box and Save; Cancel leaves the form, so atsfill accepts it, a declared side effect), the SMS consent where `hasActiveSMSProvider` (two radios, Save), the cookie banner (rejected). Accepting the policy is kept in the `SiteConfig` cookie of the browser profile.
- The wizard is Candidate Info, one page per questionnaire ("Questionnaire 1 of 4", then its name; some sites show no counter), an Assessment step on some sites, then Submit. Next (`[test-id=application-next-step]`) validates the step (`POST …/JobApplication/validatesections`, `…/canuserreapply`) and stores nothing.
- An assessment on another site before Submit (`[test-id=embeddedassessment-openassessment-button]`) is `unsupported`, `external_step`.

## Reading the form

- Fields come from the template; ids are the form's own keys (`firstName`, `countryCode`), `workHistory.<key>` and `educationHistory.<key>` for a history entry, `workHours.*`, `q<question id>` for a question. The page's bundle maps template field names to keys (`EMPLOYERNAME` is `companyName`).
- Labels are the template's; the page's own are localized (`Postcode`, `Country dialling code`), so atsfill finds controls by id and `test-id`, never by label.
- Questionnaire controls are `jobPostingApplication_sections_<page>_questionnaire_questions_<n>_<part>`: `<n>` counts the questionnaire's statements too, except on the disability form, laid out on its own.
- Dropdowns are Ant Design selects: `input[role=combobox]` in `.ant-select-selector`, options in a dropdown portaled after `main` (`.ant-select-dropdown` holding `#<id>_list`), a virtual list that renders a screenful at a time. Their options are read by scrolling it. Some are searchable, some `readonly`.
- An option of a question shows its `description` (falling back to `displayName`); the option is named by it.

Each rendering, and on how many of the 27 surveyed postings' templates it shows (2026-10-20):

| Page | Field type | Postings |
|---|---|---|
| text input, `textarea` (name, address, city, postal code, LinkedIn) | `text`, `max_length` from `maxlength` | 27 |
| email and its confirmation, not in the template | `text` | 27 |
| dropdown, searchable or `readonly` (prefix, suffix, contact method, source, country) | `choice`, options read from the list | 27 |
| state/province: disabled until a country is picked, its list the country's | `typeahead` | 27 |
| mobile, home phone: dial code dropdown (`🇺🇸 +1`, no `title`) and number | `phone` | 27 mobile, 18 home |
| fax, pager: number only, 7 to 15 characters | `phone` | 3, 4 |
| "how did you hear" source asking for details (`requiresAdditionalExplanation`): `candidateSourceDescription` shows | `text`, `shown_when` `Is` the source on those | 27 |
| SMS consent dialog | `choice`, `No`/`Yes` | where enabled |
| resume, cover letter, additional documents, `input[type=file]` | `file` | 25 |
| history entry (employment, education): "Add …" (`[test-id=add-<section>-record]`), fields, Update | one entry, the fields' types, `max_length` read from an entry `read` adds; required only with the section | 16 each, required on 1 |
| G.P.A. of an education entry: a number box (`role=spinbutton`) that drops what is not a number, and `4.0` to `4`, when it loses focus | `text`, labelled `(a number)`, read back after it loses focus | seen required on 1, 2026-10-22 |
| work hours: a box per day, each ticked day a start and end `input[type=time]`, comments; open from the start, saved only with a day ticked | `workHours.days` `multi_choice`, `.start`, `.end` (`HH:MM`, every day alike), `.comment` | 5, required on 2 |
| question type 1, a statement | not a field | 7 |
| type 2 radio, type 5 yes/no: `.ant-radio-group` | `choice` | 13, 23 |
| type 4 dropdown | `choice` | 17 |
| type 3 text | `text` | 24 |
| type 6 box (`…_isChecked`) | `choice`, `Yes`/`No` | 2 |
| type 7 boxes (`…_selectedOptionIds`, `.ant-checkbox-group`) | `multi_choice` | 11 |
| an option with `hasFollowUp`: "Enter additional comments here" shows, required (`…_answerFollowup`; one per ticked option on a box group, `…_answerFollowups_<option id>`) | `q<id>.followup`, `text`, required, `shown_when` `Is` the question on those options; on a box group one per option, `q<id>.followup.<option id>` | 19 |
| EEO, veteran (radios), disability form: name and date prefilled and disabled, employee id disabled for a guest, boxes | `choice`, `multi_choice`; its text questions, the page's own, are not fields | 5 |
| candidate acknowledgement box on Submit (`#jobPostingApplication_userAcknowledged`) | `choice`, `Yes`/`No` | 26 |

A conditional field is labelled `(asked when '<field>' is <option> or <option>)`.

A template field, section or answer type outside this table is listed as `unhandled_rendering`.

## Filling

- The resume goes first: uploading it (`POST …/FileService/uploadresume`) imports it over the Test userl information, so nothing is typed until the upload answers.
- The country goes before the state. A dropdown is opened from the middle of the window, the step's button bar covering the bottom; its option is clicked in the same script that finds it, the virtual list rendering its options anew as it moves. A searchable one is typed into first; a `readonly` one is scrolled to the option.
- A phone number picks the longest dial code its digits start with, the main country of a shared one (`+1` is the United States), and types the rest.
- A section saves with its Update, the first submit button after its first field; a click while a dropdown is still closing is lost, so Update is clicked until the section closes. An open section keeps Next from going on.
- Some sites refuse a history entry dated in the future without an error; the entry stays open and the wizard on Candidate Info.
- A comment box is filled once its option is picked and the box shows, each of a box group's with its own answer. The wizard keeps only its last page, so the fields shown once filled are the comment boxes and source details each page showed before Next; a comment box showing that `read_form` did not list is reported by its element id.
- Next can go unheeded right after a save; atsfill clicks it again, up to three times, until the step's controls change, the button reads Submit, or an error shows; an error adds its own `<id>_help` element, not a control. Submit is never pressed by `fill`.

## Read-back and submit

- Nothing but the files reaches Dayforce before submit. The read-back is the page, taken from each step before leaving it: inputs' values, the dropdowns' shown item, the dial code and number, the checked radio's and boxes' labels, the file name shown under its input, the SMS radio as the dialog held it, and the acknowledgement box on Submit.
- Submit has not been observed. The page loads an invisible reCAPTCHA (`recaptcha/api2`), which may judge the send.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `references` | the references section, entries added like a history | `unsupported_rendering` | 3, required on 1 |
