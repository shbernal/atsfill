# 0005. Answers and read-back

## Decision

Fields are identified by a stable `id` from the form (the input's `name`, the system's question id). Labels are for display: they differ in punctuation, apostrophes and language between a form's renderings, and repeat ("Please specify").

`fill` and `send` take one JSON map `{field_id: value}` from the answers file. Its value is typed by the field:

- `text`, `phone`, `choice`, `typeahead`: a string, for a typeahead the text to type;
- `date`: a string, `YYYY-MM-DD`, whatever the page displays;
- `multi_choice`: a list of strings;
- `file`: an absolute path.

A field missing from the map is left as the page has it, empty or prefilled by an account. A required field missing from the map refuses the command, unless the account prefilled it (`prefilled` in `read`).

The read-back is compared to the answers by field type:

| Type | Matches when |
|---|---|
| `text` | equal after trimming whitespace and Unicode normalization |
| `date` | equal |
| `choice`, `multi_choice` | each value equals one of the field's options |
| `typeahead` | the picked suggestion starts with the answer's text, or holds its comma-separated parts in order ("Paris" or "Paris, France" for "Paris, Île-de-France, France") |
| `file` | the base names are equal |
| `phone` | the digits and the country are equal |

The response lists each mismatch with its field id and read-back; with `-v`, every field with its answer, its read-back and `match`.
