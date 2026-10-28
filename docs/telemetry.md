# Telemetry

atsfill sends one anonymous event after each `read`, `fill`, `send` and `profile`, so that the [run history](adr/0016-telemetry-and-run-history.md) can show whether each hiring system's adapter works today. The first command that sends one says so on stderr and in its JSON output (`notice`), once per data directory.

## What is sent

At `basic`, the default, one `run` event:

| Property | Example |
|---|---|
| `system` | `greenhouse`; for a posting no adapter handles, the registry source its host belongs to, or `unknown` |
| `command` | `read`, `fill`, `send` or `profile` |
| `verdict` | `pass`, `fail`, `excluded` or `unsupported` |
| `status`, `reason` | `refused`, `mismatch`: the reason's code only, never its detail |
| `version` | the atsfill version |
| `posting` | a hash of the posting, see below; none after `profile`, which has no posting |
| `source` | `user` for an installed release, `dev` for a checkout of the repository |
| `commit` | for `dev` only, the checkout's commit when it has no uncommitted changes |

Nothing the user typed is sent: no answers, no field values, no URL, no account, no email address, no file. Each event has its own random id and there is no install id, so nothing ties one machine's events together. Events go to PostHog's EU Cloud (Frankfurt), which drops IP addresses; atsfill turns GeoIP off too.

`detailed` sends the same event.

## The posting hash

The hash is pseudonymous, not anonymous. It is a SHA-256 of the posting's key, the URL the double-send guard keys a posting by, without its fragment and `utm_` parameters. The hash cannot be reversed, but the maintainer can hash a list of public postings and see which of them match. That tells which public postings were run, never by whom.

## Turning it off

In the config file (`~/.config/atsfill/config.toml` on Linux, `~/.atsfill/config.toml` elsewhere), at the top level:

```toml
telemetry = "off"   # "basic" by default
```

Setting `DO_NOT_TRACK=1` in the environment also turns it off, whatever the config says. atsfill never sends from its test suite or from CI.
