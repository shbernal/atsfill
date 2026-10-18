# 0002. Stack

## Decision

- Python 3.14, uv, ruff, ty and pytest. Code is fully annotated and ty checks it on every commit, so a wrong type fails before a test runs. ty is in beta like Camoufox and pinned the same way; pyright is the fallback if it stalls.
- Camoufox for the browser: a Firefox build patched in C++ to hide automation and present a consistent device, driven through its Python package, which returns a stock Playwright `Browser`. Playwright's API is kept over raw CDP (Chrome DevTools Protocol) for its auto-waiting locators, file uploads, network response capture and storage state, which ADRs 0004 to 0007 rely on. Camoufox is in beta and pinned to an exact version; an upgrade is a deliberate change, checked with a live dry run.
- MIT license, published to PyPI as `atsfill` and run with `uvx atsfill`. CI and publishing on GitHub Actions.

## Rejected

- patchright, a Playwright build for Chromium that removes the CDP signals reCAPTCHA scores. It passed Ashby's reCAPTCHA Enterprise where stock Playwright is refused, but stealth there rests on one class of protocol leaks, behind a small team that follows Playwright release by release. Camoufox patches the engine itself, which holds up headless (ADR 0004).
- camofox-browser, a Node REST server over Camoufox for agents that drive a browser themselves. It is the long-running shared browser ADR 0004 rejects, behind an API of its own instead of Playwright's.

## Consequences

Camoufox passes Ashby's reCAPTCHA Enterprise, whose refusal only shows on submit: real Ashby `send`s, on a virtual display and in plain headless (ADR 0004), ended `FormSubmitSuccess` on 2026-10-18, where stock Playwright is refused with `RECAPTCHA_SCORE_BELOW_THRESHOLD`. Camoufox downloads its own Firefox build (1.3 GB, about 2.5 GB unpacked) instead of using an installed browser.

`camoufox` and `playwright` are imported in one module, the browser launcher. Everything else takes a page or a context, so a change of browser library touches one file.
