# SAP SuccessFactors

SAP SuccessFactors Recruiting's career sites and candidate pages, observed on live postings, last on 2026-10-26. The fixture pages are in [tests/pages/successfactors/](../../tests/pages/successfactors/): the posting, the sign-in and sign-up pages, and an application page with one example of each rendering below, served as an Acme career site and its data center host in `tests/test_successfactors.py`.

## Where the form is

- A posting is on the employer's career site, a domain of its own (`careers.acme.com`) or `<employer>.jobs.hr.cloud.sap`: `[/<subsite>]/job/<location>-<title>/<id>/`, the id 8 to 10 digits. The adapter claims that path on any host, after every other adapter but Phenom. The double-send key is `https://<career site>/job/<id>`.
- The page holds no company id. Its apply link, `/talentcommunity/apply/<id>/`, followed from the posting page, redirects to the candidate pages on a data center host (`career<n>.successfactors.<eu|com>`, `career<n>.sapsf.eu`), `/careers?company=<company id>`. Loaded directly, without the posting before it, the link goes to the career site's home. Some sites open a dialog on a real click, so the adapter clicks the link from script.
- Of 16 employers, the link led to a sign-in page on 11 and to a Quick Apply page on 5.

## Account

Applying needs an account, one per employer: the account scope is the career site's host. The session's cookies are on the data center host, `careerSiteCompanyId` holding the company id.

- **Sign-in page.** `#username`, `#password`, a button calling `validateFields()`, and Create an account (`a[href*="login_ns=register"]`). A wrong password leaves the page with its message, `refused: wrong_password`.
- **Sign-up.** `#fbclc_userName`, `#fbclc_emailConf`, `#fbclc_pwd`, `#fbclc_pwdConf`, `#fbclc_fName`, `#fbclc_lName`, a Country/Region of Residence `select`, optional job-alert boxes, and Terms of Use: `#dataPrivacyId` opens the data privacy statement in a dialog whose Accept (`dlgButton_…`) records the consent. Submit is `#fbclc_createAccountButton`. It asks the user's name. The country list's values are ISO 3166 codes: atsfill picks `country` from `--details`, the list's first without it (the application's own Country field answers it again).
- **Password rules**: 8 to 18 characters, upper and lower case, a number or punctuation, no space. atsfill's generated passwords are 16 characters for this reason.
- **After the sign-up**: on 4 of 7 employers the browser lands on the application, signed in; on 1 a verification email ("Activate your … Profile") comes first, `blocked: email_link`, and the link's page activates the account; on 1 the account is made and the page goes back to sign-in, where atsfill signs in; on 3 later employers it goes back to the career site (`/career`), signed in. No OTP and no captcha were seen.
- **Quick Apply page.** The account fields sit on the application itself, with one submit (`#fbqa_apply`) creating the account and sending the application together. "Please sign in" (`openSignInModal()`) opens a sign-in dialog; on the 5 employers seen it offered no Create an account: the `quick_apply_sign_up` known gap.
- Some sign-ups (6 of 18) require a phone number, `#fbclc_phoneNumber`, after a calling code `select` (`#fbclc_ituCode`, `CA`: `Canada (+1)`): atsfill picks the code the `--details` phone starts with, of `country` where several countries share it, and types the national number after it; without a phone, `refused: sign_up_details: no phone` before anything is sent. Some ask more still, such as who may see the profile (2 of 6 with a phone): the `sign_up_fields` known gap.
- **Signed-in check**: `/career?company=<company id>&career_ns=job_listing_summary&navBarLevel=MY_PROFILE` on each data center host the session's `careerSiteCompanyId` cookie names, which shows a sign-out for an account.

## Reading the form

The application (`/portalcareer`) is one page of collapsible sections opened by Expand all sections (`[id$=_expandAllSections]`): My Documents, Profile Information, the list sections (Outside Work Experience, Education History), Job-Specific Information, then Save and Apply (`[id$=_submitBtn]`). The widgets are SAP's `juic` components, whose ids are numbers given at render (`60:_txtFld`) and change as a widget re-renders. Each field is a `.RCMFormField` with a `label.rcmFormFieldLabel`, starred (`span.requiredField`) when required. A field's id is its input's `name` (`firstName`, `cellPhone`, `cust_expectedSalary`), else its label, slugged. A field holding the account profile's value is `prefilled` with it.

| Rendering | Field type | Postings |
|---|---|---|
| text input (`input[data-testid=sfTextField]`), with a `name` | `text` | 5 of 5 |
| `textarea` | `text` | 1 |
| paginated picklist: `input.rcmpaginatedselectinput` (`role=combobox`, `aria-owns` its `ul[role=listbox]`) and a button opening it, the items `li[role=option] a[title]`; a country, Yes/No questions, EEO answers | `choice`, the options read by opening it | 5 of 5 |
| cascading picklist whose items follow its parent (a state after the country), empty when read | `typeahead` | 2 |
| radio group (`input[type=radio]`) | `choice` | 1 |
| `select` | `choice` | |
| one checkbox | `choice` `Yes`/`No` | |
| attachment (`.attachmentField`): resume, cover letter, document storage | `file` | 5 of 5 |

The list sections' entries are not read: they are empty on a new account and none was required.

## Filling

- Fields are filled in the page's order, a country before its state, each looked up again first since its widget may have re-rendered.
- Text is filled and the field left with Tab. A picklist is opened with its button (clicked again when the click only closed another list) and the item whose title is the answer clicked, else the first starting with it.
- An attachment's button opens a callout of sources; its device input (`[role=dialog] input[type=file]`) uploads once set, and the field shows the file's name. A field holding a file shows a delete button instead: atsfill deletes the file, confirming the dialog (`#sfOverlayMgr button.globalPrimaryButton`), then uploads. An upload is saved to the account's profile at once.

## Read-back and submit

- Each field's value from the page: an input's value, the picklist's text, the checked radio's label, a box as `Yes`/`No`, an attachment's file name (the one uploaded to it, where it lists several).
- Submit is Apply (`[id$=_submitBtn]`), which atsfill expects to send the application; no send was made. The page holds an empty `#otpContainer`: whether a submit asks for an OTP is not known.

## Development

The Test user's accounts are one per employer, created by the round (ADR 0013): `atsfill login successfactors --user test --url <posting> --create --email <address> --details <file>`.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `quick_apply_sign_up` | a Quick Apply page, whose sign-in dialog offers no sign-up: the account and the application are sent in one submit | `unsupported_flow` | 5 of 16 |
| `sign_up_fields` | a sign-up asking more than the account's fields and the phone: who may see the profile | `unsupported_flow` | 2 of 18 |
