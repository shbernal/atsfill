# atsfill

Job application forms on hiring systems, read, dry-run and submitted from the command line on a user's behalf, with every rule about when a send may submit kept in one core.

## People

**User**:
The person applying: owns the answers, the config file and the decision to send.
_Avoid_: applicant, candidate

**Consumer**:
The agent or script that runs atsfill commands for the user.
_Avoid_: client, caller

**Harness**:
The consumer's permission layer, which asks the user before each send.

**Employer**:
The organization a posting belongs to, which receives the application.
_Avoid_: company, recruiter

## Hiring systems

**Hiring system**:
A product that hosts application forms for many employers: an ATS, or a job board that takes the application itself.
_Avoid_: platform, provider, site

**Adapter**:
The module that knows one hiring system's page. Its name (`ashby`, `wttj`) identifies the system in commands, the config file and results.

**Posting**:
One job opening on a hiring system, which an application is for. Every URL of a posting has the same key.
_Avoid_: job, offer, listing

**Form**:
The application form behind a posting.

**Field**:
One input on a form, identified by a stable id taken from the form. Its label is for display only.
_Avoid_: input, question

**Field type**:
How a field's answer is written and matched: `text`, `date`, `choice`, `multi_choice`, `typeahead`, `file` or `phone`.

**Rendering**:
One way a field type appears on a hiring system's page, such as a native select or a combobox that saves on Enter.

**Side effect**:
Something the hiring system or the employer receives before the submit, such as a field saved as it is typed or an uploaded resume.

**Account**:
A user's sign-in on a hiring system, needed by some systems to read or to send.

**Session**:
The stored browser state of a signed-in account, loaded only into its own adapter's context.
_Avoid_: cookies, login

**Prefilled**:
Said of a field that holds a value before any answer, from the account's profile.

## Answers and read-back

**Answers**:
The user's map from field id to value for one form.
_Avoid_: responses, profile

**Read-back**:
What the hiring system will receive for each field after a fill: from the server's response where it has one, else from the form state.

**Check**:
One field's answer against its read-back, ending in a match or a mismatch.

## Commands and outcomes

**Read**:
Listing a form's fields, over HTTP when the adapter can, else from the page.

**Dry run**:
A fill followed by a read-back, never a submit. What the `fill` command does.
_Avoid_: preview, test run

**Send**:
The `send` command: a fresh fill and read-back, then a submit when the submit gate allows it.
_Avoid_: apply

**Submit**:
The adapter step that clicks the form's submit control and returns the evidence. Only a send submits.

**Application**:
A form submitted for a posting: real and final.

**Submit gate**:
The core's rules for whether a send may submit: every check matches, the adapter is enabled to submit, and the posting was not already sent.
_Avoid_: gate, read-back gate

**Attempt**:
The record of a send that reached the submit step, keyed by posting. Its last outcome is what the double-send guard reads.

**Double-send guard**:
The part of the submit gate that refuses a send for a posting whose last attempt ended `submitted`, `unknown` or without an outcome, unless the user passes `--resend`.

**Status**:
The one outcome every form command ends in: `ready`, `refused`, `submitted`, `blocked`, `unknown` or `unsupported`.

**Reason**:
The snake_case code, with an optional detail, that explains a status other than `ready` or `submitted`.

**Evidence**:
A URL or network response that proves a submit went through. Never page text.

**Time budget**:
The time a command has, lock wait included, before it stops and returns a status.

**Device identity**:
The browser fingerprint generated on the first launch and reused by every later one.
_Avoid_: fingerprint, profile

**Data directory**:
Where atsfill keeps the device identity, sessions, attempts and the lock.

## Registry

**Registry**:
The committed record of every mapped hiring system: its kind, share, account need, block and verification.

**Source**:
freehire's name for where a posting comes from; the registry has one entry per source.

**Kind**:
What a source is: `ats`, `board` (a job board that takes the application), `aggregator` (links out to other sources) or `custom` (a form the employer built).

**Share**:
A source's fraction of freehire's postings, worldwide, on the date it was measured.

**Block**:
A bot protection that stopped Camoufox on a hiring system, recorded with its vendor, signal and step.

**Verification**:
How far an adapter has been proven: `mapped`, `dry_run_verified`, then `send_verified`, each recorded with its date, release and commit. Only the user sets `send_verified`.
_Avoid_: adapter state

**Send broken**:
A send-verified adapter whose send fails now. The last send verification stays as the last known good.

## Development

**Survey**:
A run of `read` and a dry run with the test persona over a list of posting URLs, one row per URL. It never sends.

**Corpus**:
The survey's rows, in `.tmp/corpus/`.

**Test persona**:
The committed fictional user whose answers are derived from each form's own fields.

**Fixture page**:
A hand-made HTML page that reproduces one hiring system's renderings for the committed tests.

**Adapter gate**:
`scripts.gate`, an adapter's definition of done: fixture page, adapter doc, tests, and dry runs `ready` on employers outside the round's sample.
_Avoid_: gate, done check

**Sample**:
The postings a round surveys first to learn its system, about fifteen employers' worth. The adapter gate counts only postings outside it.

**Inventory**:
The field type × rendering table in an adapter's doc: every rendering the sample showed, supported or not, and how many postings showed it.

**Round**:
One session that adds or repairs one adapter, ending in a pull request or a registry entry.

## Flagged ambiguities

- "Gate" meant both the submit gate and the adapter gate. Always qualify it.
- "Inventory" meant both a round's surveyed postings (now the sample) and the field type × rendering table (still the inventory).
- "Adapter state" meant both the verification and the whole `status` row for an adapter. The ladder is the verification.
- A **Block** (a bot protection in the registry) is not the `blocked` status, which also covers emailed codes, login walls and the time budget.
- "Company" and "employer" named the same organization. It is the employer. "Company" stays only where it is a hiring system's own word: a field label, a URL path.
