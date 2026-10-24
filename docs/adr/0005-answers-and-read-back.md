# 0005. Answers and read-back

## Decision

Fields are identified by a stable `id` from the form (the input's `name`, the system's question id). Labels are for display: they differ in punctuation, apostrophes and language between a form's renderings, and repeat ("Please specify").

`fill` and `send` take one JSON map `{field_id: value}` from the answers file. Its value is typed by the field:

- `text`, `phone`, `choice`, `typeahead`: a string, for a typeahead the text to type;
- `url`: an absolute `http(s)` URL;
- `date`: a string, `YYYY-MM-DD`, whatever the page displays;
- `multi_choice`: a list of strings;
- `file`: an absolute path.

A field missing from the map is left as the page has it, empty or prefilled by an account. A required field missing from the map refuses the command, unless the account prefilled it (`prefilled` in `read`).

An answer the form's fields already rule out refuses the command before any typing (`invalid_answers`): an unknown field id, a value of the wrong shape, a choice not among the options, a text longer than the field's length limit, a `url` that is not an absolute URL, a file that is not there.

A field is `url` only where its hiring system declares a URL (a question type, an `input type=url`). A label such as "LinkedIn Profile" on a plain text field stays `text`: whether it wants a URL or a handle is the answer's author's reading, not a rule atsfill adds to the form.

The read-back is compared to the answers by field type:

| Type | Matches when |
|---|---|
| `text`, `url` | equal after trimming whitespace and Unicode normalization |
| `date` | equal |
| `choice`, `multi_choice` | each value equals one of the field's options |
| `typeahead` | the picked suggestion starts with the answer's text, or holds its comma-separated parts in order ("Paris" or "Paris, France" for "Paris, Île-de-France, France") |
| `file` | the base names are equal |
| `phone` | the digits and the country are equal |

The response lists each mismatch with its field id and read-back; with `-v`, every field with its answer, its read-back and `match`.
