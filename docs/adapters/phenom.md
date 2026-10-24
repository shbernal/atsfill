# Phenom

Phenom's apply wizard on employers' careers sites, observed on live postings, last on 2026-10-22. The fixture pages are [tests/pages/phenom/job.html](../../tests/pages/phenom/job.html) and [apply.html](../../tests/pages/phenom/apply.html), configured by [form_schema.json](../../tests/pages/phenom/form_schema.json), [Messages_en_us.json](../../tests/pages/phenom/Messages_en_us.json) and the loaders' answers in [references.json](../../tests/pages/phenom/references.json), served as `careers.acme.example` and Phenom's CDN in `tests/test_phenom.py`: a four-step wizard with one example of each rendering below.

## Where the form is

- Each employer has its own host (`careers.<employer>.com`, `jobs.<employer>.com`). A job is `/<site>/<lang>/job/<jobSeqNo>[/<slug>]`, the site a country (`us`, `gb`, `ca`) or `global`. The double-send key is the host and the `jobSeqNo`.
- The job page's Apply link (`data-ph-at-id=apply-link`, `phw-tk=apply_click`) leads to the wizard, `/<site>/<lang>/apply?jobSeqNo=` (`hvhapply` for some hourly roles, the same configuration), or to the system behind Phenom: Workday, Taleo, iCIMS, ADP, Oracle, Ashby. The job page's `phApp.ddo.jobDetail.data.job.externalApply` says which. The backing system does not decide it. A link to another host is `unsupported`, `external_step`.
- The wizard is react-jsonschema-form (`form.rjsf`) with Phenom's own widgets, three to eight steps. Only the current step is in the page. Next (`button#next`) posts the step to `/applySubmit`, which creates a Phenom candidate profile on the first step and checks the step server side; the last step's Submit sends the application to the backing system.
- A cookie banner (Phenom's own `[ph-module=gdpr]`, TrustArc, OneTrust, cookieconsent's `.cc-window`) shows at any time over the form; the blocker's lists hide it. A loading overlay (`.overlaybg`) covers the form while a step loads.
- A closed posting's job URL and wizard URL both answer `404` with Phenom's error page, its `phApp.pageName` `"404"` ([tests/pages/phenom/404.html](../../tests/pages/phenom/404.html)): `unsupported`, `posting_gone`.

## Reading the form

The apply page's HTML assigns `phApp` (`refNum`, `locale`), `phApp.ddo.response.response` (the backing system as `applyATS`, `jobCountry`, `jobData` with the questionnaires' ids and `has…Questionnaire` flags) and `phApp.sessionParams.csrfToken`. The page's scripts' world is out of the adapter's reach in the browser, so the read parses those literals from the HTML, over HTTP (`read_api`) or from the loaded page (`read_form`), and both then read the same way:

