# Welcome to the Jungle

Observed on live postings, last on 2026-10-19. The fixture page is [tests/pages/wttj/job.html](../../tests/pages/wttj/job.html), served as a signed-in job page in `tests/test_wttj.py`.

## Where the form is

- Jobs: `www.welcometothejungle.com/<lang>/companies/<company>/jobs/<slug>`. The double-send key drops the language: `https://www.welcometothejungle.com/companies/<company>/jobs/<slug>`.
- `easyapply.jobs/r/<id>` links (from job boards) redirect to the job, with or without a session, and open the apply form by themselves. The key comes from the job they land on.
- Some jobs apply on the company's own site: their apply button is a link, and atsfill reports them `unsupported`.
- CloudFront lets Camoufox through headless with its own user agent. (Headless Chromium is answered 403.)

## Account

Applying needs a signed-in account: `atsfill login wttj` opens `/en/signin`. The signed-in check is `GET https://api.welcometothejungle.com/api/v2/users/me`: 200 for an account, 204 for a visitor. A session saved from Chromium works in Camoufox.

The job page is rendered for a visitor first: the apply button is a link to `/en/authenticate/signin` until the page knows the account, a moment later.

## Reading and filling

The apply button (`[data-testid="job_header-button-apply"]`) opens a modal with the form holding `[data-testid=apply-form-submit]`, prefilled from the profile. Field ids are the input names. Every form is the profile's fields, the ones the employer picked, and the employer's questions; the inventory, on 15 companies, then 10 at the gate:

| Name | Field type | Rendering | Postings |
|---|---|---|---|
| `firstname`, `lastname`, `subtitle` | `text` | input, required | 15 |
| `media.<site>`: `website`, `linkedin`, `twitter`, `github`, `stackoverflow` | `text` | input, required by some employers | 25 |
| `cover_letter` | `text` | textarea, required by some employers | 24 |
| `questions.<hash>` | `text` | textarea, required, one to three | 5 |
| `phone` | `phone` | text input | 15 |
| `location` | `typeahead` | downshift combobox | 15 |
| `resume` | `file` | PDF only, behind the profile's CV | 25 |
| `portfolio` | `file` | PDF only | 1 |
| `consent` | `choice`, `Yes`/`No` | checkbox | 15 |

No question rendered as a checkbox, radio or select: those, and any other checkbox, read as unsupported when required, and are left out otherwise.

- The email comes from the account and is disabled; the avatar is not read.
- A field holding a value when the form opens is `prefilled`; one not answered keeps what the profile put there, required or not.
- The profile's CV shows first, with a Remove button: remove it, then upload the tailored PDF. "Utiliser comme CV par défaut", which would replace the profile's CV, is never ticked.
- Location suggestions load only while typing key by key, into a box cleared by select-all and Backspace: Playwright's `fill("")` (select-all and Delete) leaves the profile's value in it, and the answer lands after it. The first suggestion, "Position actuelle", geolocates and is skipped. Suggestions read `Paris, Île-de-France, France`. The first answer typed on a page sometimes loads none, "Position actuelle" alone, for good: it is typed again.
- Nothing is sent before submit, the CV included, so the read-back is the form's inputs and the CV's shown name (`[data-id]`).

## Submitting

`[data-testid="apply-form-submit"]` sends `POST /api/v1/applications`, which answers 201 with an `application.reference`: that is the evidence. A login wall instead of the form means the session expired: `blocked: login_required`.
