# 0004. The browser

## Decision

- Each command launches its own Camoufox browser (ADR 0002), at the pinned version that `atsfill install-browser` downloads. It runs headless, so nothing appears on the user's screen and nothing depends on the desktop: a real Ashby `send` in plain headless on Linux ended `FormSubmitSuccess` on 2026-10-18, past the reCAPTCHA Enterprise check that refuses stock Playwright. `atsfill login` (ADR 0007) is the one visible window.
- One device identity per data directory: generated on the first launch for the machine's real operating system, saved, and reused by every later launch. Locale and timezone are the machine's.
- The browser is never signed in to LinkedIn, Google or any account. "Apply with LinkedIn" and Google autofill buttons, or a page that tries to send the agent somewhere else, meet a login wall.
- No persistent browser profile: every command gets a fresh browser context, so autofill, saved drafts and another system's cookies never reach a form. What persists is the device identity and the sessions a system needs, loaded into that context only (ADR 0007).
- The window is at least 900px tall: Greenhouse's and Teamtailor's submit buttons misbehave in a shorter one.
- One command uses the data directory at a time; another waits on its lock, so two sends of one posting cannot both pass the double-send guard (ADR 0006). Each command has a time budget of about three minutes, always returns a status, and closes the browser when it exits.
- Every launch loads uBlock Origin with its annoyance lists on (cookie banners, pop-ups, widgets), read from disk: `install-browser` fetches them, since fetching them at launch held the first page for up to 30 seconds. Each launch compiles the lists on, so only the annoyance lists are: ads and trackers are not atsfill's concern. Until they compile, about two seconds, a page keeps its banners, so a launch returns once a probe page's banner is hidden. A banner the lists hide needs no code in the adapter. For one they miss, the adapter gives uBlock Origin cosmetic filters (`filters`); the blocker carries them as one more list on, rewritten by each launch, so the adapter knows its page and the core does the hiding.
- Before a click, atsfill checks with `elementFromPoint` that the target is on top, closing what covers it when the adapter knows how. Evidence of a send is a URL or a network response, never page text, so page language does not matter.

## Rejected

- A headed browser on a private virtual display (`headless="virtual"`), which Camoufox's documentation recommends because headless may be detected. It passed Ashby too, but needs Xvfb installed, and plain headless passed without it. A system that refuses headless brings it back.
- A visible window placed off screen. Wayland compositors decide where a window goes, so it lands on the user's workspace.
- A new device identity on every launch, Camoufox's default. A device that changes on every visit, or claims a different operating system than the engine behaves like, is itself a signal.
- Attaching to one long-running browser shared with other tools. A shared browser carries every other tool's sessions, so a form-filling agent could reach a logged-in LinkedIn or Google, and clients that run at the same time share tabs with nothing coordinating them.
