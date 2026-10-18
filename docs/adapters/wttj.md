# Welcome to the Jungle

Observed on live postings, last on 2026-10-18; sends were made by hand through the same form on 2026-10-04. The fixture page is [tests/pages/wttj/job.html](../../tests/pages/wttj/job.html), served as a signed-in job page in `tests/test_wttj.py`.

## Where the form is

- Jobs: `www.welcometothejungle.com/<lang>/companies/<company>/jobs/<slug>`. The double-send key drops the language: `https://www.welcometothejungle.com/companies/<company>/jobs/<slug>`.
- `easyapply.jobs/r/<id>` links (from job boards) redirect to the job, with or without a session, and open the apply form by themselves. The key comes from the job they land on.
- Some jobs apply on the company's own site: their apply button is a link, and atsfill reports them `unsupported`.
- CloudFront lets Camoufox through headless with its own user agent. (Headless Chromium is answered 403.)

## Account

Applying needs a signed-in account: `atsfill login wttj` opens `/en/signin`. The signed-in check is `GET https://api.welcometothejungle.com/api/v2/users/me`: 200 for an account, 204 for a visitor. A session saved from Chromium works in Camoufox.

The job page is rendered for a visitor first: the apply button is a link to `/en/authenticate/signin` until the page knows the account, a moment later.

## Reading and filling

The apply button (`[data-testid="job_header-button-apply"]`) opens a modal with the form holding `[name=cover_letter]`, prefilled from the profile. Field ids are the input names:

| Name | Field type |
|---|---|
| `firstname`, `lastname`, `subtitle`, `media.website`, `media.linkedin`, `media.twitter`, `cover_letter` | `text` |
| `questions.<hash>` (employer questions, textareas so far) | `text` |
| `phone` | `phone` |
| `location` (downshift combobox) | `typeahead` |
| `resume` | `file`, PDF only |
| `consent` | `choice`, `Yes`/`No` |

- The email comes from the account and is disabled; the avatar is not read.
- Fields not answered keep what the profile put there.
- The profile's CV shows first, with a Remove button: remove it, then upload the tailored PDF. "Utiliser comme CV par défaut", which would replace the profile's CV, is never ticked.
- Location suggestions load only while typing key by key, into a box cleared by select-all and Backspace: Playwright's `fill("")` (select-all and Delete) leaves the profile's value in it, and the answer lands after it. The first suggestion, "Position actuelle", geolocates and is skipped. Suggestions read `Paris, Île-de-France, France`.
- Nothing is sent before submit, the CV included, so the read-back is the form's inputs and the CV's shown name (`[data-id]`).

## Submitting

`[data-testid="apply-form-submit"]` sends `POST /api/v1/applications`, which answers 201 with an `application.reference`: that is the evidence. A login wall instead of the form means the session expired: `blocked: login_required`.
