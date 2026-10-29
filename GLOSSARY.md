# atsfill

Job application forms on hiring systems, read, dry-run and submitted from the command line on a user's behalf, with every rule about when a send may submit kept in one core.

## People

**User**:
The person applications are sent for, with their accounts and attempts. Picked with `--user`, `default` without it.
_Avoid_: applicant, candidate

**Home**:
The operating-system account atsfill runs under, through its home directory: the config file and the data directory. It serves one or more users; its owner writes the config and answers the send prompt.
_Avoid_: machine, OS user

**Consumer**:
Whoever runs atsfill commands for the user: an agent, a script, or a person.
_Avoid_: client, caller

**Harness**:
The consumer's permission layer, which asks the home's owner before each send.

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

**Tenant**:
One employer's own space on a hiring system: its career site, its settings and, on some systems, its accounts. Usually named in the posting URL (`acme` in `acme.wd5.myworkdayjobs.com`).
_Avoid_: instance, subdomain

**Posting**:
One job opening on a hiring system, which an application is for. Every URL of a posting has the same key.
_Avoid_: job, offer, listing

**Form**:
The application form behind a posting.

**Field**:
One input on a form, identified by a stable id taken from the form. Its label is for display only.
_Avoid_: input, question

**Follow-up**:
A field the form shows only once other fields' answers meet its condition.
_Avoid_: dependent field, branching question, conditional question

**Alternative**:
One of a group of fields that take the same thing in different shapes (a resume as a file or as a text), sharing a key in `one_of`. The form expects exactly one; the group, not the field, is required.
_Avoid_: exclusive field, either-or field

**Dependent list**:
A choice whose options follow other fields' answers, a state list under a country: one option set per condition. It always shows, so it is not a follow-up.
_Avoid_: dependent field, cascading select

**Condition**:
What decides, from other fields' answers, whether a field shows (`shown_when`) or is required (`required_when`), or which options a dependent list offers (`option_sets`): `Is`, `AnyOf`, `AllOf`.

**Field type**:
How a field's answer is written and matched: `text`, `url`, `date`, `choice`, `multi_choice`, `typeahead`, `file` or `phone`. A field is `url` only where its hiring system declares it one, never from its label.

**Rendering**:
One way a field type appears on a hiring system's page, such as a native select or a combobox that saves on Enter.

**Overlay**:
Whatever a page draws on top of a control, taking the click meant for it: a cookie banner, a toast, a spinner, a modal.
_Avoid_: covered (reads as test coverage)

**Toast**:
An overlay that shows a short message for a few seconds and goes by itself, such as a résumé parse's result. The Rippling fixture page names its toast `.notice`; that class is not the term.
_Avoid_: notice

**Side effect**:
Something the hiring system or the employer receives before the submit, such as a field saved as it is typed or an uploaded resume, an account created or a profile step saved. Muted per user with `mute-side-effect-warnings`.

**Account**:
A user's sign-in on a hiring system, needed by some systems to read or to send: one per user, hiring system and account scope.

**Account scope**:
What one account covers on a hiring system: the whole system (no scope), or one tenant where each keeps its own accounts, as on Workday.

**Login**:
Signing in to an account, which stores its session: by a person in a visible window, or by atsfill with a password source, or with the OTP command for an account without a password. Also the `login` command.

**Password source**:
Where atsfill gets an account's password: `--password -` on stdin, the user's `password-commands` entry, or the secret store for passwords atsfill generated. An account without a password has none: the OTP command stands in for it.

**Sign-up details**:
What creating an account asks of the user besides the email and the password (a name, a phone, an address), passed by the consumer on each `login --create --details` and stored nowhere by atsfill. Not answers: they go to the sign-up, not to a form.

**Session**:
The stored browser state of a signed-in account, loaded only into its own adapter's context.
_Avoid_: cookies

**OTP**:
A one-time password a hiring system sends by email or SMS and asks for before it goes on. atsfill types an emailed one in from the OTP command. An OTP step an adapter does not type into is `email_otp` or `sms_otp`; one it does ends `no_otp_command` without an OTP command, `otp_not_received` when the command prints none in time, `otp_rejected` when the system does not take what it printed.
_Avoid_: code, verification code, PIN

**OTP command**:
The user's `otp-commands` entry for an adapter, or for `all`: a command atsfill runs while the OTP step waits, until it prints the OTP emailed since the step asked for it.

**Account profile**:
What a hiring system keeps about an account and prefills its forms from. Some systems apply only from a complete one.

**Profile form**:
The form an account profile is edited through, in one or more profile steps. Only the `profile` command fills it, never `fill` or `send`.
_Avoid_: wizard, onboarding

**Profile step**:
One page of a profile form, which the hiring system saves on its own.
_Avoid_: section

**Save**:
Writing one profile step to the hiring system: a side effect, repeatable, never an application.
_Avoid_: submit (for a profile)

