# Wellfound

A startup job board (formerly AngelList Talent) that takes the application itself. Observed on live postings on 2026-10-30, headless Camoufox, signed in as the Test user: 15 postings opened, 13 dialogs read. The fixture pages are [tests/pages/wellfound/job.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/wellfound/job.html), a posting whose Apply renders the dialog from its GraphQL read, [login.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/wellfound/login.html), [signup.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/wellfound/signup.html) and [profile.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/wellfound/profile.html), served by `tests/test_wellfound.py`.

## Where the form is

- Postings come as `wellfound.com/jobs/<id>-<slug>`, or `wellfound.com/company/<employer>/jobs/<id>-<slug>`; `https://wellfound.com/jobs/<id>` keys the posting. The page is server-rendered behind Cloudflare, which let plain HTTP clients and headless Camoufox through.
- Every posting read takes the application on Wellfound (`directApply`). A closed posting still answers 200, saying "The job listing is no longer available" or "… was recently taken down and is no longer available for applications", with no Apply: `posting_gone`. Others answer 404 or 410.
- The page's Apply buttons (several, one per layout) all open the same dialog.

### Signed out

Apply opens "Your Application": name, email, a password and its confirmation, location, years of experience, desired salary, the US work authorization questions, the employer's questions and a resume. One submit creates the account and sends the application, so atsfill never uses it: a session that ended shows it, `login_required`, and the core signs in again.

## Account

One account per user for the whole system, a candidate account.

- Sign-in (`login wellfound`): `wellfound.com/login`, email (`#user_email`) and password (`#user_password`), Log in, a form posted to `/login`. An account is redirected to `/jobs`; a wrong password shows the form again with "Email or password is incorrect": `refused: wrong_password`. No OTP was asked. "Continue with Google" is not used.
- Sign-up (`login wellfound --create --email <address> --details <file>`, its `name` the full name): `wellfound.com/jobs/signup`, the job seeker sign-up (`/join` offers it as "I'm looking for a job"). Name, email and a password of 8 to 128 characters, checked on the page; Continue posts `/jobs/signup/email-verification-code`, which emails a six-digit OTP ("<code> - Your Wellfound email verification code"). A taken email is 422 with `field_errors.email` "Email has already been taken": `refused: email_in_use`. About four codes asked within ten minutes earn a 429: `blocked: rate_limited`. The code is checked as soon as six digits are typed (`…/verify`, 422 for a wrong one: `otp_rejected`), which enables Create account; it posts `/jobs/create-user` and lands on the onboarding, signed in.
- The signed-in check is `GET wellfound.com/jobs/onboarding`, not followed: a visitor is redirected to `/login`, an account to an onboarding step. A response Cloudflare marks `cf-mitigated` tells neither.

## Account profile

A new account lands on an onboarding at `/jobs/onboarding/<step>`: Profile (`extended_profile`), Preferences, Culture and Resume/CV, each saved through its own GraphQL mutation (`SubmitExtendedProfile`, `SubmitPreferences`, `SubmitCultureQuestions`). atsfill does not walk it: every field that lets Apply open is also on the tabs of `/profile/edit`, which load for any account. With only those tabs saved, Apply opened the dialog while the onboarding stayed unfinished and `candidateOnboarding.requiredSteps` kept listing all four steps.

