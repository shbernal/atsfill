# ADP MyJobs

ADP's candidate experience (`myjobs.adp.com`), a different product from ADP Workforce Now's career center ([adp.md](adp.md)), observed on live postings on 2026-10-26, headless. No adapter: every sign-in, the first one included, is a one-time code, a known gap.

## Where the form is

- A posting is `myjobs.adp.com/<career site>/cx/job/<id>`, one employer per career site, a single-page app.
- Signed out, the posting page shows only the employer's header, footer and Sign in: the posting itself loads from `POST /cx/staffing/v2/job-applicant`, which redirects to ADP's sign-in (11 of 11 employers). The posting and its form were not read.

## Account

- Sign in goes to `myjobs.adp.com/<career site>/auth`: Email Address and Continue, or Use phone number, or a LinkedIn, Google, Facebook or ADP sign-in. There is no password.
- An address ADP does not know opens "Create your candidate profile": the email, Phone Number (a country dropdown and typed digits, required), First Name, Last Name, the employer privacy policy box, Continue. The profile exists from that Continue on.
- Then "Select your verification method": Text Message to the phone or Email Message, Send passcode. The email gets a six-digit "verification code from ADP", valid 15 minutes, typed on the Verify step.
- A known address goes from Continue straight to Verify, its code sent to the email (4 of 4 employers tried): the code is the sign-in, every time.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `sign_in_otp` | every sign-in, the sign-up's included, is a one-time code to the email or phone; signed out, the posting does not load | `unsupported_flow` | 11 of 11 employers |
