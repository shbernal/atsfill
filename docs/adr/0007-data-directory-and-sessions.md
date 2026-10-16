# 0007. Data directory and sessions

## Decision

Everything atsfill keeps lives under one data directory: `$XDG_STATE_HOME/atsfill` on Linux (`~/.local/state/atsfill` when the variable is unset), `~/.atsfill` elsewhere. `--data-dir` (or `ATSFILL_DATA_DIR`) moves it. The config file (ADR 0006) lives apart, in the config directory. Created `0700`.

```
identity.json             the device identity every launch reuses (ADR 0004)
sessions/<adapter>.json   one Playwright storage state per system that needs an account, 0600
attempts.jsonl            the double-send guard
lock                      held by the running command (ADR 0004)
```

A hiring system that needs an account (Welcome to the Jungle, later Y Combinator's Work at a Startup or Workday) uses the session found at `sessions/<adapter>.json`, or the file `--session <adapter>=<path>` names. It is loaded only into that adapter's context. Without one, the adapter returns `blocked: login_required` with the command that fixes it.

Sessions get in through a login window only a person can complete:

- `atsfill login <adapter>` opens a visible window on the system's login page and saves the session once signed in. The password goes to the system's own page, never through atsfill.
- `atsfill logout <adapter>` deletes it.
- `atsfill status` also checks every stored session and exits 1 if one is signed out.

Sessions are never imported from the user's everyday browser.
