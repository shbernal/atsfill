# MyCareersFuture

Singapore's government job board, which takes the application itself, observed on live postings, last on 2026-10-26. It has no adapter: applying needs a national identity no Test user can hold.

## Where the form is

- A posting is `www.mycareersfuture.gov.sg/job/<id>`, redirected to `/job/<category>/<slug>-<id>`, `<id>` 32 hex characters.
- The posting is public. Its only apply control is Log in to Apply, a button, which goes to `login.id.singpass.gov.sg/main` ("You are logging in to MyCareersFuture Portal") on every employer tried (7 of 7).

## Account

- The jobseeker sign-in is Singpass, Singapore's national digital identity: the Singpass app's QR code, or Use password. There is no email account and no sign-up on the board.
- A Singpass account is registered at `portal.singpass.gov.sg/home/ui/register/instructions` with the full name as on an NRIC or FIN card, the NRIC or FIN number, its date of issue and a security check. The Test user has none, so no account is made and the form behind the sign-in was not seen.

## Known gaps

| Gap | What | Code | Postings |
|---|---|---|---|
| `national_id_login` | applying needs a Singpass sign-in, whose account takes a Singapore NRIC or FIN number | `unsupported_flow` | 7 of 7 employers |
