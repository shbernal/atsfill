# ADP

ADP Workforce Now's career center, observed on live postings on 2026-10-26, headless. No adapter: the sign-up before the form verifies the email or the mobile number with a one-time code, a known gap.

## Where the form is

- A posting is `workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=<employer>&ccId=<n>&jobId=<id>&lang=<locale>`, one employer per `cid`, a single-page app at that one URL.
- The posting page has Apply (`#recruitment_jobDescription_candidateApply`). It opens, at the same URL, "Join Our Talent Community": the step every application starts with.

## Account

Applying needs a candidate profile with the employer, made on the step below.

- The step asks First Name (`#guestFirstName`), Last Name (`#guestLastName`), Email (`#guestEmail`) and Mobile Number (`#login_view_phone`, with a country code dropdown, required), then Continue. It warns that the email or mobile number identifies the person: a later use by someone else overrides the profile's name and history. There is no password. The alternative is a Google, Facebook or LinkedIn sign-in.
- Continue opens "Get your verification code" (`.otpDialogContent`): send the code to the mobile number or the email (`#phoneIdentifierId`, `#emailIdentifierId`), then Send (`#recruitment_login_guestLogin`). The email gets a six-digit code, typed into `#oneTimePassWord` and Verify (`#recruitment_login_verifyOTP`).
- 14 of 15 employers showed the code dialog after Continue; on the 15th the step had no mobile number field and was not taken further.
- The form behind the code was not read.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `sign_up_otp` | the talent community step sends a one-time code to the email or mobile number before the form | `unsupported_flow` | 14 of 15 employers |
