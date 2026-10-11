# scigantic-cards

A JSON format for describing a dataset to an LLM agent, and a linter for it.

A card holds what the agent is unlikely to know already: which published dataset this is, how this copy differs from it, how large it is, and a snippet that opens it. A card that only restates what the model already knows fails the lint.

## Install

```
pip install scigantic-cards
```

Python 3.10 or newer. No dependencies.

## Format

A card is a JSON object. Only `name` is required for the file to load. The linter checks the rest.

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Short id for the card |
| `identity` | string | The published dataset, release or paper this copy comes from |
| `summary` | string | One or two sentences |
| `layout` | string | How files are organized |
| `approx_bytes`, `approx_items` | integer or null | Size, for choosing between streaming and loading |
| `fields` | list of `{name, dtype, note, group}` | Columns or variables. `group` is the table or file a field belongs to, optional |
| `differs_from_upstream` | list of strings | How this copy differs from the well-known version |
| `gotchas` | list of strings | Sentinel values, unit conventions, known bad files |
| `access` | list of `{goal, snippet, verified}` | Code that opens the data. `verified` is true if you ran it |
| `checked_at` | string | ISO date the card was last checked against the data |

Unknown keys are rejected. See [`examples/acme-stations.json`](examples/acme-stations.json), a fictional dataset.

The same rules are published as JSON Schema in `src/scigantic_cards/card.schema.json`, for validating cards from other languages.

## Lint

```
$ scigantic-cards lint examples/needs-work.json
examples/needs-work.json: error: identity: No identity. Name the canonical dataset, release, or paper so the agent can recall what it already knows.
examples/needs-work.json: error: delta: No differences or gotchas. A card with neither only restates what the agent already knows.
examples/needs-work.json: warn: size: No size. Agents choose between streaming and loading based on it.
examples/needs-work.json: warn: unverified-access: 'List files': snippet was not executed.
examples/needs-work.json: error: tilde-path: 'List files': a path starting with ~/ is not expanded by Python unless it goes through os.path.expanduser or Path.expanduser.
examples/needs-work.json: warn: checked-at: checked_at is missing or not an ISO date (YYYY-MM-DD).
```

Options: `--strict` counts warnings as failures. `--budget N` sets the size limit in bytes (default 4000).

Exit codes: 0 no problems, 1 lint errors (or warnings with `--strict`), 2 a file could not be read or is not a valid card.

### Rules

| Rule | Severity | Fires when |
|---|---|---|
| `name` | error | name is blank |
| `identity` | error | identity is blank |
| `delta` | error | `differs_from_upstream` and `gotchas` have no non-blank entry |
| `access` | error | there are no access snippets |
| `empty-snippet` | error | a snippet is blank |
| `tilde-path` | error | a snippet has a string starting with `~/` and no `expanduser` |
| `size` | warn | neither size field is set |
| `unverified-access` | warn | a snippet has `verified: false` |
| `field-dtype` | warn | a field has no dtype |
| `field-duplicate` | warn | the same name appears twice in the same group |
| `checked-at` | warn | `checked_at` is not an ISO date |
| `budget` | warn | the card is over the budget as compact UTF-8 JSON |

## Python

```python
from scigantic_cards import Card, CardError, lint

try:
    card = Card.from_json(open("card.json", encoding="utf-8").read())
except CardError as e:
    print(e)  # for example: card.fields[2].dtype: required
else:
    for issue in lint(card):
        print(issue.severity, issue.rule, issue.message)
```

`Card.from_dict` and `Card.from_json` accept the same documents as the JSON Schema and raise `CardError` with the path of the bad value otherwise.

## Tests

```
pip install -e ".[dev]"
pytest
```

The suite includes property-based tests (Hypothesis) that generate valid and slightly broken cards and check that the Python loader and the JSON Schema agree on every one.

## License

MIT-0.
