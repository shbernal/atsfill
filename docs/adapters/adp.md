# ADP

ADP Workforce Now's career center, a different product from ADP MyJobs ([adpmyjobs.md](adpmyjobs.md)), observed on live postings on 2026-10-26 and 2026-10-29, headless Camoufox, with the Test user at 19 employers, the wizard walked to its last step on 16. The fixture page is [tests/pages/adp/app.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/adp/app.html), the sign-in, the talent community step and a wizard of each rendering, its API served by `tests/test_adp.py`.

## Where the form is

- A posting is `workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=<employer>&ccId=<career center>&jobId=<id>&lang=<locale>`, one employer per `cid`, a single-page app at that one URL. `ccId` is `19000101_000001` at most employers (25 of 30), another at the rest.
- Signed in, the posting is `…/mascsr/applicant/mdf/recruitment/postLogin.html?cid=<employer>&ccId=<n>&lang=<locale>&jobId=<id>`: Apply (`#recruitment_jobDescription_candidateApply`, twice, one hidden) opens the application's wizard at that address, and on a posting with a draft, Complete Your Application (`#recruitment_jobDescription_complete`) reopens it; some open the wizard straight away. Signed out, that address redirects to ADP's login page. The public posting's Apply shows the talent community step whatever the session.

## Account

Applying needs a candidate profile with the employer, made on its talent community step. One employer's session is not another's: there the candidate profile's endpoint answers 500.

- The career center's Sign In (`&selectedMenuKey=SignIn`) asks "Email or Mobile Number" (`#guestEmail`), then Continue. Some employers first show a privacy notice, Consent & Continue or Decline & Close, also before Join Our Talent Community.
- An email the employer does not know gets "Tell us about yourself." instead, the talent community step: First Name (`#guestFirstName`), Last Name (`#guestLastName`), Email (`#guestEmail`) and Mobile Number (`#login_view_phone`, required, a country select beside it; a number typed in E.164 picks its country), then Continue. It warns that the email or mobile number identifies the person: a later use by someone else overrides the profile's name and history. There is no password. The alternative is a Google, Facebook or LinkedIn sign-in. `login --create` gives the details (`asks`: name, phone).
- Either way, "Get your verification code" (`.otpDialogContent`) follows: the code to the mobile number or the email (`#phoneIdentifierId`, `#emailIdentifierId`), then Send (`#recruitment_login_guestLogin`). The email gets a six-digit code, typed into `#oneTimePassWord` and Verify (`#recruitment_login_verifyOTP`), which moves to the signed-in career center a moment before the session holds.
- The session is the `.adp.com` cookies (`SMSESSION`); signed in, `…/mascsr/applicant/careercenter-auth/events/staffing/v2/job-applicants?cid=<employer>` answers the candidate profile, signed out it redirects to the login page: the signed-in check.

## The form

The wizard is one page, its steps in a bar, Next (`#ja_sv_cw_next_footer_btn`) moving step by step on the client: it saves nothing. Opening it POSTs `…/staffing/v1/job-application.submit` once, which leaves a draft the posting lists "Pending Submission"; uploading a file POSTs `…/work-fulfillment/document.upload`. Next stays on a step whose required fields are empty or wrong and says "Correct the information in highlighted fields.", each field's error in its label ("Address Line 1 must not exceed 30 characters"). A draft reopens on the step it was left on; Previous goes back.

