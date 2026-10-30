---
name: atsfill
description: "Use when applying for a job through its posting's application form with the `atsfill` CLI: listing the form's fields, writing the answers, dry-running the fill, sending the application, or signing in to a hiring system that needs an account."
---

# Applying with atsfill

`atsfill` fills the application form behind a job posting (about 30 hiring systems) in a headless browser, reads it back, and submits only when every answer reads back as given. Each command prints one JSON result on one line.

The project moves fast: run the newest dev release, not the stable one.

```bash
uvx --prerelease allow atsfill@latest install-browser   # once, about 1.3 GB
uvx --prerelease allow atsfill@latest status            # version, config path, each system's side effects
```

Below, `atsfill` stands for `uvx --prerelease allow atsfill@latest`.

## 1. Read the form

```bash
atsfill read <posting-url>
```

`fields` lists each field's `id`, `label`, `type`, `required`, and where they apply `options`, `max_length`, `accept`, `prefilled`, `shown_when` (a follow-up), `one_of` (answer exactly one field of the group) and `unfillable`.

## 2. Write the answers

One JSON file `{field_id: value}`, keyed by `id`, never by label:

- `text`, `phone`, `choice`, `typeahead`: a string; a choice is one of its `options`, exactly.
- `multi_choice`: a list of options; an empty list clears an optional field.
- `date`: `YYYY-MM-DD`.
- `url`: an absolute `http(s)` URL.
- `file`: an absolute path.

Answer every required field not `prefilled`, and a follow-up only when its `shown_when` holds. Take answers from the user; never invent one about them (salary, visa, demographics). Leave out what you do not know and ask.

## 3. Dry-run, then send

```bash
atsfill fill <posting-url> --answers answers.json   # never submits
atsfill send <posting-url> --answers answers.json   # a real application, final
```

`fill` ends `ready` when every answer reads back as given. A dry run is still visible: some systems save answers or the resume as they are typed (`status` lists them).

Run `send` only once the user has approved this application. It needs the system in `submit-allowed` in the user's config file: that file is the user's, never edit it. A posting already sent is refused; pass `--resend` only when the user asks.

## Results

`status` is `ready`, `submitted`, `refused`, `blocked`, `unknown` or `unsupported`; exit 0 for `ready` and `submitted`, 1 otherwise, 2 for a usage error. When it is not clean, `reason` is `code: detail`, and the detail says what to do next: fix the answers on `invalid_answers` or `mismatch`, run the command it names on `login_required` or `profile_incomplete`. `-v` prints every check and key.

- `unsupported`: no adapter for that system. Apply another way, a browser of your own.
- `submitted`: `evidence` is the system's confirmation. Report it to the user.
- `unknown` after a `send`: it may have gone. Do not resend; tell the user.
- A failed run prints how to `atsfill report` it to the maintainer, which sends the posting's URL and page. Ask the user first.

## Accounts

A few systems need an account (`atsfill login --help` lists them). `atsfill login <system> --email <address>` signs in, with the password from the user's `[password-commands]`, or `--password -` on stdin. `--create` makes the account, `--window` lets a person sign in. An emailed OTP is read through the user's `[otp-commands]`. Where a system applies only from a complete profile, `atsfill profile <system>` reads it and `--answers` saves it.

`--user <name>` keeps one person's sessions and sends apart from another's.
