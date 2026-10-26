# Jobstreet

SEEK's job boards in Southeast Asia (`<country>.jobstreet.com`) and Hong Kong (`hk.jobsdb.com`), observed on live postings on 2026-10-26, headless. No adapter: applying needs a SEEK account whose every sign-in is a one-time code, a known gap.

## Where the form is

- A posting is `<country>.jobstreet.com/job/<id>` (`ph`, `sg`, `my`, `id`) or `hk.jobsdb.com/job/<id>`.
- The posting page has Apply or Quick apply (`[data-automation=job-detail-apply]`), a link to `/job/<id>/apply`. Signed out, it goes to SEEK's sign-in at `login.seek.com`, headed "Applying for" the posting (14 of 15 employers).
- One posting page showed a Cloudflare "Just a moment..." interstitial on its first load; the others did not.
- The form behind the sign-in was not read.

## Account

- The sign-in asks an Email address (`#emailAddress_seekanz`) and "Email me a sign in code", or a Google, Facebook or Apple sign-in. There is no password.
- Register (`#newEmailAddress_seekanz`) is the same step for a new address: "Email me a sign in code".
- Both open "Check your email for a code": a six-digit code typed into one input, then Sign in (`#submit-OTP`). The code is the sign-in, every time.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `sign_in_otp` | every sign-in and sign-up to the SEEK account Apply needs is a six-digit code emailed to the address, with no password | `unsupported_flow` | 14 of 15 employers |
