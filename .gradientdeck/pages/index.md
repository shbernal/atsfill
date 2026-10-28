# Page plan: index

<!-- Written by the GradientDeck roll. Rolling this page again replaces the whole file. Development notes only: never copy any of this into the page, and keep .gradientdeck/ out of what you deploy. -->

Key: 292bbf82 · register roll · mode persuade
Build: "Airport departures board" (the user chose the first-ranked candidate over the roll's "Terminal JSON stream", 2026-10-28)
Job: understand that atsfill turns any hiring system's application form into read, fill and send, and refuses a send whose answers do not read back
Action: the copyable install and first read command, uvx atsfill read <posting>
Photos: none: the product is a CLI and its results; the hiring systems' own logos are the only imagery
Seen: generative-art-computational-color-chord-counterpoint, medium-native-hypercard-stack-shoebox, pop-culture-shelf-kaiju-broadcast-alert, design-canon-deco-cinema-lobby

## Ranked candidates
1. Airport departures board: each posting is a row on a split-flap board, hiring system as carrier, its status flipping READY, REFUSED, SUBMITTED; first screen is the board mid-run with the install command as the gate line
2. Terminal JSON stream: the page is a session where atsfill prints one JSON result per line; first screen is a real read, fill, send sequence with the install command at the prompt
3. Railway signal box interlocking: send is a lever that will not move until read-back, submit-allowed and not-already-sent levers are set; first screen is the lever frame with the command beside it and the send lever locked by a mismatch
4. Registered-mail form with carbon copy: the application is a printed form and the read-back is its carbon duplicate checked line by line; first screen is the top sheet filled and the carbon under it with one mismatched line circled
5. Election night results graphic: each hiring system reports like a constituency, supported or in scope, with share of postings; first screen is the results map and the command as the live ticker
6. Swiss transit wayfinding program: one grid of pictograms and route lines, one route per hiring system through read, fill, send; first screen is the route diagram with the command at the origin station
7. Jacquard punch-card loom: one answers card drives every system's loom; first screen is the card of answers feeding a row of looms each weaving its system's form

## Fill in before writing code
Replace every blank with your decision. `node roll.mjs check` lists any left.

Thesis: the job hunt becomes a departures hall: each posting is a flight, each hiring system a carrier, each result a remark on the board, and the send is a gate that closes on a wrong answer. It refuses the dev-tool landing (dark glow, centred headline, a row of feature cards) and the job-site one (friendly blue, people at laptops).
First screen: a thin header strip (atsfill wordmark left, Docs and GitHub right). Below, left, the offer line in Overpass 800, two lines, and one sentence under it. The rest is the board, full container width, near-black: column heads TIME, CARRIER, POSTING, COMMAND, REMARKS; seven rows of example postings, each carrier's mark in a white tile, remarks flipping on split-flap cells through CHECKING, READY, REFUSED, SUBMITTED. The board's last row is the gate line: GATE, then `uvx atsfill read <posting>` with a copy button, the action in working form inside the board.
Type and color: A) Overpass / Atkinson Hyperlegible Next / Overpass Mono against B) Archivo / Public Sans / Red Hat Mono, rendered at 1440 and 390: A won, Overpass's highway-sign letters read as wayfinding without costume, Atkinson stays easy at 18px, Overpass Mono's slashed zero suits the flap cells. Board ground #0e1013, flap #1c1f24, flap text #f1ede3, signage yellow #ffc61a the one accent (gate line, main action, current row); hall ground below #eeebe3 with ink #16181c. Dark for the board because a departures board is a lit sign; the hall is light because it is read at length.
Own system: two surfaces only, the lit board (near-black, flap cells with a 1px split line across their middle, 3px corners) and the hall (warm concrete light, ink text, 1px rules, no cards). Overpass speaks for signs and headings, Atkinson for reading, Overpass Mono only on flap cells and commands. Remarks carry their word always, colour second: READY and SUBMITTED green #5fd38d, REFUSED red #ff6b5b, BLOCKED and UNKNOWN amber, CHECKING white. Yellow belongs to the gate: the action, the copy button, the current row marker.
Story: (1) the board: what atsfill does, at a glance, with the install command. (2) One flight, three gates, on the hall: read lists the fields, fill reads every answer back, send submits only when all match; each step a real command and its one-line JSON, side by side, dense. (3) Gate closed: quiet, one large mismatch, answer against read-back, REFUSED; the send never happened. (4) Carriers serving this terminal: the supported systems' marks on a board-style wall, a link to each one's page; anything else returns unsupported. (5) Terminal rules: submit allowed per system in a config the agent does not write, the double-send guard, side effects declared, one JSON result and an exit code; a signage list, not cards. (6) Close: back on the board, NOW BOARDING row with the install command and links to the docs.
Signature: the split-flap remarks: each cell a two-half element that flips through the character drum with a CSS 3D rotateX on its upper and lower leaves, driven by a script that plays the example run (rows go CHECKING, then READY or REFUSED or SUBMITTED, staggered). Reduced motion shows the final board still.
Example content: the board's postings (Acme, Northwind, Contoso, Fabrikam, Tailspin, Wingtip, Litware: generic sample employers, with invented roles and times), the read/fill/send JSON lines for an example Acme posting, the mismatch (level: Fluent read back as Basic). The board carries a small "example run" label.
Finish: done means rendered at 1440, 768 and 390, `check` run, a fresh reviewer's P0/P1 findings fixed, and every example listed in the report.

Verdicts (wins / close / declined, then the one discipline raised):
- Generative Art Computational Color Chord Counterpoint: declined. Discipline: every state is labelled beyond colour, a remark is always its word.
- Hypercard Stack Shoebox: declined. Discipline: everything is addressable: each carrier links to its system's page, each section has its anchor.
- Kaiju Broadcast Alert: close (clearer on the refusal, not recognised by the audience). Discipline: escalation is reserved: routine remarks stay calm, only REFUSED gets the full red frame, once, in the Gate closed section.
- Deco Cinema Lobby: declined. Discipline: arrival staged as thresholds: board, check-in, gate, carriers, rules, boarding, each a step tighter toward the action.
