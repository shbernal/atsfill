# 0005. Answers and read-back

## Decision

Fields are identified by a stable `id` from the form (the input's `name`, the system's question id). Labels are for display: they differ in punctuation, apostrophes and language between a form's renderings, and repeat ("Please specify").

`fill` and `send` take one JSON map `{field_id: value}` from the answers file. Its value is typed by the field:

- `text`, `phone`, `choice`, `typeahead`: a string, for a typeahead the text to type;
- `url`: an absolute `http(s)` URL;
- `date`: a string, `YYYY-MM-DD`, whatever the page displays;
- `multi_choice`: a list of strings, an empty list clearing the field; refused where the field is required;
- `file`: an absolute path.

A field missing from the map is left as the page has it, empty or prefilled by an account. A required field missing from the map refuses the command, unless the account prefilled it (`prefilled` in `read`, the profile's value). A value the page itself starts the field with, its `default`, answers nothing: the user gives the real answer.

An answer the form's fields already rule out refuses the command before any typing (`invalid_answers`): an unknown field id, a value of the wrong shape, a choice not among the options, a text longer than the field's length limit, a `url` that is not an absolute URL, a file that is not there.

A field's `knockouts` are the options whose choice ends the application, where its hiring system declares them. A knockout answer is never refused, as it can be the truthful one: the fill goes on and lists it under `warnings`. An adapter lists knockouts only where its system says which option knocks out, never from a label or a question type.

A field is `url` only where its hiring system declares a URL (a question type, an `input type=url`). A label such as "LinkedIn Profile" on a plain text field stays `text`: whether it wants a URL or a handle is the answer's author's reading, not a rule atsfill adds to the form.

A follow-up is a field the form shows only once other answers meet its condition, `shown_when`; `required_when` holds where an answer makes a field required. A condition is a tree: `Is(field_id, values, negated)` holds when the field's answer is one of `values` (one of them ticked, for a `multi_choice`), compared exactly, or without `values` when the field is answered; `AnyOf` and `AllOf` combine conditions. Against values, an `Is` never holds for an unanswered field, negated or not; an adapter whose system shows a follow-up on an empty answer says so with `AnyOf`. A condition reads a field's answer, else its `prefilled`, else its `default`; a follow-up the answers keep hidden counts as unanswered. `required` is the system's own flag, required whenever the field shows, never an adapter's opinion; labels are the page's, the condition is only in `shown_when`.

A dependent list always shows; only its options follow other answers, a state list under a country. It is not a follow-up. Its `option_sets` are keyed by the same condition tree and read by the same evaluator: an answer is checked against the first set whose `when` holds, else against `options`, the options under any parent answer no set covers; `options` left `None` there means unknown, and the read-back decides. An adapter lists the sets it has, or can get without a request per parent value. Otherwise it keys the set it read, under the parent's answer before any, to that value and leaves `options=None` for the rest. Labels stay the page's own.

The core decides from the answers alone, before typing: a required follow-up they show and leave unanswered refuses (`invalid_answers`), and so does an answer to a follow-up they keep hidden. An adapter's conditions name listed fields, never in a loop, and a dependent list's sets never follow its own answer, else the command ends `adapter_error`.

An alternative is a field that takes the same thing as others in a different shape, a document as a file or as a text: each keeps its system's own id, and the group shares a key, `one_of`. The group is required when its members carry the system's `required` flag; a member never is on its own. A required group with no member answered nor prefilled refuses (`no answer for required <key>`), and so does more than one member answered, required or not (`invalid_answers`). The answers decide which side goes, never a preference in the config. A group of one member, or members disagreeing on `required`, ends the command `adapter_error`. A required field atsfill cannot fill stops the form; an alternative it cannot fill stops it only when none of its group can be filled.

`read` lists every field, follow-ups included. An adapter whose form can show a field after an answer implements `ListsShownFields`: once filled, the ids of the fields the page shows. A field it did not list ends the fill `unhandled_rendering`, to be fixed in the adapter; a follow-up the page shows though its condition is unmet ends it `mismatch`: its condition was read wrong. An adapter whose source lists every field whatever the answers (an API) need not implement it, and says so in its doc.

The read-back is compared to the answers by field type:

| Type | Matches when |
|---|---|
| `text`, `url` | equal after trimming whitespace and Unicode normalization |
| `date` | equal |
| `choice`, `multi_choice` | each value equals one of the field's options; an empty `multi_choice` answer, when the field reads back nothing |
| `typeahead` | the picked suggestion starts with the answer's text, or holds its comma-separated parts in order ("Paris" or "Paris, France" for "Paris, Île-de-France, France") |
| `file` | the base names are equal |
| `phone` | the digits and the country are equal |

The read-back holds only the fields the page shows. A follow-up the answers keep hidden that reads back a value is a mismatch. The response lists each mismatch with its field id and read-back; with `-v`, every field with its answer, its read-back and `match`.
