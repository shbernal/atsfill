# SAP SuccessFactors

SAP SuccessFactors Recruiting's career sites and its candidate pages, observed on live postings, last on 2026-10-26. There is no adapter yet: this is what one round saw on 16 employers, and what an adapter has to handle.

## Where the form is

- A posting is on the employer's career site, a domain of its own (`careers.acme.com`, `jobs.acme.com`) or `<employer>.jobs.hr.cloud.sap`: `[/<subsite>]/job/<location>-<title>/<id>/`, the id 8 to 10 digits. The page holds no company id and no link to the candidate pages but its apply link.
- The apply link is `/talentcommunity/apply/<id>/?locale=<locale>`. Followed from the posting page it redirects to the candidate pages on a SuccessFactors data center host, `career<n>.successfactors.<eu|com>` or `career<n>.sapsf.eu`, `/careers?company=<company id>&…`. Loaded directly, without the posting page before it, it redirects to the career site's home. On some sites the link opens a dialog first (`dialogApplyBtn`); a click from script goes through.
- Of 16 employers, the apply link led to a sign-in page on 11, and to a Quick Apply page on 5.

## Account

Applying needs an account, one per employer: the company id on the data center host. A session's cookies are on that host, `careerSiteCompanyId` among them, holding the company id.

- **Sign-in page.** `#username`, `#password`, a button calling `validateFields()`, and "Create an account" (`a[href*="login_ns=register"]`).
- **Create an account.** `#fbclc_userName`, `#fbclc_emailConf`, `#fbclc_pwd`, `#fbclc_pwdConf`, `#fbclc_fName`, `#fbclc_lName`, a `select#fbclc_country` (Country/Region of Residence), optional job-alert boxes (`#fbclc_emailEnabled`, `#fbclc_campaignEmailEnabled`), and Terms of Use: `#dataPrivacyId` opens the data privacy statement in a dialog, whose Accept button records the consent. The submit is `#fbclc_createAccountButton`. It asks the user's name.
- **Password rules** (shown under the field): 8 to 18 characters, an upper and a lower case letter, a number or punctuation, no space or Unicode. atsfill's generated passwords are 16 characters for this reason.
- On the one employer tried, the account was signed in at once, with no verification email and no OTP, and the browser landed on the application (`/portalcareer?_s.crb=…`).
- **Quick Apply page.** The same account fields (`fbclc_*`) sit on the application page itself, under the resume and the employer's fields, with one submit (`#fbqa_apply`) that creates the account and sends the application together. "Already a registered user? Please sign in" leads to the sign-in page. An account made first through the sign-in page's Create an account is the way to a dry run there.
- The signed-in check: `/career?company=<company id>&career_ns=job_listing_summary&navBarLevel=MY_PROFILE` on the data center host shows Sign Out for a signed-in session; the page holds the sign-in form's markup either way.
- No captcha was seen on the sign-in, the sign-up or the application.

## Reading the form

The application page (`/portalcareer`) is one page of collapsible sections, all open after "Expand all sections": My Documents, Profile Information, Outside Work Experience, Education History, Job-Specific Information (Application Details), then Save and Apply (`[id$=_submitBtn]`). The widgets are SAP's `juic` components, their ids numbers given at render (`60:_txtFld`), different on each load. Each field is a `.RCMFormField` with a `label.rcmFormFieldLabel` (`for` the control), a `span.requiredField` when required.

| Rendering | Field type | Seen on |
|---|---|---|
| text input (`input[data-testid=sfTextField]`), with a stable `name` (`firstName`, `lastName`, `cellPhone`, `contactEmail`) | `text` | every page |
| `textarea` (salary expectations, availability) | `text` | 1 of 2 pages read |
| paginated picklist: an `input.rcmpaginatedselectinput` (`role=combobox`, `aria-owns` its list) and a button opening it; a country, a county, the employer's Yes/No questions, EEO answers | `choice` | every page |
| cascading picklist (`sfCascadingPicklist`): a country, then its state or county | `choice` | 2 of 2 |
| radio group (`fd-radio`), gender | `choice` | 1 of 2 |
| attachment (`.attachmentField`): resume (required), cover letter, opening an upload dialog | `file` | every page |
| a list of entries (Outside Work Experience, Education History), rows added and removed | not read yet | 1 of 2 |

The page holds an empty `#otpContainer`: whether a submit asks for an OTP is not known.

## Development

The Test user's accounts are one per employer, created by the round (ADR 0013). A juic widget re-renders on a click, so a locator held across a click goes stale, and some clicks navigate.

## Known gaps

The adapter is not written: the account flow, the field inventory above and the fixture page are the next round's starting point.
