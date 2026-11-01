# Telemetry

atsfill sends one anonymous event after each `read`, `fill`, `send` and `profile`, so that the [run history](run-history.md) can show whether each hiring system's adapter works today. The first command that sends one says so on stderr and in its JSON output (`notice`), once per data directory.

## What is sent

At `basic`, the default, one `run` event:

| Property | Example |
|---|---|
| `system` | `greenhouse`; for a posting no adapter handles, the registry source its host belongs to, or `unknown` |
| `command` | `read`, `fill`, `send` or `profile` |
| `verdict` | `pass`, `fail`, `excluded` or `unsupported` |
| `status`, `reason` | `refused`, `mismatch`: the reason's code only, never its detail |
| `refused` | for `invalid_answers` and `behind_consent`, each `field id: rule`, as `q_12: not_an_option`: the id the form gives the field, never the answer |
| `version` | the atsfill version |
| `posting` | a hash of the posting, see below; none after `profile`, which has no posting |
| `source` | `user` for an installed release, `dev` for a checkout of the repository |
| `commit` | for `dev` only, the checkout's commit when it has no uncommitted changes |

Nothing the user typed is sent: no answers, no field values, no id of a field the form does not list, no URL, no account, no email address, no file. Each event has its own random id and there is no install id, so nothing ties one machine's events together. Events go to PostHog's EU Cloud (Frankfurt), which drops IP addresses; atsfill turns GeoIP off too.

## A failed run's report

A run whose verdict is `fail` has a `report` event: the `run` event's properties, plus the posting URL, the reason's detail, and `snapshot`, the page as it stood when the run failed. atsfill keeps the last one in the data directory, `failed-run.json`, and the command's JSON output names `atsfill report` (`report`), which sends it, once, whatever the level but `off`. At `detailed`, the run sends its report itself, and its `run` event carries the URL.

The snapshot is scrubbed on the machine, before it is kept:

- scripts, styles, images drawn in SVG and metadata are removed;
- every field's value, checked and selected state is stripped;
- every answer, uploaded file name, account email and value the account filled in for you is replaced by `[scrubbed]` wherever the page shows it, each word of an answer of up to four words too, and so is any email address; answers shorter than three characters are left;
- a page over 800 KiB is cut to the smallest part holding every field of the failing step.

## The posting hash

The hash is pseudonymous, not anonymous. It is a SHA-256 of the posting's key, the URL the double-send guard keys a posting by, without its fragment and `utm_` parameters. The hash cannot be reversed, but the maintainer can hash a list of public postings and see which of them match. That tells which public postings were run, never by whom.

## Turning it off

In the config file (`~/.config/atsfill/config.toml` on Linux, `~/.atsfill/config.toml` elsewhere), at the top level:

```toml
telemetry = "off"   # "basic" by default, or "detailed"
```

Setting `DO_NOT_TRACK=1` in the environment also turns it off, whatever the config says. atsfill never sends from its test suite or from CI.
