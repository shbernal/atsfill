# ADP MyJobs

ADP's candidate experience (`myjobs.adp.com`), a different product from ADP Workforce Now's career center ([adp.md](adp.md)), observed on live postings on 2026-10-26 and 2026-10-29, headless Camoufox, with the Test user at four employers. The fixture page is [tests/pages/adpmyjobs/app.html](../../tests/pages/adpmyjobs/app.html), the sign-in, a posting and a workflow of each kind of section, its API served by `tests/test_adpmyjobs.py`.

**Apply sends the application.** The form shows only after it: every form command, `read` and `fill` included, sends the application on a posting the account has not applied to, and needs `adpmyjobs` in `submit-allowed` (ADR 0006).

## Where the form is

- A posting is `myjobs.adp.com/<career site>/cx/job/<id>`, one employer per career site, a single-page app. Signed out it shows the employer's header and Sign in; signed in, that address renders nothing, and the posting is `/<career site>/cx/job-details?reqId=<id>`. A posting gone shows "Problem with the service", and no Apply.
- Apply POSTs `my.adp.com/myadp_prefix/mycareer/api/staffing/v2/job-application/assignment-creation` (`{reqId}`) and the application exists from then on: `myjobs.adp.com/cx/staffing/v2/job-applications` lists it `APPLICATION_SUBMITTED`, label `CONTINUE`, My Jobs says "Application submitted!", and the posting shows Applied instead of Apply. There is no withdraw.
- What follows is the application's workflow, `/<career site>/cx/app?reqId=<id>&assignmentId=<application>&workflowComponent=<section>`, one section at a time and in order. A posting applied to is continued there, at the first section not completed: the address without `workflowComponent` spins forever.
- The workflow (`recruiting-questionnaires/WORKFLOW?assignmentID=`) lists its sections, each with `component`, `completionStatusCode` and `isBackEnabled` (false but on E-Signature): Data Privacy Statement, Contact Information, Select Source, Preliminary Questions, Documents, Electronic Signature Notice and Consent, Equal Employment Opportunity Information, Application Form, in an order and selection each employer sets. On a new application it lists only the first section, a privacy statement, until that one is saved.
- Each section's Next saves it (`recruiting-questionnaires/<application>/answers?formType=<section>&submit=true`, then a `job-application.change` event) and moves the address to the next section. The last section's Next completes the application: the page says "Application Submitted!", at the same address.

## API

The page calls ADP's API with the token it keeps in `sessionStorage` (`myJobsToken`) as the `myjobstoken` header and the time zone as `tz`; `my.adp.com`'s endpoints also take `rolecode: manager`, and the form endpoints (`/api/rm/`) the career site's id as `careersiteid`, from `myjobs.adp.com/public/staffing/v1/career-site/<career site>` (`settings.externalId`). Without its headers an endpoint answers 400 "Missing required headers" or redirects to `sorry.adp.com`.

- A section's questions: `recruiting-questionnaires/<component>.<id>?assignmentID=`, each with `questionID`, `answerDataTypeCode`, `questionText`, `requiredIndicator`, `disabledIndicator`, `answerDefaultValue` (the profile's value, or the answer saved), `links` to its options' codelist (`/codelists/...`, `listItems` of `codeValue` and `longName`), `dependencies` (the questions an answer shows), `questionLabels` (`postcode_USA_label`: "ZIP Code:" for Postcode in the US) and `validationRules.conditionalRequiredValidations` (the questions an answer requires). `navigation=next` answers 401 on a section not open.
- The Application Form is a form of pages: its questionnaire lists them as its sections, the open page's with its questions, the others by `sectionID` once the form is open, and a last page of its own (`sectionReview`) reviewing the answers. Not open, it lists no pages.
- Preliminary Questions: `staffing/v2/job-application/workflow/<application>/prescreen.questions`, each with `questionType` (Single choice, Free form, Instructions), `orderId` and `possibleAnswers`, an answer branching to a later question (`branchingQuestionId`), ending them (`endOfQuestionIndicator`), or knocking the application out (`knockoutRule`).

## Account

