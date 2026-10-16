# 0003. One adapter per hiring system

## Context

Hiring systems differ in the details that decide whether a send works: a combobox that saves only on Enter, a submit button that moves between mouse down and mouse up in a short window, a cookie banner that comes back over the consent boxes. The rules about when a send is allowed do not differ.

## Decision

An adapter is one module per hiring system with five steps the core calls in order, and an optional `read_api(url)` that lists the fields without a browser (ADR 0011):

| Step | Does |
|---|---|
| `detect(url, page)` | says whether the form belongs to its system, from the host, else from the page (a Teamtailor footer, a Greenhouse embed) |
| `read_form(page)` | lists the fields: id, label, type, required, options, length limit, accepted files |
| `fill(page, answers)` | types the answers through the system's quirks |
| `read_back(page)` | reads what the system will receive, from the server's response where it has one, else from the form state |
| `submit(page)` | clicks submit and returns the evidence of the send |

The core owns everything that is not specific to a system: the browser, matching the read-back against the answers, refusing, the double-send guard and the statuses. An adapter only knows its system's page.

Adapters are built in order of how often their system shows up: Ashby, Teamtailor and Greenhouse, then Welcome to the Jungle, then the long tail one system at a time.

## Consequences

When a system changes its page, an adapter breaks where an agent would adapt. The read-back gate turns most breakage into a refusal rather than a wrong application, but where the read-back comes from the same page the adapter filled, a selector that hits the wrong input reads back what it wrote. Adapters that have a server-side read-back (Ashby) are stronger than those that read the page.
