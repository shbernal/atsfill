You run unattended: nobody answers questions, so decide and record why.

The issue (`gh issue view`) names a hiring system, a command and a reason code. Its postings and their URLs: `uv run python -m scripts.failures <system> <command> <reason>`. URLs and employer names stay out of everything you write: commits, branch, pull request, issue comments.

1. Reproduce on this checkout, as it is, before changing anything: a dry run, `uv run atsfill fill`, with the Test user (CONTRIBUTING.md for systems with an account). Never `send`, whatever the command that failed: reproduce a `send` failure as far as a dry run reaches, then from the adapter's code against `docs/adapters/<system>.md`.
2. Check the postings fail for the one cause. If they don't, fix the most common one and say which part is left.
3. Fix on a branch `triage/<issue>-<system>-<reason>`: adapter, its doc and its fixture page together, a test that fails without the fix, the checks AGENTS.md names. Push the branch and open a pull request whose body has `Fixes #<issue>` and how you reproduced it. Do not merge it: the maintainer does.

End by replacing the issue's `agent:working` label with exactly one of these, and a comment saying why, starting with `Triage:`:

- `agent:done`: the pull request is open.
- `cannot-reproduce`: every posting passes now, is gone, or is unresolved; say what you ran.
- `agent:skip`: not the code's to fix: an SMS OTP (find or file its `otp-required` issue, as AGENTS.md says), a captcha, rate limiting, a choice that is the maintainer's.
- `agent:stuck`: you tried and could not fix it; say what you tried and what you found.