**Prefilled**:
Said of a field that holds a value before any answer, from the account profile, or one the page sets and takes no other for (today's date, read-only). A required field prefilled may go unanswered.

**Default**:
The value a page starts a field with, not from the account profile: the employer's country, today's date on a signature. Conditions read it; it does not answer a required field.

## Answers and read-back

**Answers**:
The user's map from field id to value for one form, or for one account profile's profile form.
_Avoid_: responses, profile

**Fill**:
An adapter writing the answers into a form, never submitting it. Also the `fill` command, which is a dry run.

**Read-back**:
What the hiring system will receive for each field after a fill: from the server's response where it has one, else from the form state.

**Check**:
One field's answer against its read-back, ending in a match or a mismatch.

## Commands and outcomes

**Read**:
Listing a form's fields, by a request read when the adapter has one and it answers, else by a browser read.

**Request read**:
Listing a form's fields with plain HTTP requests, no browser, from a public API, an undocumented API, hydration data or server-rendered HTML. An adapter has one or not; without one, a read is a browser read.
_Avoid_: HTTP read, API read, read over HTTP

**Browser read**:
Listing a form's fields from the form in the browser, once its scripts have rendered it. Every adapter has one; `fill` and `send` always use it.
_Avoid_: page read, read from the page

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
The core's rules for whether a send may submit: every check matches, the adapter is in the user's `submit-allowed`, and the posting was not already sent.
_Avoid_: gate, read-back gate

**Attempt**:
The record of a send that reached the submit step, keyed by posting: `clicked` until its outcome is recorded. Its last outcome is what the double-send guard reads.

**Double-send guard**:
The part of the submit gate that refuses a send for a posting whose last attempt ended `submitted`, `unknown` or without an outcome, unless `--resend` is passed.

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

**Evidence**:
A URL or network response that proves a submit went through. Never page text.

**Time budget**:
The time a command has, lock wait included, before it stops and returns a status.

**Device identity**:
The browser fingerprint generated on the first launch and reused by every later one.
_Avoid_: fingerprint, profile

**Data directory**:
Where atsfill keeps the home's device identity and lock, and each user's sessions, accounts and attempts.

## Registry

**Registry**:
The committed record of every mapped source: its kind, share, account need, block and scope.

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

**Scope**:
Where a source stands with atsfill: `out_of_scope`, `in_scope` (wanted, no adapter passing the adapter gate yet) or `supported` (a dev run's dry run passed, recorded with its date and commit, never demoted). Whether an adapter works today is the run history's to say.
_Avoid_: verification, adapter state

## Telemetry

**Telemetry**:
The anonymous events atsfill sends to PostHog after each `read`, `fill` and `send`, at the level the config's `telemetry` key sets: `off`, `basic` (the default: system, command, verdict, status and reason code, version, posting hash) or `detailed` (also the posting URL and a report).
_Avoid_: analytics, tracking

**Verdict**:
What one run says about its adapter, classified by the core from its status and reason: `pass`, `fail`, `excluded` (the run says nothing about the adapter) or `unsupported`.
_Avoid_: outcome, result

**Report**:
The `report` event: a scrubbed snapshot of a failed run's failing step, input values stripped, sent at `detailed` or by `atsfill report`.

**Dev run**:
A run from a checkout of the repository rather than an installed release, sent with `source = dev` and its commit.

**Run history**:
The docs page that draws, per hiring system and for `fill` and `send`, the latest verdict of each posting and version over a window.
_Avoid_: dashboard, status page

**Failure**:
A posting and command whose latest verdict within the run history's window is `fail`, until it passes on another build, kept as one `failure` issue per hiring system, command and reason code.

**Regression**:
A failure whose posting passed before: on the same build, it points at the hiring system; on an older one, at the code.

**Triage**:
The local hourly agent run that takes one open `failure` issue, confirms it with a dry run and opens a pull request fixing it.

## Development

**Survey**:
A run of `read` and a dry run with the Test user over a list of posting URLs, one row per URL. It never sends.

**Corpus**:
The survey's rows, in `.tmp/corpus/`.

**Test user**:
The fictional user the project tests with, `--user test`, whose answers are derived from each form's own fields. Always capitalized and followed by "user".
_Avoid_: persona, fixture user

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
- "Dependent" meant both a follow-up, which shows on an answer, and a dependent list, whose options change with an answer and which always shows. The first is a follow-up; a dependent list is not one.
- "Inventory" meant both a round's surveyed postings (now the sample) and the field type × rendering table (still the inventory).
- "Adapter state" meant both a source's registry standing and the whole `status` row for an adapter. The first is the scope; "state" is left to the session (`signed_in`, `signed_out`, `unknown`, `missing`).
- "Session" meant both the stored browser state and an agent's working session. The agent's is an agent run.
- "Profile" meant the answers, the device identity and the account profile. Only the account profile keeps the word, never bare.
- "Login" is the act of signing in; the session is what it stores.
- "Code" meant both an OTP and a reason's snake_case code. Only the reason keeps the word.
- "Corpus" meant both the survey's rows and which postings mapping covers. The second is coverage.
- "Sample" meant the round's sample, a skipped posting, and drawing postings outside the sample. Only the first keeps the word.
- "Job board" meant both a board, which takes the application, and an aggregator, which links out.
- "System" meant both a hiring system and any registry entry. A registry entry is a source; "system" is a hiring system.
- "Outcome" meant both a status and an attempt's recorded result, and a verdict would have made three. A run's verdict about its adapter is the verdict.
- "Refusal" means the `refused` status only. A low reCAPTCHA score is `blocked`.
- A **Block** (a bot protection in the registry) is not the `blocked` status, which also covers OTPs, login walls and the time budget.
- "Gap" meant both a known gap and anything atsfill could not fill. A form it cannot fill is out of scope, a known gap or unhandled; "gap" alone is not used.
- "Out of scope" also named a `custom` source and finding postings. As a term it means only what needs the user live.
- "Company" and "employer" named the same organization. It is the employer. "Company" stays only where it is a hiring system's own word: a field label, a URL path.
- "User" meant both the person applications are sent for and the person who owns the home, writes the config and answers the send prompt. The second is the home's owner.
- "Read over HTTP" said nothing, since a browser talks HTTP too, and "API read" was wrong for adapters that parse HTML or hydration data. It is a request read.
- "Page read" was wrong for the browser read, since a request read can fetch the page too.
