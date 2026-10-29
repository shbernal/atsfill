# ADP

ADP Workforce Now's career center, observed on live postings on 2026-10-26 and 2026-10-29, headless, with the Test user at three employers, the wizard walked to its last step on one. No adapter yet: Apply opens a sign-up of name, email and mobile number verified by an emailed OTP, the shape ADP MyJobs signs in with, then the application's wizard.

## Where the form is

- A posting is `workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=<employer>&ccId=<n>&jobId=<id>&lang=<locale>`, one employer per `cid`, a single-page app at that one URL.
- The posting page has Apply (`#recruitment_jobDescription_candidateApply`, twice, one hidden). Some employers first show a privacy notice, Consent & Continue or Decline & Close. Apply opens, at the same URL, "Join Our Talent Community": the step every application starts with.

## Account

Applying needs a candidate profile with the employer, made on the step below.

- The step asks First Name (`#guestFirstName`), Last Name (`#guestLastName`), Email (`#guestEmail`) and Mobile Number (`#login_view_phone`, with a country code dropdown, required), then Continue. It warns that the email or mobile number identifies the person: a later use by someone else overrides the profile's name and history. There is no password. The alternative is a Google, Facebook or LinkedIn sign-in.
- Continue opens "Get your verification code" (`.otpDialogContent`): send the code to the mobile number or the email (`#phoneIdentifierId`, `#emailIdentifierId`), then Send (`#recruitment_login_guestLogin`). The email gets a six-digit code, typed into `#oneTimePassWord` and Verify (`#recruitment_login_verifyOTP`).
- 14 of 15 employers showed the code dialog after Continue; on the 15th the step had no mobile number field and was not taken further.
- The step asks the same at an employer where the address already verified: nothing tells a known address before the code.
- Verify opens the application at `workforcenow.adp.com/mascsr/applicant/mdf/recruitment/postLogin.html?…&requisitionId=…`, "Applying for <title>": a wizard of Personal Information (name, the verified email, read only, mobile and home phone, address), Resume (upload or text, and attachments), Questions, Voluntary Self-ID on some employers, Review Your Application, then Self-Attest & Submit, the send. Next moves step by step; Finish later keeps a draft.
- At a second employer the wizard opened on Resume, Personal Information marked done: whether the profile is shared across employers is not settled.
- Signed in, the posting is `workforcenow.adp.com/mascsr/applicant/mdf/recruitment/postLogin.html?cid=<employer>&ccId=<n>&lang=<locale>&jobId=<id>`: Apply opens the wizard without the talent community step, and on a posting with a draft, Complete Your Application (`#recruitment_jobDescription_complete`) reopens it, at the first step. The public posting page shows the talent community step whatever the session.
- The session is the `.adp.com` cookies (`SMSESSION`); signed in, `…/mascsr/applicant/careercenter-auth/events/staffing/v2/job-applicants?cid=<employer>` answers the candidate profile.

## The form

The wizard is one page, its steps in a bar, Next (`#ja_sv_cw_next_footer_btn`) moving step by step on the client: it saves nothing. Opening it POSTs `…/staffing/v1/job-application.submit` once, which leaves a draft the posting lists "Pending Submission"; uploading a file POSTs `…/work-fulfillment/document.upload`. Next stays on a step whose required fields are empty and says "Correct the information in highlighted fields.", each field's error under it.

`…/staffing/v1/job-application.submit/meta?cid=<employer>` describes it: `data.control.questionnaire.questions` (each `questionID`, `questionText`, `answerTypeCode`, `requiredIndicator`, `answerOptions`), `data.control.attestations` (the text to sign), and under `meta["/data/transforms"]` each profile field's `optional`, `hidden` and code list (country, state, the self-ID's gender, ethnicity, race, veteran and disability codes).

| Step | Fields | Renderings |
|---|---|---|
| Personal Information | first, middle and last name, the verified email (read only), mobile (the sign-up's), home phone, an optional text messages box, country, address lines 1 to 3, city, state, zip code, county | text inputs (`#personalInfomationFirstName`, `#PersonalAddress_city`, …) that drop keys while the step re-renders, so a typed value is checked; country a react-select combobox (`#PersonalAddress_country`); address line 1 a Google Places search that also takes a typed line; state a `wfn-dropdown-list` once the country is picked (`DC - District of Columbia`), its options `role=option`; phones `react-phone-number-input` |
| Resume | a résumé or attachments, `minItems` 1 in the meta | a file input |
| Questions | the questionnaire; desired salary, rate (Annually, Hourly), amount and currency, where the meta does not hide `requestedPayRate` | `Yes/No Buttons` an `sdf-radio-group` of `sdf-radio-button` (`Yes`, `No`); `Comment` a textarea (`#question_<n>`); salary a radio group, a text input and a list |
| Voluntary Self-ID | gender, ethnicity and race (a decline box), protected veteran status, disability | lists, a checkbox, `sdf-radio-group`s (veteran `Y`, `N`, `D`) |
| Review Your Application | the answers, read only | |
| Self-Attest & Submit | the attestation's text, "Yes, I agree to sign electronically." (`self_att_agree_chk`) | a checkbox, then Submit (`#submitApplication`), the send |

Voluntary Self-ID shows on some employers only.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `sign_up_phone` | the talent community step before the form requires a mobile number, even when its OTP goes to the email; the OTP-only sign-up asks a phone (ADP MyJobs), so an adapter takes it | `unsupported_flow` | 14 of 15 employers |
