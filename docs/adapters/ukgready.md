# UKG Ready

UKG Ready's career pages (formerly Kronos Workforce Ready), observed on live postings, last on 2026-10-26. It has no adapter: an account atsfill could sign in to again needs a zip code at sign-up, which `login` does not take.

## Where the form is

- A posting is `/ta/<tenant>.careers?ShowJob=<id>` on `secure<n>.saashr.com`, `secure<n>.entertimeonline.com`, `prd01-hcm01.prd.mykronos.com` or `secure.workforceready.eu`. The tenant is a company alias (`6123456`, `acme1000`); the page is an Angular view over `/ta/rest/ui/recruitment/companies/%7C<tenant>/job-requisitions/<id>`.
- Apply opens the first step in place: Use my resume or Type it in myself (13 of 14 tenants), then first name, last name and email, required; one tenant asked a phone number too and offered no choice.
- Its Continue posts `/ta/rest/system/applicant-portal/companies/|<tenant>/applicant` with the name, the email and the posting: that creates the account in the tenant and a job application for the posting (`…/job-requisitions/<id>/job-application`, 201), then opens the form signed in.

## Account

Applying needs an account, one per tenant. The same email on the first step again answers "We noticed you already have an account using this email address. Please log in to continue."

- The account the first step creates has no password. The tenant's sign-in (`/ta/<tenant>.careers?LanguageOverride=EN`: Username, Password) says to use Forgot your password? to create one: a reset by email, whose page takes the new password. Its account security screen offers only two-factor authentication and passkeys.
- The sign-in's Create a new account sets a password: email and its confirmation, password and its confirmation, first and last name, country, and a zip code. The zip code is required on every tenant tried (14 of 14): "The postal or zip code cannot be empty."
- No mail arrived after either sign-up.

## Reading the form

Seen once, under a first-step account, not inventoried: a wizard of 6 to 8 items (Contact Information, Resume & Cover Letter, Work Experience, Education, EEO Information, Professional References, the questions), each saved by its Continue, which refuses an item missing a required field (the cell phone and street address on Contact Information). Each item's fields come from `/ta/rest/ui/recruitment/applicants/<applicant>/profile-tabs/<tab>/config`, which takes the page's Authentication header, not the cookies alone. The first item also offers a security question and a passkey.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `sign_up_zip` | the sign-up that sets a password requires a zip code, which `login` does not take; the first step's account has no password, so it cannot be signed in to again | `unsupported_flow` | 14 of 14 tenants |
