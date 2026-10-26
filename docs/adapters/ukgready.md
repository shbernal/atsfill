# UKG Ready

UKG Ready's career pages (formerly Kronos Workforce Ready), observed on live postings, last on 2026-10-26. It has no adapter: the form is a profile wizard of item editors (see Known gaps), walked signed in on one tenant only.

## Where the form is

- A posting is `/ta/<tenant>.careers?ShowJob=<id>` on `secure<n>.saashr.com`, `secure<n>.entertimeonline.com`, `secure<n>.yourpayrollhr.com`, `prd01-hcm01.prd.mykronos.com`, `secure.workforceready.eu` or `secure.workforceready.com.au`. The tenant is a company alias (`6123456`, `acme1000`); the page is an Angular view over `/ta/rest/ui/recruitment/companies/%7C<tenant>/job-requisitions/<id>`, public.
- Apply opens the first step in place: Use my resume or Type it in myself (13 of 14 tenants), then first name, last name and email, required; one tenant asked a phone number too and offered no choice.
- Its Continue posts `/ta/rest/system/applicant-portal/companies/|<tenant>/applicant` with the name, the email and the posting: that creates the account in the tenant and a job application for the posting (`…/job-requisitions/<id>/job-application`, 201), then opens the form signed in.
- The posting page ignores a session: signed in, it still shows Log in and the first step. Signed in, applying goes through the applicant portal instead (below).

## Account

Applying needs an account, one per tenant: the account scope would be the tenant alias. The same email on the first step again answers "We noticed you already have an account using this email address. Please log in to continue."

- The account the first step creates has no password; the sign-in says to use Forgot your password? to create one, a reset by email.
- The sign-in is `/ta/<tenant>.careers?LanguageOverride=EN` (the posting's Log in link, opened in a new tab): Username (`input[name=Username]`), Password (`input[name=PasswordView]`), Sign in. It lands on the applicant portal, `…careers?rnd=…&Ext=careers&sft=<token>&ActiveSessionId=<n>#home`. No code was asked.
- The sign-in's Create a new account (`…?rnd=…`, a server-rendered form) sets a password: `Username` and `ReenterUsername` (the email), `NewPassword` and `ReenterNewPassword` (8 to 64 characters, 3 of 4 character classes, no username in it), `FirstName`, `LastName`, `Country` (a `select` by name, ISO 3166 alpha-3 values) and `ZipCode`, required on every tenant tried (14 of 14). Create account (`button[name=CreateANewAccount]`) lands signed in on the portal. No mail arrived.
- The session is the `JSESSIONID` and `XSRF-TOKEN` cookies, kept across browsers. The REST API answers it with the token as an `x-xsrf-token` header (`/ta/rest/ui/bootstrap/me`: 200 and the account, else 401 "Missed Authentication header"). The portal opens again at `…careers?Ext=careers&sft=<XSRF-TOKEN>#home`; `…careers?Ext=careers` without the token shows the sign-in and ends the session.

## Reading the form

Walked once, under a Test user account made by Create a new account, not inventoried.

- In the portal, Find Jobs (`#applicant/findjobs`) lists the tenant's postings by their requisition id, the `ShowJob` id. Apply there posts `/ta/rest/ui/recruitment/applicants/job-requisitions/<id>/job-application` (201, `{"id": <application>}`): the job application exists from then on, unsubmitted. The wizard is `#applicant/profile`, titled Apply for Job, with Back, Continue and a progress count.
- `/ta/rest/ui/recruitment/applicants/<account>/profile-tabs` lists the wizard's items (`profile_tabs`: id, name, `tab_type`, `completed`) with the current posting and application (`job_req_id`, `job_application_id`). Each item's fields are `…/profile-tabs/<tab>/config`: groups of items by type, each with `field_configs` (name, `required`, `editable`) or `custom_field_configs` (label, `type`, `required`, `max_length`, an `index`). Its values are `…/profile-tabs/<tab>?job_app_id=<application>`, one entry per item.
- The items seen: Contact Information (`CONTACT_INFO`: name, emails, cell and home phone; `ADDRESS`; `CUSTOM_FIELDS`; `SECURITY_QUESTION`, a question and an answer, required), Resume & Cover Letter (`RESUME_ADDITIONAL_INFO`: work eligibility, referral source; `COVER_LETTER_UPLOAD_DOCUMENT`; `RESUME`; custom fields), Work Experience and Education (lists of entries; the highest education required), Professional References, Job Application Summary (`MY_JOB_APP_SUMMARY`, every item again read-only, and `SIGN_OFF`, an electronic signature, required), the last item, where the application is finished.
- On the page, the contact details and the address are cards opened by an edit button into an editor; custom fields and the security question are inline inputs. Element ids are generated on each render (`m1rklctcrVzFHIK5u1Hm`), so a field is known by its item type and name or its custom field index, from the config.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `profile_wizard` | the form is the portal's profile wizard: items of a dozen types, cards edited in editors, lists of entries, a security question and an electronic signature; atsfill reads and fills none of it yet | `unsupported_flow` | 1 of 1 tenants walked signed in |
