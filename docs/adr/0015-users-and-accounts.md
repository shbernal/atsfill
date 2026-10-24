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

[users.test]
mute-side-effect-warnings = ["all"]
```

The keys `submit` and `ack_side_effects` are an error naming their replacement.

**Account scope.** A user has one account per hiring system and scope. An adapter's `account_scope(url)` gives the scope: None where an account covers the whole system (Welcome to the Jungle), the employer where each employer keeps its own (Workday: the tenant, whatever the URL form). A form command derives the scope from the posting URL; `login` and `logout` take it from `--url <posting>`.

**Login.** `atsfill login <adapter> [--user u] [--url posting]` signs in with the account's password source, or opens the window for a person when there is none (`--window` forces it). `--create` makes the account where the adapter supports sign-up: atsfill generates the password and stores it before clicking, and never prints it or takes it on the command line. With a password already stored the account may exist (a rerun, or a second posting on the same tenant), so `--create` signs in with it instead, keeping the account's email: a generated password never replaces a working one. A sign-up that asks the user's name (an adapter's `asks_name`) takes it from `--name`; atsfill keeps no name of its own. Outcomes: `signed_in`; `refused` with `email_in_use`, `email_domain_rejected`, `password_rule`; `blocked` with `email_link`, `email_otp`, `sms_otp`, `captcha`, `rate_limited`; `unsupported` with `unhandled_rendering`.

**Account creation is a side effect** (ADR 0008) and never implicit: never part of `fill` or `send`. A consumer's harness asks before `login --create` for a real user. Development creates Test user accounts freely.

**Verification links and OTPs.** The user's mailbox is outside atsfill. atsfill reports what the hiring system waits for, and the consumer (the user, or an agent with access to the mailbox) supplies it. A verification link is a one-time click in the mailbox: `blocked: email_link` names the address and the `login` command to run again once it is clicked; atsfill takes nothing back. OTPs (`email_otp`, `sms_otp`) are a known gap.

**Password sources.** Where atsfill gets an account's password, first found wins: `--password -` on stdin for one sign-in, the user's `password-commands` entry for the adapter, the secret store for passwords atsfill generated. A command is split into arguments, without a shell; `{email}` and `{scope}` are filled per argument. With a source, a form command that finds the session signed out signs in again on its own, which is not a side effect. Unlocking a password manager for an agent trusts the agent with the password: atsfill never prints it, and cannot stop the agent from fetching it.

**Secret store.** Generated passwords go to the system's secret store through `keyring`, service `atsfill`, entry `<user>/<adapter>[/<scope>]`; to `users/<user>/passwords.json` (0600) when no backend is available, `status` naming which. A generated password is known only to atsfill: once deleted, the way back is the hiring system's password reset through the user's mailbox.

**Removing stored data.** `logout` deletes a session. Passwords are removed with the secret store's own tools, a whole user by deleting `users/<user>/`. A command for it waits for a need.

## Consequences

A hiring system with an account per employer is served without a person at each employer's window. A user's double-send guard, sessions and submit permission are theirs alone; a home sending for two users keeps them apart. Every user of a home shares its device identity (ADR 0004). A generated password lost with the secret store costs a password reset per account.
