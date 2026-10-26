# isolved Hire

isolved Applicant Tracking's career sites, observed on live postings on 2026-10-26, headless. No adapter: the posting page's Apply verifies the email or phone with a one-time code, and the sign-up that avoids it asks a phone number and a postal address, which `login` does not take.

## Where the form is

- A posting is `https://<employer>.isolvedhire.com/jobs/<id>`, one subdomain per employer.
- The posting page holds the apply step in place, under Apply now: "How would you like to verify your identity?", Continue with phone (a text code) or Continue with email (`.identity-choice__option`). Email asks the address ("We'll look up your account or create a new one"), then "Almost There!" with an optional text message consent, then Send verification code: the email gets a six-digit code ("Your Verification Code"), typed in before Verify. 15 of 15 employers showed it.
- The application is a wizard at `/apply/<id>-0.php`: Instructions, contact information, Job Questions, Resume, Voluntary Disclosure, Source, Submit Application. Signed out it redirects to `/account/login.php?listing_id=<id>&i=0`. Seen once, not inventoried.

## Account

An account is system-wide: made on one employer's site, it signs in on another's, which first asks to agree to the privacy policy again.

- The sign-in (`/account/login.php`: `username`, `password`) offers "Need an account? Click here!", the sign-up at `/account/`: first and last name, email and its confirmation, phone number and its type, street address, country, state, city, zip code, password and its confirmation, text message consent (`applicant_consent_approval`), preferred contact method (`text_approval`), and the privacy policy checkbox (`info_use_consent`). The same 18 fields on 15 of 15 employers.
- Its submit signs in at once, with no code or link; a mail with the account's login link follows. The wizard then opens signed in.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `sign_up_otp` | the posting page's Apply sends a one-time code to the email or phone before the form | `unsupported_flow` | 15 of 15 employers |
| `sign_up_fields` | the sign-up with a password, which needs no code, asks a phone number and a postal address, which `login` does not take | `unsupported_flow` | 15 of 15 employers |
