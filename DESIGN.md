---
provenance: decided
modes:
  "/": persuade
  "/getting-started/, /telemetry/, /adapters/*, /adr/*": read
colors:
  board: "#0e1013"          # the lit sign: board, gate-closed and boarding sections
  board-raised: "#16191d"   # the board's panel
  flap: "#1c1f24"           # a split-flap cell
  flap-split: "#08090b"     # the line across a cell's middle
  flap-text: "#f1ede3"      # text on the board
  board-muted: "#a9a59b"    # secondary text on the board, 7.7:1 on board
  board-rule: "#2a2e35"
  hall: "#eeebe3"           # the light sections, read at length
  hall-raised: "#e4e0d5"
  ink: "#16181c"
  ink-muted: "#4a4d52"      # 7.1:1 on hall
  hall-rule: "#cbc6b9"
  gate: "#ffc61a"           # the one accent: the action, the copy button, gate labels
  gate-ink: "#14110a"
  ok: "#5fd38d"             # READY, SUBMITTED
  no: "#ff6b5b"             # REFUSED, on the board
  wait: "#f5a524"           # BLOCKED, UNKNOWN
typography:
  sign: { family: "Overpass", weights: [700, 800], tracking: "-0.005em display, +0.02em wordmark, +0.08em caps labels" }
  read: { family: "Atkinson Hyperlegible Next", size: "1.0625rem", leading: 1.55 }
  board: { family: "Overpass Mono", weight: 700, features: "tabular-nums" }
  scale: ["0.875rem", "1.0625rem", "1.25rem", "clamp(1.5rem, 1.2rem + 1vw, 2rem)", "clamp(2rem, 1.4rem + 2.2vw, 3.25rem)", "clamp(2.25rem, 1.3rem + 3.4vw, 4.5rem)"]
rounded:
  flap: "3px"
  panel: "6px"
spacing:
  scale: ["0.25rem", "0.5rem", "0.75rem", "1rem", "1.5rem", "2rem", "3rem", "clamp(3.5rem, 2rem + 5vw, 7.5rem)"]
  container: "76rem"
components:
  flap-cell:
    background: "{colors.flap}"
    text: "{colors.flap-text}"
    radius: "{rounded.flap}"
  gate-line:
    background: "{colors.board}"
    marker: "{colors.gate}"
    radius: "{rounded.panel}"
  copy-button:
    background: "{colors.gate}"
    text: "{colors.gate-ink}"
    min-height: "44px"
  sign-plate:
    background: "{colors.ink}"
    text: "{colors.gate}"
    radius: "{rounded.flap}"
---

## Overview

The docs site of a CLI that applies to jobs for an agent. The job hunt is a departures hall: each posting a flight, each hiring system a carrier, each result a remark on a split-flap board, the send a gate that closes on a wrong answer. The bold move is the board itself, played on load, under a runway where a plane takes off on a loop; around it, the hall stays quiet, its lists rising in as they scroll.

## Colors

Two surfaces: the lit board (dark) and the hall (warm light). Yellow is signage and belongs to the gate only: the action, its copy button, gate and sign labels. Remarks are coloured by status but always carry their word. Red escalates once, the gate-closed frame; nowhere else. Rejected: the dark-glow dev-tool palette, and a job-site blue.

## Typography

Overpass, from highway signage, speaks for signs and headings; Atkinson Hyperlegible Next for everything read; Overpass Mono only on flap cells, commands and JSON. Fonts are self-hosted in `docs/assets/fonts/` (OFL): no request to a font CDN. Rejected on a render: Archivo / Public Sans / Red Hat Mono.

## Layout

One container (76rem) with a fluid gutter; sections alternate board and hall so no two neighbours share a shape. Running text stays under 64ch.

## Elevation & Depth

On the board, depth is lightness: board, then panel, then cell. The hall is flat, separated by rules, never cards.

## Shapes

Small radii: 3px for cells and plates, 6px for panels. Rules are 1px; a 2px ink rule opens a list (steps, carriers).

## Components

The runway (one plane, one inline SVG symbol, rolling and lifting off in CSS; the same plane leaves a SUBMITTED row and marks the Departures sign), flap cells (two leaves, a split line, CSS rotateX through a character drum), gate lines (the command, a yellow marker, a copy button), sign plates (yellow on ink, the rules' terms and the route's stops), the route (four stops on one line, You, the agent, atsfill, the hiring system, with a packet riding each leg while its line in the list below is current; still, the list alone), the carrier wall (each system's mark in a white tile, generated from the registry by `scripts.backlog`).

## Do's and Don'ts

- Yellow fills only the copy buttons and marks gate and sign labels.
- A remark is always its word; colour is second.
- Example data on the board is labelled as an example run.
- A carrier's mark is its own, unedited, shown only to say it is supported.
- Docs pages keep Zensical's frame; the theme only sets type, palette and the header's yellow rule.
