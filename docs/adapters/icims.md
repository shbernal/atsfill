# iCIMS

iCIMS career portals, observed on live postings on 2026-10-26, headless. No adapter: the step before the form puts up an hCaptcha image challenge, recorded as the system's block in the registry.

## Where the form is

- A posting is `<portal>.icims.com/jobs/<id>/<slug>/job`, one portal per employer (`careers-<employer>`, sometimes another prefix). The page wraps the portal in an iframe, `icims_content_iframe`, at the same URL with `in_iframe=1`.
- Apply (`iCIMS_ApplyOnlineButton`) links to `…/job?mode=apply&apply=yes&in_iframe=1&hashed=<n>`, which redirects to `…/<slug>/login`. Some postings carry no Apply button and link to the employer's own site instead.

## Account

Applying needs an account, one per portal: the form is behind the sign-in.

- The login step, "Enter Your Information", asks an email (`#email`, `css_loginName`), a phone country code (a searchable dropdown) and a phone number, then Next (`#enterEmailSubmitButton`). It loads hCaptcha (`api.hcaptcha.com`, invisible), run when Next is pressed: Next stays disabled with a loading overlay until it passes.
- Headless, Next brought up an image challenge on 4 portals, and stayed disabled on 2 more. Nothing past this step was seen: whether a new email leads to a sign-up with a password, a verification email or an OTP is unknown.

## Block

The hCaptcha image challenge on the login step's Next stops every portal before the form, so no form was read. ADR 0013 adds no evasion beyond Camoufox: the system waits in the backlog's blocked tail.