- The configuration is public on the CDN: `cdn-prod-static.phenompeople.com/CareerConnectResources/<refNum>/<applyATS, lowercased>/external/careersite/singlepage/schema/form_schema.json`, its labels in `bundles/Messages_<locale>.json` and `Messages_default.json` (`{{key}}` in titles and options).
- `schema` and `uiSchema` describe every step's fields; a top-level property's `uiSchema.nav` names its step, a key of `stepConfig` (or its `stepId` or name). A step whose `stepRules` hold on `phApp` (no secondary questionnaire for the job) is not shown.
- A loader (`apiConfig.payload`, `%%phApp.…%%` filled in) posted to `/applySubmit` with the token serves the rest: the job's questionnaires (`asyncfield`: `getWorkdayJSQs` by questionnaire id, SuccessFactors' `getJsq` by job, which also takes `jobSeqNo`, `jobId`, `locale` as `en_US` and an `apTxnId`), the country's fields (`getCountrySpecificFields` with the `countryCode` the country holds before any answer, under `cntryFields.`), lists (`asyncselect`, by the uiSchema's `datasourcepath`, `responseField`, `displayTextField`, `valueField`), and a search's first suggestions (`asynctypeaheadv3`, by `ui:options`' `dataFieldMapKey`), listed as its options: some of what it takes, not all. A questionnaire with an empty loader is built by a workflow (`psqToSchema`, Taleo's prescreening) from `jobData.<sourceName>.questions`: each question's id is `<targetName>.questionnaireId.<questionId>`, a follow-up names its parent question and option.
- A list a workflow loads when another answer changes (`workflow`, an `onchange` `http` node on `%%formData.<parent>%%` and a transform to `properties.<list>`) lists the options under the parent's answer before any: its `preFillWindowObj` value or `default`, else the job's country where the parent lists it, else its first option, the label naming it: `State (options for 'Country' 'United States')`. A list under two answers (a region under state and country) follows the one that is itself a dependent list. An `asyncselect` whose own loader names an answer (`dependentKey`, an entry's country) depends on it the same way, within its entry. The job's country is a name and a country list's values are codes (`USA`): the phone code list (`USA_1`, `United States of America (+1)`) maps one to the other.
- Phenom's applicant-source workflow fills its lists from a `temp_data` `jsonData` table: the sources, or the sub-sources under each, which are listed together. A `dependent_utm` tree (sources under each "how you heard" answer, `sourceTypeSchema`) is listed the same way, a hidden sub-source (`show: false`) left out.
- `allOf` rules add properties when their `if` holds: on the page's own data (a hidden field, `locale`) it is decided at once; on answers it is the field's `shown_when`, a `const` or `enum` an `Is` on the options' labels, `not` negating it, `anyOf` and `allOf` joining parts, and a resume given (`resumeName` `isNotEmpty`) `Is("resume")`; a field a rule adds keeps the rule's `required`, required whenever shown, and a rule requiring a field already there is its `required_when`. A field asked only without a resume is not read and, where it is required, the resume is. A job question's `parentQuestionId` and `parentOptionId` are its `shown_when`.

A top-level list that holds an answer before any (its `default`, the job's country) has that option's text as the field's `default`, the page's, not the account's, so a required one is still answered; a signature's date (`defaultTodayDate`) is `prefilled` with today's, the only one the page takes; a `removeField` common rule leaves its field out; a `maxLength` is the field's.

A field's id is its schema path, dotted (`phoneWidget.phoneNumber`, `jsqData.QUESTIONNAIRE-3-185.a`, `experienceData[0].fromTo.startDate`), which is the control's DOM id, a radio group's `name`, or a checkbox group's boxes' id without `_<n>`; the resume is `resume`. Required: the parent's `required` list or `ui:required`.

Each rendering, and in how many of the 17 configurations read out of 23 companies (2026-10-21):

| `htmlType` (`ui:widget`) | Page | Field type | Configurations |
|---|---|---|---|
| `text`, `signature`, `number` | text `input` | `text` | 17 |
| `textarea` | `textarea` | `text` | 10 |
| a `phone` name (`phoneNumber`, `homePhone`), `phoneNumber_v2`'s number | phone input; `tel-national` beside a country code control (`(+1)` in its option, `USA_1` as its value) takes the number without the code | `phone` | 17 |
| `choice`, `choiceKeyValue`, `choiceKeyValueV2`, `country`, `asyncselect`, `sourceTypeSchema`, `dependent_utm`, `single_utm`, a string with an `enum` | native `select`; a dependent list loads a moment after its parent | `choice` | 17 |
| `typeahead` | typeahead listing its options once opened (`.rbt`, `input[data-attribute=typeAHead]`, `.rbt-menu .dropdown-item`; the menu takes the input's id) | `choice` | 2 |
| `asynctypeaheadv3` (school, employer), an array of picks | typeahead suggesting only what is typed, its input not marked as one; a menu that says "Searching…" is waited out; with `allowNew`, Enter takes a name it does not list | `typeahead` | 3 |
| `checkbox`, `valuecheckbox`, a boolean | single checkbox (consents, opt-ins), clicked in the page | `choice`: `Yes`/`No` when required, `No`/`Yes` otherwise | 17 |
| `multipleChoice` (`checkboxes`) | checkbox group | `multi_choice` | 1 |
| `radio`, (`radio`, `radiov2`) | radio group, `Yes`/`No` for a boolean | `choice` | 9 |
| `calendar`, `date` | react-datepicker, day: month and year lists, else the month shown and previous and next month buttons; a typed date does not reach the form | `date` | 10 |
| a questionnaire's `format: date` | the browser's `input[type=date]`, typed as `YYYY-MM-DD` | `date` | 1 |
| `daterangepicker`, `format` `MM/yyyy` | two month pickers (`.react-datepicker__monthPicker`, years in `select.range-select` or paged in `.react-datepicker-year-header`), `fromTitle`/`toTitle`, required by `input1Required`/`input2Required`; read back as `MM/YYYY` whatever the order shown | `text`, label `(MM/YYYY)` | 13 |
| `daterangepicker`, `format` `yyyy` | two year pickers (`.react-datepicker__year--container`, twelve years a page) | `text`, label `(year)` | |
| `array-of-object` | entries: the first, where `minItems` or `minItemsRequired` shows one before Add is clicked | as their fields | 13 |
| `object`, `phoneNumber_v2`, `asyncfield`, `dynamicschema` | their fields | as their fields | 17 |
| the resume upload (`.resume-upload-wrapper input[type=file]`), `cloudWidget.commonCloudOptions`: required where its `resumeInfoText` has a `*` or a `resumeMandatory` rule applies, the file types from it and `suppResFileType`; taken once `/fileUpload` answers or the page shows its name (at times in a hidden span), then parsed at once into the form | | `file` | 17 |
| `markdown`, `label`, `hidden`, `summary`, attachments (`additionalattachment`, `…_v2`, `file`, `dropbox`, `onedrive`) | | not read | 17 |

Reading takes a page load and 1 to 8 seconds of requests.

## Filling

- Filling clears the site's storage and cookies and opens the wizard anew. The resume is uploaded first and the page's requests left to settle: its parse fills and clears fields when it comes back.
- Each step's answered fields, those the page shows in the configuration's order (a country before its states), are filled as the page renders them; then the step is scanned again, for a field an answer showed (follow-ups three deep), and filled until its read-back holds after two seconds, six rounds at most. A field never shown has no read-back. The wizard keeps only its current step, so the fields shown once filled are those each step showed before Next. A control disabled until another answer waits a round; a text box that became a list (a region, once the state is set) is filled as one.
- A list is set even to the value it holds, cleared first, so a list depending on it loads; a dependent list without the answer is set once more after its parent is set again.

## Read-back and submit

- The read-back is each step's controls, read before Next: values, a select's text, the checked radio's or boxes' labels, a box as `Yes`/`No`, dates as `YYYY-MM-DD` from the picker's placeholder, a national phone with its code.
- On one site, an employer search with `allowNew` reads back its pick, yet the step still finds it empty (`is a required property`) and does not pass.
- Submit is the last step's submit button; the evidence is `/applySubmit`'s answer to the post with `isLastStep: true`. No send has been made through this adapter, so the end of a send is unobserved.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `first_step` | a first step other than the form (`applicantAcknowledgement`, `email`, `login`, `page=terms`) | `unsupported_flow` | |
| `long_wizard` | a wizard of more than twelve steps | `unsupported_flow` | 0 |
| `calendar_without_month` | a react-datepicker with neither month and year lists nor the month shown | `unsupported_rendering` | 1 |