`atsfill profile wellfound` (ADR 0017) reads and saves two profile steps. The fixture page is [tests/pages/wellfound/profile.html](https://github.com/shbernal/atsfill/blob/main/tests/pages/wellfound/profile.html).

- `about`: `/profile/edit`, the Profile tab.
- `preferences`: `/profile/edit/preferences`, the Preferences tab.

Most controls save as they change; the bio, the social links and a work experience save on their own form's Save, shown once a field changes. So a fill saves, field by field, and the core's read-back comes after: the page is reloaded and the values read from its Apollo state (`__NEXT_DATA__`, `props.pageProps.apolloState.data`, the `Candidate:<id>` object and what it refers to). `save` has nothing left to do. A mismatch stops the run with what was filled saved, and an answer that already matches the saved value is not touched.

- Most mutations answer the `Candidate`, many with `missingRequiredFields` (`LOOKING_FOR`, `PRIMARY_ROLE`, `PRIMARY_LOCATION`, `REQUIRE_SPONSORSHIP`, `US_AUTHORIZED`, `CANDIDATE_STATE`).
- `SaveCandidateState` answers 200 and keeps nothing while another required field is missing, and a state of `null` is shown as "Your job profile is visible to startups". The status is filled last, after the `about` step, and read back.
- The job search status is "Ready to interview", "Open to offers" or "Closed to offers", the last hiding the profile from startups (`invisible`); an account closed to offers can still apply. Which one is the user's answer, never atsfill's. The dialog notes "Your job status will change": a send is expected to change it.
- A profile save the page refuses itself (a link that is no URL) posts nothing and names no error: `refused: step_refused: <field>: the page saved nothing`. GraphQL `errors` are `refused: step_refused: <field>: <errors>`.
- A complete profile can get a dialog over the page, "We want to feature you to recruiters!": Start my application enrols the account, "Skip for now — I'm not ready yet" posts `DismissFeatured` and it stays gone. It covers the controls, and it opens whatever `OptInModalAutoLauncher` answers. `profile` declines it where it shows, a declared side effect; it was seen on 1 of 4 Test accounts.
- A company or a skill is searched and taken only under its exact name (a suggestion's other line is its site or followers), `answer_not_taken` otherwise. The search also lists near names, and showed no way to add an unknown company. It finds nothing for "React.js" and lists it for "React", so a whole name is searched first, then its first word.
- Editing a saved work experience cannot change its company: `refused: step_refused: about: work_company`.

| Field ids | Field type | Rendering | Mutation |
|---|---|---|---|
| `location` | `typeahead`, `City, Country` | downshift search; a saved one shows with a × that brings it back | `ProfileSavePrimaryLocation` |
| `primary_role` | `choice` | react-select `primaryRole`, options under group headings | `ProfileSaveRoles` |
| `years_experience` | `choice`, position in the list | react-select `yearsExperienceInPrimaryRole` | `ProfileSaveRoles` |
| `skills` | `multi_choice`, no options | downshift search, a chip with a × per skill, each change saved | `ProfileSaveSkills` |
| `bio` | `text`, 160 characters | `#form-input--bio`, the form's Save | `ProfileSaveBio` |
| `website`, `linkedin`, `github`, `twitter` | `url` | `#form-input--<onlineBioUrl\|linkedinUrl\|githubUrl\|twitterUrl>`, one form's Save | `ProfileSaveSocialProfiles` |
| `work_company`, `work_title`, `work_from`, `work_to` (`MM/YYYY`), `work_current`, `work_description` | `text`, `choice` Yes/No for `work_current` | the first entry: the form, open on an empty profile, or its Edit; `work_to` is not used while `work_current` is Yes | `ProfileSaveExperience` |
| `job_type` | `choice` | react-select `primaryLookingFor`, the first of the system's job types | `ProfileSaveLookingFor` |
| `us_authorized`, `require_sponsorship` | `choice` | radios `form-input--<name>--<true\|false>` | `ProfileSaveUsAuthorization`, `ProfileSaveVisaSponsorship` |
| `remote` | `choice`, Yes/No | checkbox `form-input--distributedTeamOk--true` | `ProfileSaveInterestedLocations` |
| `job_status` | `choice` | `[data-test=CandidateStateDropdown]`, the page's own being the last of two | `SaveCandidateState` |

A react-select is searched by typing and taken with Enter: its input has no box to click once it holds a value. Left as they are: the name, the photo, other job types, the salary, the locations, education, achievements, the identity questions, and the Culture and Resume tabs.

Apply on a profile that lacks these goes to `/profile/edit?job_listing_id=<id>`, and a job that requires skills or work experience the profile lacks lists them and disables Send: both end `blocked: profile_incomplete`, naming the `profile` command.

## Reading and filling

Apply posts the GraphQL read `JobApplicationModal` (`{jobListingId}`), whose `jobListing` the dialog renders, and records a `CreateInteraction` of kind `APPLY_ATTEMPT` on the job: a dry run is visible to the system as an attempt. atsfill reads the fields from that answer:

- `currentUserApplied`: the account applied, `already_sent`.
- `offsiteListingUrl`: Apply opens another site, `external_step` (none seen).
- `currentUserQualificationReport.errors` disable the dialog and Send: `MISSING_SKILLS` and `MISSING_WORK_EXPERIENCE` are `blocked: profile_incomplete`; the others (`LOCATION_MISMATCH`, `LOCATION_SPECIFICITY`, `NEEDS_TO_RELOCATE`, `NEEDS_WORK_AUTHORIZATION`) are the employer's rules, `not_qualified` (3 of 13). `warnings` (`INCORRECT_ROLE`, `NEEDS_MORE_EXPERIENCE`) only show "Improve your odds".
- `aiAssessmentRequired`: an AI interview follows the send, the known gap `ai_assessment` (none seen).
- `applicationQuestionSets`: question sets rendered after the questions, the known gap `question_sets` (none seen).
- `questions`, each with `id`, `kind`, `question`, `required` and `options` (`id`, `option`); `managedByAts` marks the questions of a posting Wellfound imports from another system (`source: ATS`, 2 of 13), which asked contact links and yes/no questions, most required.

Field ids: `question_<id>`, and `note` for the note. The dialog is the form holding `[data-test="JobApplicationModal--SubmitButton"]`; each question is a labelled block titled by its question, a required one's title ending in `*`. Renderings, from the page's question component and seen live:

| Kind | Field type | Rendering | Postings (of 13) |
|---|---|---|---|
| `LONG` | `text` | textarea `customQuestionAnswers[<id>][answer]`, 4 rows | 12 |
| `SHORT` | `text` | text input `customQuestionAnswers[<id>][answer]` | 2 |
| `NUMBER` | `text`, a number | number input, same name | 0 |
| `DATE` | `date` | date input, same name, `YYYY-MM-DD` | 0 |
| `SINGLE` | `choice` | radios `customQuestionAnswers[<id>][jobListingQuestionOptionId]`, value the option id, each moved out of view behind its label | 2 |
| `MULTIPLE` | `multi_choice` | checkboxes, same name | 0 |

- Platform postings asked one optional question, "What interests you about working for this company?" (10 of 13).
- Without questions, the dialog shows a note in their place, `textarea[name=userNote]` ("Cover Letter", placeholder "Write a note to <employer>."), required when `candidateNoteRequired`; it is hidden for an imported posting that does not require it. The page keeps the note's draft in the browser's local storage, which a fresh context does not have.
- Nothing is sent while the dialog is filled. The read-back is its inputs.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `ai_assessment` | the job asks for an AI interview after the send | `unsupported_flow` | 0 of 13 |
| `question_sets` | the dialog shows question sets after the questions | `unsupported_rendering` | 0 of 13 |

## Submitting

Send application posts the GraphQL mutation `CreateJobApplication` (`{input}`), answered `jobListing {id currentUserApplied}`: `currentUserApplied` on the job is the evidence; without it, `unknown`. GraphQL `errors` are `blocked: answer_refused`. The page sends every GraphQL request through a wrapper that, on a response Cloudflare marks `cf-mitigated: challenge`, shows Turnstile ("One more step before you proceed...") and reloads the page once solved: `blocked: captcha`.

No send has been observed.