`…/staffing/v1/job-application.submit/meta?cid=<employer>&ccId=<n>` describes it, the questionnaire only with the posting's `jobId` in a `requisitionoid` header: `data.control.questionnaire.questions` (each `questionID`, `questionText`, `answerTypeCode`, `requiredIndicator`, `answerOptions` of `answerID` and `answerValue`), `data.control.attestations` (the text to sign), `data.control.customIndicators` (`VSID`, `GENDER`, `RACE`, `VSID_VeteranEnabledIndicator`, `VSID_DisabilityEnabledIndicator`: which Self-ID questions show), and under `meta["/data/transforms"]` each profile path's `optional`, `hidden` and code list (states as `US-Illinois`, currencies, the self-ID's codes).

Every employer of the 16 showed the same steps: Personal Information, Resume, Questions, Voluntary Self-ID (14), Review Your Application, Self-Attest & Submit.

| Step | Field | Type | Rendering | Seen on |
|---|---|---|---|---|
| Personal Information | first, middle, last name | `text` | inputs (`#personalInfomationFirstName`, …), the profile's name prefilled | 16 |
| | email | `text` | read only, the verified email | 16 |
| | mobile, home phone | `phone` | `react-phone-number-input` (`#personalInfomationMobileNumberError`, `…ErrorMessage`), keeping its country's `+1` once cleared; the mobile the sign-up's | 16 |
| | country | `choice` | a react-select combobox (`#PersonalAddress_country`), its options `[id*=-option-]`, the pick in `…__single-value` | 16 |
| | address lines 1 to 3 | `text` | inputs, line 1 a Google Places search that also takes a typed line; 30 characters at most | 16 |
| | city, postal code, county | `text` | inputs; once the country is picked the postal code is "Zip Code" and County shows | 16 |
| | state | `choice` | a textbox until the country is picked, then a `wfn-dropdown-list` (`DC - District of Columbia`), its options `role=option`, its select in its shadow root | 16 |
| Resume | résumé | `file` | Upload File (or Enter Text) and a file input; Next is disabled until one is uploaded | 16 |
| Questions | `Yes/No Buttons` | `choice` | `sdf-radio-group`, radios `#questionYes_<n>`, `#questionNo_<n>` | 12 |
| | `Drop List` | `choice` | `sdf-select-simple#question_<n>`, its items `sdf-select-item` in a floating pane | 10 |
| | `Comment` | `text` | `textarea#question_<n>` | 8 |
| | `Text Field` | `text` | `input#question_<n>` | 4 |
| | `Multi-Select Menu` | `multi_choice` | a react-select combobox, picks in `…__multi-value__label` | 2 |
| | `Money Field` | `text` and `choice` | the amount `input#question_<n>`, shown `50,000.00` once left; the currency a react-select `#question_currency_type_<n>`, `United States Dollar ( USD )` | 2 |
| | `Number Field` | `text` | `input#question_<n>`, shown formatted | 1 |
| | `Date Field` | `date` | `input#question_<n>`, typed MM/DD/YYYY | 1 |
| | desired pay | `choice`, `text`, `choice` | where the meta does not hide `requestedPayRate`: Annually or Hourly (`sdf-radio-group`), the amount (`#desiredSalaryId`), the currency (`sdf-select-simple#add_info_select_box`), or at one employer no list and `$` fixed before the amount, read back as US dollars | 6 |
| | comment | `text` | "Do you want to tell us anything else about yourself?" `textarea#commentTextArea`, where the meta does not hide `comment` | 6 |
| Voluntary Self-ID | gender, ethnicity, race | `choice` | react-select comboboxes (`#vsidGender`, `#vsidEthinicity`, `#vsidRace`), race enabled once ethnicity is Not Hispanic or Latino; a decline box | 14 |
| | veteran, disability | `choice` | `sdf-radio-group`s whose radios' values are the meta's codes (`Y`, `N`, `D`), their labels the meta's but for a period | where the indicators enable them |
| Self-Attest & Submit | the attestation | `choice` (Yes, No) | the employer's text, then "Yes, I agree to sign electronically." (`input[name=self_att_agree_chk]`, hidden, its label clicked) | 16 |

- The read opens the wizard and goes back to its first step: Personal Information from the page, each box required where its label carries an asterisk; the questionnaire, the desired pay, the comment, Voluntary Self-ID and the attestation from the meta. A question of another type is listed unfillable.
- The fill answers each step and Next, up to the attestation, which it agrees to; the read-back is each step's values before its Next, an amount as typed.

## Known gaps

None met.

## Submitting

Submit (`#submitApplication`), on the attestation, is the send; Apply and the steps before it send nothing. Not seen yet: the evidence is the wizard closing.
