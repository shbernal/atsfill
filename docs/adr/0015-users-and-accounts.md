# 0015. Users and accounts

Supersedes the login part of ADR 0007.

## Context

ADR 0007 gives each hiring system that needs an account one session per data directory, signed in by a person in a window. Some hiring systems keep one account per employer (Workday: one per tenant), so one session per system cannot serve them, and a window per employer does not scale. One computer account may also apply for more than one person, and the project needs a fictional user of its own whose accounts development can create freely.

## Decision

**User and home.** A user is the person applications are sent for: their accounts and attempts. Answers stay the consumer's file, passed with `--answers`. The home is the operating-system account atsfill runs under, through its home directory: the config file and the data directory. One home serves one or more users; the person who owns the home writes the config and answers the send prompt. Commands pick a user with `--user <name>` (lowercase letters, digits and `-`), `default` without it. The Test user is the fictional user the project tests with, `--user test`.

**Data directory per user.**

```
identity.json, lock                       the home's
users/<user>/attempts.jsonl               the double-send guard is per user
users/<user>/accounts.json                each account's email
users/<user>/sessions/<adapter>.json
users/<user>/sessions/<adapter>/<scope>.json
users/<user>/passwords.json               0600, only without a secret store
```

A data directory from before users has its `sessions/` and `attempts.jsonl` moved to `users/default/` once, on first use.

**Config per user.** Keys are kebab-case. Top-level keys are the `default` user's; `[users.<name>]` holds another user's, complete, nothing inherited: the Test user gets no `submit-allowed`, so it cannot send.

```toml
submit-allowed = ["ashby"]
mute-side-effect-warnings = ["all"]
[password-commands]
wttj = "rbw get welcometothejungle.com {email}"
[otp-commands]
all = "mailbox wait {email} --code --since {since}"

[users.test]
mute-side-effect-warnings = ["all"]
```

The keys `submit` and `ack_side_effects` are an error naming their replacement.

**Account scope.** A user has one account per hiring system and scope. An adapter's `account_scope(url)` gives the scope: None where an account covers the whole system (Welcome to the Jungle), the employer where each employer keeps its own (Workday: the tenant, whatever the URL form). A form command derives the scope from the posting URL; `login` and `logout` take it from `--url <posting>`.

**Login.** `atsfill login <adapter> [--user u] [--url posting]` signs in with the account's password source, or opens the window for a person when there is none (`--window` forces it). `--create` makes the account where the adapter supports sign-up: atsfill generates the password and stores it before clicking, and never prints it or takes it on the command line. With a password already stored the account may exist (a rerun, or a second posting on the same tenant), so `--create` signs in with it instead, keeping the account's email: a generated password never replaces a working one. That sign-in takes the `--details` too, refused the same way when one in `asks` lacks, and an adapter whose system can leave an account half made (a step the sign-up shows after the account exists, unfinished) gives them to that step; a plain sign-in that lands on it is `refused: sign_up_details`. What a sign-up asks of the user (an adapter's `asks`: `name`, `phone`, `street`, `city`, `state`, `zip`, `country`) comes from `--details <file.json>`, `{"name", "phone", "address": {"street", "city", "state", "zip", "country"}}`, every key optional, the phone in E.164, the country in ISO 3166 alpha-2 and the state by its name, as the country's own lists write it (`Illinois`, not `IL`): hiring systems list states by name, and no standard maps codes to the names they use; a file of another shape is a usage error. A detail the adapter asks that the file lacks is `refused: sign_up_details: no <keys>`, before the browser opens. A detail only some tenants require, once the page shows it, is refused the same way by the adapter, before it sends anything, so the generated password goes; one a tenant asks only after the account exists belongs in `asks`. atsfill keeps none of the details: the consumer passes them on each `--create`, and they go to the hiring system under the adapter's `sign_up` side effect (ADR 0008). Optional consents a sign-up offers, such as text messages, stay declined. An account without a password, on a system that emails an OTP at every sign-in, signs in through the OTP command in place of a password source: atsfill generates, stores and takes no password for it, `--password` is a usage error and `--create` reports `password: none`. Such a system turns an unknown email's sign-in into a sign-up, so a sign-in without `--create`, a form command's included, stops before that click: `refused: no_account` names the `login --create` command to run. `--create` signs in where the email already has an account. Outcomes: `signed_in`; `refused` with `sign_up_details`, `no_account`, `email_in_use`, `email_domain_rejected`, `password_rule`; `blocked` with `email_link`, `email_otp`, `sms_otp`, `no_otp_command`, `otp_not_received`, `otp_rejected`, `captcha`, `rate_limited`; `unsupported` with `unhandled_rendering`.

**Account creation is a side effect** (ADR 0008) and never implicit: never part of `fill` or `send`. A consumer's harness asks before `login --create` for a real user. Development creates Test user accounts freely.

**Verification links.** The user's mailbox is outside atsfill. A verification link is a one-time click in the mailbox: `blocked: email_link` names the address and the `login` command to run again once it is clicked; atsfill takes nothing back.

**OTPs.** A system sends a fresh OTP each time its step shows, so a command that stopped there and ran again later would meet a new one: the page stays open while atsfill gets the OTP from the user's `otp-commands` entry for the adapter, else its `all` entry, and types it in. The entry is a command, split into arguments without a shell like a password command, with `{email}` (the address the OTP went to, taken to be the one atsfill typed: no system has been seen sending it elsewhere, [#176](https://github.com/shbernal/atsfill/issues/176)), `{scope}` and `{since}` (when the step asked for it, ISO 8601 in UTC, so an older OTP is not taken) filled per argument. atsfill runs it until it exits 0 printing the OTP, its first word, within `OTP_TIMEOUT_S` (90 seconds); a command that waits for the mail itself runs once, stopped at the timeout. Without an entry the command ends `blocked: no_otp_command`, with no OTP in time `blocked: otp_not_received`, naming the step and the address, and with one the page does not take `blocked: otp_rejected`. `email_otp`, like `sms_otp`, is an OTP step an adapter does not type into. It serves sign-ins, sign-ups and form steps alike, and is the whole sign-in of an account without a password, in the core once: an adapter only finds its system's OTP step and the input it takes, and says what the page did with it. An SMS OTP (`sms_otp`) stays a known gap. Typing an OTP is not a side effect: the system sent it for the step atsfill was already on.

**Password sources.** Where atsfill gets an account's password, first found wins: `--password -` on stdin for one sign-in, the user's `password-commands` entry for the adapter, the secret store for passwords atsfill generated. A command is split into arguments, without a shell; `{email}` and `{scope}` are filled per argument. With a source, or the OTP command for an account without a password, a form command that finds the session signed out signs in again on its own, which is not a side effect. Unlocking a password manager for an agent trusts the agent with the password: atsfill never prints it, and cannot stop the agent from fetching it.

**Secret store.** Generated passwords go to the system's secret store through `keyring`, service `atsfill`, entry `<user>/<adapter>[/<scope>]`; to `users/<user>/passwords.json` (0600) when no backend is available, `status` naming which. A generated password is known only to atsfill: once deleted, the way back is the hiring system's password reset through the user's mailbox.

**Removing stored data.** `logout` deletes a session. Passwords are removed with the secret store's own tools, a whole user by deleting `users/<user>/`. A command for it waits for a need.

## Consequences

A hiring system with an account per employer is served without a person at each employer's window. A user's double-send guard, sessions and submit permission are theirs alone; a home sending for two users keeps them apart. Every user of a home shares its device identity (ADR 0004). A generated password lost with the secret store costs a password reset per account.
