# UKG

UKG Pro Recruiting's job boards and application form, observed on live postings, last on 2026-10-31. The fixture page is [tests/pages/ukg/apply.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/ukg/apply.html), served as an Acme tenant in `tests/test_ukg.py`: the form with one example of each rendering below.

## Where the form is

- A posting is `/<tenant>/JobBoard/<board>/OpportunityDetail?opportunityId=<id>` on `recruiting.ultipro.com`, `recruiting2.ultipro.com`, `recruiting.ultipro.ca` or `<employer>.rec.pro.ukg.net`. The tenant alias shows in either case (`acme1000acme`, `ACME1000ACME`); the double-send key is the detail URL with it in lowercase.
- Apply goes to `…/Account/Login?redirectUrl=…/OpportunityApply?opportunityId=<id>`, the sign-in; signed in, the form is `…/OpportunityApply?opportunityId=<id>`.

## Account

Applying needs an account, one per tenant: the account scope is the tenant alias. The same address signs up on each tenant separately.

- The sign-in is Auth0's Universal Login on `signin-<region>.ultipro.com` (`signin-us`), with the tenant as an Auth0 organization: `input#username`, `input#password`, and Sign up (`/u/signup`: `input#email`, `input#password`). No tenant sent a verification email or asked for an OTP: 6 more on 2026-10-27, each signed up and then signed in from a fresh browser, sent no mail at all. An Auth0 OTP page (`/u/mfa…`, `/u/email-verification`, `/u/login-email-verification`) stays `blocked: email_otp` until one is seen: its input is not known.
- After the sign-up the tenant asks its own details (`/AuthCode/Register`, "Almost there!"): first and last name, a phone number (a plain `tel` input, required on 10 of 16 tenants, any format taken), and on some tenants a privacy policy box, a text-message box and an AI scoring box (Candidate Match, ticked by default). Its inputs are UKG web components that take typed keys only; Create account stays disabled until the required ones are given. atsfill types the name and, where required, the phone as given (E.164), ticks the privacy box and leaves the other two unticked.
- The phone is known to be needed only once the Auth0 identity exists, so the adapter asks the phone of every sign-up (`asks`), refused before the browser opens without it.
- An identity left without its tenant details (a sign-up stopped there) lands on the same step at every sign-in. A plain sign-in stops there with `refused: sign_up_details`; `login --create` with the password already stored signs in and gives that step the `--details`.
- The signed-in check asks for the last posting's form: it loads, where a visitor or an account without its tenant details is redirected to the sign-in or the details step. Without a posting seen, it looks for the tenant host's `.AspNetCore.Cookies` session cookie, which lasts a day.
- Auth0's error codes name the refusal: `user-exists` (`email_in_use`), wrong credentials (`wrong_password`), a password rule (`password_rule`).

## Reading the form

One page, a Knockout view: Contact Information, the profile sections (Work Experience, Education, Skills, Behaviors, Motivations, Licenses and Certifications, Links: collapsed editors, never required), Documents, Availability, Questions, Submit. The page holds the posting as JSON (`opportunity`: `ApplicationQuestions`, `IsResumeRequired`) and the contact fields' configuration; the read takes the controls the page shows. A field's id is its control's DOM id (`Phone`, `Country`, `ApplicantSource`, `TextResponse3`) or a radio group's name (`employeereferral`, `MultipleChoiceResponse0`), `documents` and `availability_days`.

Each rendering, and how many of the sample's postings showed it:

| Rendering | Field type | Postings |
|---|---|---|
| text input, `textarea`: address, city, zip, middle name, availability notes, a Text or Numeric question | `text` | all |
| the account's name (`FirstName`, `FamilyName`): read-only, the account's value | `text`, `prefilled` | all |
| `type=tel` (`Phone`, `SecondaryPhone`) | `phone` | all |
| `select`: title, suffix, country, how did you hear (`ApplicantSource`) | `choice` | all |
| state (`State`): a `select` shown once a country with states is picked, filled from `…/Candidate/GetStates?countryId=<id>` | `choice`, the states per country in `option_sets` | all |
| equal-opportunity question (`Gender`, `HispanicOrigin`, `USFederalContractor`): a `select` with an "I decline to say" box under it (`[data-automation$=-decline-checkbox]`) that clears the select when ticked | `choice`, the box's label one more option | 6 of 14 |
| race (`EthnicOrigin`): the same, shown when the ethnic origin is Not Hispanic/Latino | `choice`, a follow-up of `HispanicOrigin` | where the ethnic origin is asked |
| radio group: referred by an employee, a Multiple Choice question | `choice` | all |
| the referrer's name (required), email and phone: text inputs in the page while hidden, shown when referred | `text`, follow-ups of `employeereferral` on its Yes | where the referral is asked |
| one checkbox (`WillingToRelocate`) | `choice` `Yes`/`No` | all |
| date picker (`ukg-input type=date`, month, day and year inputs): when can you start, today by default | `date` | all |
| day toggles (`[data-automation=day-btn]`) | `multi_choice` | |
| Documents upload (`form[action*=/Documents/UploadFile]`), required with `IsResumeRequired` | `file` | all |

A read-only field without a value (preferred name, former name) is listed `unsupported_rendering`; atsfill leaves it. The Upload Resume box at the top parses a resume into the profile and is not used. An uploaded document's own box (`select-document-checkbox`) is not a field.

Once filled, the same read gives the fields shown, without the follow-ups it lists while hidden.

Where a tenant makes the address optional, Contact Information is a read-only panel of the account's (name, phone, email) whose controls, Country and State among them, sit in a closed edit dialog: the read lists none of them.

## Filling

- A select is picked by its option's text; picking the country waits for its states. An equal-opportunity answer "I decline to say" ticks the box, any other unticks it and picks the option. A radio or the checkbox is clicked to the answer, the date's month, day and year typed in turn, a day toggled when it differs.
- The document is uploaded to the account's documents (`…/Documents/UploadFile`) and listed by its name once stored.

## Read-back and submit

- The read-back is the page: an input's value (a Numeric question's without the grouping autoNumeric shows, `50,000` read as `50000`), a select's option text or its ticked decline box's label, the checked radio's label, the date picker's `value` (`YYYY-MM-DD`), the selected days, the last uploaded document's name.
- Submit is `[data-automation=btn-submit]`, expected to move to `…/OpportunityApply/ApplicationSubmitted`; a tenant may turn on Cloudflare Turnstile for it (`enable-recruiting-signup-captcha-implementation`). No send was made.

## Development

The Test user's accounts are one per tenant, created by the round (ADR 0013): `atsfill login ukg --user test --url <posting> --create --email <address> --details <file>`.
