# 0011. Reading a form over HTTP

## Context

`read` launches a browser and waits on the data directory lock (ADR 0004) to list fields that some systems publish without one: Greenhouse's Job Board API returns a posting's questions (`?questions=true`), and Ashby's job board page loads its form from an unauthenticated GraphQL endpoint. Neither can submit: the documented submit APIs take the employer's key.

## Decision

- An adapter may implement `ReadsOverHttp`: `read_api(url)`, that lists the fields over HTTP without a browser. It returns `None` when it cannot: a host it does not recognise (a custom-domain career page), an error, or a response it does not understand. An adapter without one says why in its doc.
- `read` calls `read_api` first and falls back to the page read when it returns `None`. `fill` and `send` always read the page they fill.
- `read_api` returns the same field ids as `read_form`, because answers are keyed by them (ADR 0005). An attribute the API does not carry (a length limit, accepted file types) is `null`; the `fill` read-back catches what it would have caught.
- The adapter gate (ADR 0013) reads its ready postings both ways and compares the fields.

## Consequences

`read` on a known host takes a request instead of a browser launch and runs where no browser or display is available. An endpoint that is not documented (Ashby's) can change without notice; the fallback keeps `read` working, and the next adapter gate run shows the drift. Between runs, a drifted endpoint can list the wrong fields; `fill` reads the page, so the send is not affected.
