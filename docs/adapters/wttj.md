# Welcome to the Jungle

Observed on live postings, last on 2026-10-19; the account on 2026-10-24. The fixture pages are [tests/pages/wttj/job.html](../../tests/pages/wttj/job.html), served as a signed-in job page in `tests/test_wttj.py`, and [tests/pages/wttj/account.html](../../tests/pages/wttj/account.html), the sign-in and sign-up forms.

## Where the form is

- Jobs: `www.welcometothejungle.com/<lang>/companies/<employer>/jobs/<slug>`. The double-send key drops the language: `https://www.welcometothejungle.com/companies/<employer>/jobs/<slug>`.
- `easyapply.jobs/r/<id>` links (from job boards) redirect to the job, with or without a session, and open the apply form by themselves. The key comes from the job they land on.
- Some jobs apply on the employer's own site: their apply button is a link, and atsfill reports them `unsupported`.
- CloudFront lets Camoufox through headless with its own user agent. (Headless Chromium is answered 403.)

## Account

Applying needs a signed-in account, one per user for the whole system. The signed-in check is `GET https://api.welcometothejungle.com/api/v2/users/me`: 200 for an account, 204 for a visitor. A session saved from Chromium works in Camoufox.

- Sign-in: `/en/signin` (it becomes `/en/authenticate/signin`), email and password, posted to `POST api.welcometothejungle.com/api/v1/sessions`: 201, then the page leaves for the profile's onboarding (`/en/complete-profile/…`). A wrong password is 401, "Email or password incorrect": `refused: wrong_password`.
- Sign-up (`login wttj --create --email <address> --details <file>`, its `name` the first name): `/en/authenticate/signup`, a first name (required), email, password, and a newsletter box left unticked; posted to `POST /api/v3/registrations`: 201, signed in, on the onboarding, which need not be finished to apply. A taken email is 422 with `errors.email`: `refused: email_in_use`. The page checks the password's strength as it is typed (`POST /api/v3/passwords/validate`) and refuses one under 12 characters without posting: `refused: password_rule`. The page loads an invisible reCAPTCHA v3; headless Camoufox passed it.
- The page gets its CSRF token (cookie `csrf-token`) about a second after it renders. A form posted before then is answered 403 and the page shows nothing, so both forms wait for it.
- The account is created unconfirmed and a "Confirm your new account" email follows: `login --create` ends `blocked: email_link`. Clicking its link signs every session out (`users/me` answers `{"info": "wttj-forced-signed-out"}`), so `login wttj` runs once more after it. Whether an unconfirmed account can apply is unobserved.
- Sign-ins in a burst from one address make `/en/signin` answer 429, "Too Many Requests", for a while: `blocked: rate_limited`.
- With a password source, a form command that finds the session signed out signs in again on its own.

The job page is rendered for a visitor first: the apply button is a link to `/en/authenticate/signin` until the page knows the account, a moment later.

## Reading and filling

The apply button (`[data-testid="job_header-button-apply"]`) opens a modal with the form holding `[data-testid=apply-form-submit]`, prefilled from the account profile. Field ids are the input names. Every form is the account profile's fields, the ones the employer picked, and the employer's questions; the inventory, on 15 employers, then 10 at the adapter gate:

| Name | Field type | Rendering | Postings |
|---|---|---|---|
| `firstname`, `lastname`, `subtitle` | `text` | input, required | 15 |
| `media.<site>`: `website`, `linkedin`, `twitter`, `github`, `stackoverflow` | `text` | input, required by some employers | 25 |
| `cover_letter` | `text` | textarea, required by some employers | 24 |
| `questions.<hash>` | `text` | textarea, required, one to three | 5 |
| `phone` | `phone` | text input | 15 |
| `location` | `typeahead` | downshift combobox | 15 |
| `resume` | `file` | PDF only, behind the account profile's CV | 25 |
| `portfolio` | `file` | PDF only | 1 |
| `consent` | `choice`, `Yes`/`No` | checkbox | 15 |

No question rendered as a checkbox, a radio or a select: those, and any other checkbox, are listed as `unhandled_rendering`. A page that is not a job, or an apply button that opens no form, is an `adapter_error`.

- The email comes from the account and is disabled; the avatar is not read.
- A field holding a value when the form opens is `prefilled` with it (a ticked box `Yes`, a résumé its `data-id`); one not answered keeps what the account profile put there, required or not. The account profile's location arrives up to a second after the rest, so the form is read once it has, or after 3 seconds.
- The account profile's CV shows first, with a Remove button: remove it, then upload the tailored PDF. "Utiliser comme CV par défaut", which would replace the account profile's CV, is never ticked.
- Location suggestions load only while typing key by key, into a box cleared by select-all and Backspace: Playwright's `fill("")` (select-all and Delete) leaves the account profile's value in it, and the answer lands after it. The first suggestion, "Position actuelle", geolocates and is skipped. Suggestions read `Paris, Île-de-France, France`. The first answer typed on a page sometimes loads none, "Position actuelle" alone, for good: it is typed again.
- Nothing is sent before submit, the CV included, so the read-back is the form's inputs and the CV's shown name (`[data-id]`).

## Submitting

`[data-testid="apply-form-submit"]` sends `POST /api/v1/applications`, which answers 201 with an `application.reference`: that is the evidence. A login wall instead of the form means the session expired: `blocked: login_required`.
