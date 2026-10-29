# ADP

ADP Workforce Now's career center, observed on live postings on 2026-10-26 and 2026-10-29, headless, with the Test user at two employers. No adapter yet: Apply opens a sign-up of name, email and mobile number verified by an emailed OTP, the shape ADP MyJobs signs in with, then the application's wizard.

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

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `sign_up_phone` | the talent community step before the form requires a mobile number, even when its OTP goes to the email; the OTP-only sign-up asks a phone (ADP MyJobs), so an adapter takes it | `unsupported_flow` | 14 of 15 employers |
