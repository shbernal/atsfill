# 0011. Request read

## Context

`read` launches a browser and waits on the data directory lock (ADR 0004) to list fields that some systems serve without one: Greenhouse's Job Board API returns a posting's questions (`?questions=true`), and Ashby's job board page loads its form from an unauthenticated GraphQL endpoint. Neither can submit: the documented submit APIs take the employer's key.

## Decision

- An adapter may implement `ReadsByRequest`: `request_read(url)`, a request read that lists the fields with plain HTTP requests, without a browser, from a public API, an undocumented API, hydration data or server-rendered HTML. It returns `None` when it cannot: a host it does not recognise (a custom-domain career page), an error, or a response it does not understand. An adapter without one says why in its doc.
- `read` calls `request_read` first and falls back to the browser read when it returns `None`. `fill` and `send` always use the browser read of the form they fill.
- `request_read` returns the same field ids as `browser_read`, because answers are keyed by them (ADR 0005). An attribute the source does not carry (a length limit, accepted file types) is `null`; the `fill` read-back catches what it would have caught.
- The adapter gate (ADR 0013) reads its ready postings both ways and compares the fields.

## Consequences

`read` on a known host takes a request instead of a browser launch and runs where no browser or display is available. An undocumented API (Ashby's) can change without notice; the fallback keeps `read` working, and the next adapter gate run shows the drift. Between runs, a drifted source can list the wrong fields; `fill` uses the browser read, so the send is not affected.
