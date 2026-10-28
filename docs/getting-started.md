# Getting started

atsfill reads the application form behind a job posting, fills it with your answers, and checks what the hiring system will receive before anything is sent. Every command prints one JSON document on one line and exits with a code a script can branch on, so an agent with a shell can drive it.

## Install

atsfill runs on Linux, macOS and Windows with Python 3.14, which `uvx` fetches if needed.

```sh
uvx atsfill install-browser
```

This downloads the Camoufox browser build atsfill is pinned to: 1.3 GB, about 2.5 GB once unpacked. Every push to `main` that passes CI is also published as a dev release; `uvx --prerelease allow atsfill` runs the newest.

## Read, fill, send

1. List the form's fields, with their ids, types, options and whether they are required:

    ```sh
    uvx atsfill read https://job-boards.greenhouse.io/acme/jobs/1234
    ```

2. Write the answers to `answers.json`, keyed by field id. A file takes an absolute path:

    ```json
    {
      "first_name": "Jane",
      "last_name": "Doe",
      "email": "jane@example.com",
      "resume": "/home/jane/resume.pdf"
    }
    ```

3. Dry-run the fill. It fills the form, reads every field back, never submits, and ends `ready` when every answer reads back as given:

    ```sh
    uvx atsfill fill https://job-boards.greenhouse.io/acme/jobs/1234 --answers answers.json
    ```

4. Allow the system to submit, in the config file:

    ```toml
    submit-allowed = ["greenhouse"]
    ```

5. Send. atsfill fills again and submits only when every answer reads back as given. A `submitted` result carries the evidence of the send:

    ```sh
    uvx atsfill send https://job-boards.greenhouse.io/acme/jobs/1234 --answers answers.json
    ```

A dry run is not invisible: some systems save answers or the resume as they are typed. `atsfill status` lists each system's side effects, whether it is allowed to submit, and its scope.

## Results

Each command prints one JSON result, without the keys that are null, false or empty; after `fill` and `send` only the checks that failed. `-v` prints all of it, indented.

| Status | Meaning | Exit |
|---|---|---|
| `ready` | `read`: the form is listed. `fill`: every answer read back as given | 0 |
| `submitted` | the application was sent; `evidence` says how atsfill knows | 0 |
| `refused` | atsfill would not go on: an answer not among the options, one that read back different (`mismatch`), submit not allowed, a posting already sent | 1 |
| `blocked` | the page stopped atsfill: a sign-in, a CAPTCHA, an OTP it could not get, the time budget | 1 |
| `unknown` | the click happened and nothing says whether the application went | 1 |
| `unsupported` | no adapter for this hiring system, or a form it cannot fill; the consumer can drive a browser itself | 1 |

A usage error exits 2. Any status but `ready` and `submitted` comes with a `reason`, `code` or `code: detail`.

## The config file

`~/.config/atsfill/config.toml` on Linux, `~/.atsfill/config.toml` elsewhere. The agent never writes it: it is where the person who owns the machine decides what atsfill may do.

```toml
submit-allowed = ["greenhouse", "lever"]   # the systems send may submit on
mute-side-effect-warnings = ["greenhouse"] # side effects you have read and accept
telemetry = "basic"                        # "off", "basic" or "detailed"

[password-commands]
lever = "rbw get lever.co {email}"         # a password source per system that needs an account

[otp-commands]
all = "my-mail-otp {email} {since}"        # prints an emailed OTP
```

A posting already sent is refused unless you pass `--resend`.

## Users and accounts

One home can send for more than one user: `--user <name>` gives each their own sessions, double-send guard and `[users.<name>]` section in the config file ([ADR 0015](adr/0015-users-and-accounts.md)).

Some systems need an account, to read the form or only to send it ([the hiring systems](adapters/README.md) say which). Given an account's email (`atsfill login <adapter> --email`) and a password source, atsfill signs in by itself, and again whenever a form command finds the session signed out. When a system emails an OTP, atsfill gets it from the `[otp-commands]` entry and types it in; without one the command ends `blocked: no_otp_command`.

Where a system's sign-up allows it, `atsfill login <adapter> --create --email <address>` creates the account with a password atsfill generates and keeps in the system's secret store, never printed. Creating an account is a side effect, never part of `fill` or `send`.

## When a run fails

A failed command's result names `atsfill report`, which sends that run's URL and page, scrubbed of your answers, to the maintainer, once. [Telemetry](telemetry.md) says what is sent and how to turn it off.
