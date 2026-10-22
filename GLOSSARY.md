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
A source whose pages hold the form an application is submitted through, for many employers: an ATS or a board. "System" alone means a hiring system.
_Avoid_: platform, provider, site

**ATS**:
A hiring system an employer runs its hiring on, showing the form on the employer's pages.

**Board**:
A job board that takes the application itself, so a hiring system.
_Avoid_: job board

**Aggregator**:
A source whose postings only link to a form on another source: a job aggregator or a career-site front-end. Never a hiring system, never an adapter. A hiring system some of whose postings link out stays a hiring system; those postings end in `external_step`.

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

**Login**:
Signing in to an account, in a visible window only a person can complete, which stores its session. Also the `login` command.

**Session**:
The stored browser state of a signed-in account, loaded only into its own adapter's context.
_Avoid_: cookies

**Account profile**:
What a hiring system keeps about an account and prefills its forms from.

**Prefilled**:
Said of a field that holds a value before any answer, from the account profile.

## Answers and read-back

**Answers**:
The user's map from field id to value for one form.
_Avoid_: responses, profile

**Fill**:
An adapter writing the answers into a form, never submitting it. Also the `fill` command, which is a dry run.

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
The record of a send that reached the submit step, keyed by posting: `clicked` until its outcome is recorded. Its last outcome is what the double-send guard reads.

**Double-send guard**:
The part of the submit gate that refuses a send for a posting whose last attempt ended `submitted`, `unknown` or without an outcome, unless the user passes `--resend`.

**Status**:
The one outcome every form command ends in: `ready`, `refused`, `submitted`, `blocked`, `unknown` or `unsupported`.

**Reason**:
The snake_case code, with an optional detail, that explains a status other than `ready` or `submitted`.

**Out of scope**:
A field whose answer needs the user present for its content, such as a video recorded live or a timed assessment, so stored answers and files can never give it. Its reason is `out_of_scope`. Also a source the registry marks so.

**Known gap**:
A rendering or a flow an adapter does not handle yet but is wanted, declared by the adapter with an id and listed under "Known gaps" in its doc. Its reason is `unsupported_rendering` or `unsupported_flow`.
_Avoid_: gap, coverage gap

**Unhandled**:
Said of a rendering or an error nobody has looked at yet, with no known gap behind it: `unhandled_rendering` or `adapter_error`.

**Warning**:
A code a send carries when it went ahead anyway: `not_send_verified` or `send_broken`.

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
The committed record of every mapped source: its kind, share, account need, block and verification.

**Source**:
freehire's label for where a posting was indexed. The registry has one entry per source; only an ATS or a board is a hiring system.

**Kind**:
What a source is: `ats`, `board`, `aggregator` or `custom` (a form the employer built, not a hiring system).

**Hosting**:
Where a source's forms are found: `hosted` on the hiring system, or `embedded` on the employer's own site.

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
`scripts.gate`, an adapter's definition of done: fixture page, adapter doc, the system's tests, and dry runs `ready` on employers outside the round's sample.
_Avoid_: gate, done check

**Sample**:
The postings a round surveys first to learn its system, about fifteen employers' worth. The adapter gate counts only postings outside it.

**Inventory**:
The field type × rendering table in an adapter's doc: every rendering the adapter handles and how many postings showed it. What it does not handle yet is a known gap.

**Round**:
One agent run that adds or repairs one adapter, ending in a pull request or a registry entry.

## Flagged ambiguities

- "Gate" meant both the submit gate and the adapter gate. Always qualify it.
- "Inventory" meant both a round's surveyed postings (now the sample) and the field type × rendering table (still the inventory).
- "Adapter state" meant both the verification and the whole `status` row for an adapter. The ladder is the verification; "state" is left to the session (`signed_in`, `signed_out`, `missing`).
- "Session" meant both the stored browser state and an agent's working session. The agent's is an agent run.
- "Profile" meant the answers, the device identity and the account profile. Only the account profile keeps the word, never bare.
- "Login" is the act of signing in; the session is what it stores.
- "Corpus" meant both the survey's rows and which postings mapping covers. The second is coverage.
- "Sample" meant the round's sample, a skipped posting, and drawing postings outside the sample. Only the first keeps the word.
- "Job board" meant both a board, which takes the application, and an aggregator, which links out.
- "System" meant both a hiring system and any registry entry. A registry entry is a source; "system" is a hiring system.
- "Refusal" means the `refused` status only. A low reCAPTCHA score is `blocked`.
- A **Block** (a bot protection in the registry) is not the `blocked` status, which also covers emailed codes, login walls and the time budget.
- "Gap" meant both a known gap and anything atsfill could not fill. A form it cannot fill is out of scope, a known gap or unhandled; "gap" alone is not used.
- "Out of scope" also named a `custom` source and finding postings. As a term it means only what needs the user live.
- "Company" and "employer" named the same organization. It is the employer. "Company" stays only where it is a hiring system's own word: a field label, a URL path.