- Sign in goes to `myjobs.adp.com/<career site>/auth`, slow to render (~20 s): Email Address and Continue, or Use phone number, or social sign-ins. There is no password.
- An address the career site does not know opens "Create your candidate profile": the email, Phone Number (a country select and typed digits, required; a number typed in E.164 picks its country), First Name, Last Name, the employer privacy policy box, Continue; then "Select your verification method": Text Message or Email Message, Send passcode. The profile exists from that Continue on. A sign-in stops there, `refused: no_account`; `login --create` gives the details (`asks`: name, phone). Development uses a 555-01xx number only.
- A known address goes from Continue straight to the passcode, a six-digit "verification code from ADP" from `SecurityServices_NoReply@adp.com`, valid 15 minutes. Typed, it signs in on its own, then the employer's privacy policy shows in a dialog to Agree to; Agree goes to `/<career site>/cx`.
- ADP keeps one identity per email: a second career site's profile showed the name the first was created with.
- Signed out, `myjobs.adp.com/cx/staffing/v2/job-applications` redirects to `/workforcenow/login.html`: the signed-in check.

## Reading and filling

Each question is an `adp-form-group[data-name=<questionID>]` whose class names its widget (`van-type-sdf-input`), from ADP's web components; Preliminary Questions are one form, `#prescreeningForm`. A field's id is `<component>.<questionID>`.

| Type | Field | Rendering | Seen on |
|---|---|---|---|
| `TEXT`, `EMAIL` | `text` | `sdf-input`'s input; disabled with the profile's value (email, name) | every employer |
| `PHONENUMBER` | `phone` | `sdf-phone-number-input`: a country select and an input showing `+1 202 555 0143`; E.164 typed picks the country | every employer |
| `SELECTION`, `COUNTRIES`, `STATES`, `LOOKUP` | `choice` | `sdf-select-simple`: its frame opens a floating pane of `sdf-select-item`s, a long list its first items and a filter box (`#select-filter-input`); the pick shows in `#selected-label` | every employer |
| `RADIO` | `choice` | `sdf-radio-button[value=<code>]`, the label in its shadow root | EEO questions |
| `BOOLEAN` | `choice` (Yes, No) | `sdf-radio-button` `true` and `false` | SMS opt-in, AI opt-out |
| `CHECKBOX` | `choice` (Yes, No) | `sdf-checkbox`, `aria-checked` | the statements' agree box |
| `UPLOAD` | `file` | an upload button opens a pane with a file input and Save; the question then shows the file's name | Documents, 2 employers |
| `ESIGNATURE` | `text` | Accept and Decline radios, and the full legal name typed | Application Form, 1 employer |
| prescreen Single choice | `choice` | `sdf-radio-group#<question>`, or a `sdf-select-simple` for three answers or more | every employer |
| prescreen Free form | `text` | `sdf-textarea` | 2 employers |

- The read lists the open sections from the API; a section completed by an earlier run is sent and has no way back, and is left out. A first section that is only a statement to agree to (one required box) is agreed and saved by the read, as ADP lists the rest only after it.
- A question that shows on another's answer has that condition (`dependencies`), one required on another's answer has it as `required_when`; a prescreen question shows once an answer leads to it, an Instructions passing on its own condition. `INSTRUCTIONS`, `HEADER` and disabled questions without a value (the e-signature's date) are not fields.
- The fill answers each section in order, each question once it shows, and Next; on a form of pages each page and its Next, checking first that the page open is the one to fill (a form reopened on its review goes back with Previous). It stops before the last Next: on the last section's last page, or its review. A page that shows a question the read did not list stops the fill: the Application Form, when it was not open at the read, lists its pages once open.
- A section's Next that does not move names the errors kept for screen readers (`rm-sr-field-error`): "ZIP or postcode is not in state", "City [London] is not a valid city within State [DC]". ADP checks the address's city and ZIP against its state.
- The read-back is each section's values on the page before its Next.

## Submitting

The last section's Next is the send's last click (the application itself was sent by Apply); the evidence is the page saying "Application Submitted!". Seen once, on an application the Test user had opened.
