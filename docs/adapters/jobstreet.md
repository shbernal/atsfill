# Jobstreet

SEEK's job boards in Southeast Asia (`<country>.jobstreet.com`: `ph`, `sg`, `my`, `id`) and Hong Kong and Thailand (`hk.jobsdb.com`, `th.jobsdb.com`), observed on live postings on 2026-10-26, 2026-10-28 and 2026-10-29, headless Camoufox. The fixture pages are [tests/pages/jobstreet/job.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/jobstreet/job.html), a posting and its Quick apply wizard, and [tests/pages/jobstreet/login.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/jobstreet/login.html), SEEK's sign-in, served by `tests/test_jobstreet.py`.

## Where the form is

- A posting is `<site>/job/<id>`. Its Apply button (`[data-automation=job-detail-apply]`) links to `/job/<id>/apply` either way: "Quick apply" (`target=_self`) opens SEEK's wizard, "Apply" (`target=_blank`, `isLinkOut` in the page's data) sends the signed-in candidate to the employer's own site, `unsupported: external_step` before any sign-in. 13 of 24 postings linked out.
- Cloudflare guards every site: a plain HTTP client is answered 403, and a posting sometimes shows its "Just a moment..." check first, which passes on its own; held past a minute, it passed on a reload every time seen, and a second hold is `blocked: captcha`.
- The wizard is client-side routed: `/apply` (documents and personal details), `/apply/role-requirements` (the employer's questions, absent when there are none, as on `hk.jobsdb.com`), `/apply/profile` (the SEEK profile), `/apply/review`. A step's address loaded directly sends back to `/apply`. Continue (`[data-testid=continue-button]`) moves on, or stays with "Before you can continue with the application, please address the following issues" listing what is missing.
- The profile step re-renders as its queries come back, and a Continue clicked meanwhile does nothing: atsfill clicks again while the step neither moves nor shows an error.

## Account

- One SEEK account per email for every site, with no password: each sign-in and sign-up is an emailed OTP (ADR 0015). Each site keeps its own session (`appSession`, an Auth0 client per site), so a session on one does not sign in another: the account scope is the site (`ph.jobstreet.com`), and `login jobstreet --url <posting>` signs in on the posting's site.
- Signed out, `/job/<id>/apply` goes to `<site>/oauth/login` and SEEK's sign-in on `login.seek.com` (Auth0), which can take up to ~50 seconds to show its form: an Email address (`#emailAddress`), a Cloudflare Turnstile widget and "Email me a sign in code" (Google, Facebook and Apple sign-ins aside). Clicked before the widget passes, the page says "Please complete the recaptcha." and sends nothing; it passed on its own headless within seconds.
- The OTP step is one input labelled "verification input": six digits from `usersupport@jobstreet.com`, subject "<code> is your code for Jobstreet", which sign in on their own once typed.
- An email without an account lands on `<site>/oauth/signin-confirmation`: "Register a new account?", the privacy box (`#privacy-chkbox`, required) and the marketing one (declined), then "Register new account" through `/sign-in/onboarding/provision` back to the site. A sign-in stops there, `refused: no_account`; `login --create` registers.
- The signed-in check is the site's `/profile`, which redirects an account to `/profile/me` (307) and shows a visitor its page. Outside the browser Cloudflare can answer it 403 or with its challenge, which tells neither: `status` then reads a stored session `unknown`. The site can end a session its own way, which `/profile` does not see: the page's token refresh (`login.seek.com/oauth/token`) is answered 403 and Quick apply goes to SEEK's sign-in, seen on a session used for 90 minutes; atsfill signs in again by OTP (ADR 0015).
- Firefox cannot gather the sign-in's storage (`storage_state` aborts on Cloudflare's frame): the session is stored as its cookies, which hold it.

## Reading and filling

The page loads `GetPersonalDetails` (the profile's name, phone with its calling code, home location), `GetUser` (`profileVisibility2`) and `GetJobApplicationProcess` (`linkOut`, and `questionnaire.questions`: `id`, `text`, `__typename`, `options`) from `<site>/graphql` with the session's token; atsfill reads them from those answers.

| Step | Field | Type | Rendering | Postings |
|---|---|---|---|---|
| `/apply` | `firstName`, `lastName` | `text` | inputs, the profile's values; required | 5 walked |
| `/apply` | `currentLocation` | `typeahead` | `#currentLocation2`, suggestions while typing (`Makati City Metro Manila`); required | 5 walked |
| `/apply` | `phone` | `phone` | a calling code select (`#countryCallingCode`, `Philippines (+63)`) and `#phoneNumber`; "recommended" | 5 walked |
| `/apply` | `resume` | `file` | Upload (`#resume-fileFile`), then a radio named for the file; or "Don't include a resumé" | 5 walked |
| `/apply` | `coverLetter` | `text` | "Write a cover letter" with its box, or "Don't include a cover letter"; `one_of` `coverLetter` | 5 walked |
| `/apply` | `coverLetterFile` | `file` | Upload (`[data-testid=coverLetter-method-upload]`, a button over `#coverLetter-fileFile`), then a radio of the same test id named for the file; `one_of` `coverLetter` | 1 walked |
| `/apply/role-requirements` | `question.<id>` | `choice` | `SingleChoiceQuestion`: a native select named `questionnaire.<id>`, or radios (`Ya`, `Tidak`) whose labels take the click | selects on 9 postings, radios on 1 |
| `/apply/role-requirements` | `question.<id>` | `multi_choice` | `MultipleChoiceQuestion` | not walked |
| `/apply/review` | `profileVisibility` | `choice` | Public, Standard, Limited, Hidden radios, while the profile has none | 2 walked |

- The personal details open for editing with the profile's values, and the step does not continue until they are saved: Save writes them to the profile (`UpdatePersonalDetails`), changed or not. The read-back is what Save was given, or the profile's values when the details opened saved.
- An uploaded resume is stored in the profile at once (`ApplyProcessUploadedResume`) and made its default (`SetDefaultResume`). An uploaded cover letter is not: the next Quick apply offers only the box and "Don't include".
- The cover letter is written or uploaded, two alternatives sharing the key `coverLetter` (ADR 0005): the answers pick one, and neither leaves it out where the page allows.
- Every question was required; the page marks none.
- The profile step shows the SEEK profile (photo, career history, education, licences, skills), sent with the application. A profile without a career history stops Continue ("Career History - Please update your career history"), the known gap `profile_incomplete`; "I am new to the workforce" is the page's other way past it.
- The review step offers profile visibility, not marked required: atsfill asks it, a setting the user picks, as long as the profile has none. "Show strong interest" stays off.
- The read-back is each step's inputs as it was left, and the visibility picked on review.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `profile_incomplete` | the SEEK profile has no career history, which the profile step requires and atsfill does not fill | `unsupported_flow` | 1 account, before its first role |

## Submitting

Submit application (`[data-testid=review-submit-application]`) on `/apply/review` is the send; the evidence is the page leaving the review step. Not sent yet: where it lands is unobserved.
