# scigantic-cards

A JSON format for describing a dataset to an LLM agent, a linter for it, and a command that drafts a card from a folder or bucket.

A card holds what the agent is unlikely to know already: which published dataset this is, how this copy differs from it, how large it is, and code that opens it. A card that only restates what the model already knows fails the lint.

## Install

```
pip install scigantic-cards
```

Python 3.10 or newer. The core has no dependencies. Extras:

| Extra | Adds | For |
|---|---|---|
| `headers` | [scigantic-headers](https://github.com/Scigantic/scigantic-headers) | decoding file headers (MRC, NIfTI, Parquet, FCS, FASTQ, VCF, DICOM and others) |
| `remote` | fsspec | listing buckets |
| `s3`, `gcs` | fsspec and s3fs or gcsfs | S3 and GCS buckets |

```
pip install 'scigantic-cards[headers,s3]'
```

## Draft a card from a folder

`init` measures what it can: file counts, sizes, folder layout, extensions, CSV and TSV columns (types inferred from the first rows), and decoded file headers. It writes access snippets that find the files.

It cannot know what the dataset is or how it differs from the published version, so `identity` and the difference lists stay empty for you, or an agent, to fill in.

```
$ scigantic-cards init ~/data -o card.json
fill in: identity, and differs_from_upstream or gotchas (a script cannot know these)

$ scigantic-cards lint card.json
card.json: error: identity: No identity. Name the canonical dataset, release, or paper so the agent can recall what it already knows.
card.json: error: delta: No differences or gotchas. A card with neither only restates what the agent already knows.
card.json: warn: unverified-access: 'List files': snippet was not executed.
card.json: warn: unverified-access: 'Read one file header': snippet was not executed.
card.json: warn: checked-at: checked_at is missing or not an ISO date (YYYY-MM-DD).
```

`~` and `~/data` both work. The generated snippet calls `os.path.expanduser` on the location you gave, so the card stays portable between machines:

```python
import glob, os
root = os.path.expanduser('~/data')
files = sorted(glob.glob(os.path.join(root, '**', '*.csv'), recursive=True))
assert files, 'no files matched'
print(len(files), files[:3])
```

Edit `card.json` to add `identity` and at least one entry in `differs_from_upstream` or `gotchas`. Then run the snippets to record that they work:

```
$ scigantic-cards verify card.json --run --write
passed: List files
passed: Read one file header

$ scigantic-cards lint card.json
$ echo $?
0
```

### What init reads, and what it does not

- Hidden files and folders are skipped unless you pass `--hidden`.
- Symlinks are not followed, and only regular files are opened, so a FIFO cannot hang it.
- The listing stops at `--max-files` (default 5000) or 60 seconds, and does not descend more than `--max-depth` folders (default 16). When it stops early or skips deeper folders, the layout says the counts are lower bounds and `approx_bytes` and `approx_items` are left empty rather than reporting a partial number.
- Raw file bytes are never embedded unless you pass `--embed-bytes N` (at most 4096). Even then, only files that a header decoder recognizes are read, so a `.env` or a key file in the same folder is listed but not read.
- Unreadable folders are counted and reported in the layout.

## Draft a card from a bucket

```
$ scigantic-cards init s3://noaa-ghcn-pds/parquet/by_year/YEAR=2020/ELEMENT=TMAX --max-files 20 -o card.json
```

S3 is read anonymously and GCS with `token='anon'`. For anything else, call `draft_card(url, **storage_options)` from Python and pass fsspec options. http and https locations cannot be listed.

Excerpt of the result from that run (2026-10-11):

```
layout   = 10 files, 14.9 MiB. Extensions: .parquet (10).
location = s3://noaa-ghcn-pds/parquet/by_year/YEAR=2020/ELEMENT=TMAX
fields   = ID BYTE_ARRAY, DATE BYTE_ARRAY, DATA_VALUE INT64, M_FLAG, Q_FLAG, S_FLAG, OBS_TIME
samples  = Parquet, 500000 rows, 7 columns
```

The Parquet schema was read from the footer of an object in the bucket, not by downloading the file. Two sampled files with identical columns are listed once.

## File headers

With the `headers` extra installed, sampled files that [scigantic-headers](https://github.com/Scigantic/scigantic-headers) can decode get a `samples` entry with the decoded fields. Gzipped files work, and formats that keep their schema in a footer are read from the end of the file.

```json
{
  "path": "runs/r1/reads.fastq",
  "size": 73,
  "format": "fastq",
  "summary": "Illumina reads, first read 10 bp",
  "fields": {"platform": "Illumina", "instrument": "A00123", "flowcellId": "HXXXXDSXX", "lane": 1, "firstReadLength": 10}
}
```

Without the extra, CSV and TSV columns are still read and other files are only counted.

To keep the first bytes of those files in the card, base64 encoded:

```
$ scigantic-cards init ~/data/runs/r1/reads.fastq --embed-bytes 24
```

gives `"head_b64": "QEEwMDEyMzo0NTpIWFhYWERTWFg6MTox"`, which decodes to `@A00123:45:HXXXXDSXX:1:1`.

## Pass a card around as base64

`encode` turns a card into one URL-safe string, for an environment variable, an HTTP header, or a query parameter. `--compress` deflates it first and prefixes `z:`.

```
$ export CARD=$(scigantic-cards encode card.json --compress)
$ scigantic-cards decode "$CARD" | head -3
{
  "name": "data",
  "identity": "Example sequencing run summaries (fictional)",
```

In Python: `card.to_b64()`, `Card.from_b64(text)`. Whitespace and missing padding are tolerated. Decoding is bounded: encoded input over 8 MB or decoded output over 5 MB is rejected, so a small compressed payload cannot expand into memory.

## Verify the snippets

`verify` runs each Python snippet in a subprocess and sets `verified` to whether it exited 0. It sets `checked_at` to today if any snippet ran. Other languages are skipped.

`verify` executes code from the card, so it requires `--run`. Only run it on cards you trust. A snippet that times out (default 60 seconds, `--timeout`) is killed along with the processes it started in its process group, and helpers it leaves running after it exits are stopped too. A child that deliberately starts its own session is not killed, but `verify` stops waiting for it at the timeout. The generated snippets assert that their glob matched files, so a path that has moved fails instead of passing quietly.

`verified: true` means the snippet ran without error on the machine that ran it. It does not mean the output is correct.

## Format

A card is a JSON object. Only `name` is required for the file to load. Duplicate keys and strings with lone surrogates are rejected. The linter checks the rest. Unknown keys are rejected.

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Short id for the card |
| `identity` | string | The published dataset, release or paper this copy comes from |
| `summary` | string | One or two sentences |
| `layout` | string | How files are organized |
| `location` | string | Where the data is: a path, a mount point, or a URL |
| `approx_bytes`, `approx_items` | integer or null | Size, for choosing between streaming and loading |
| `fields` | list of `{name, dtype, note, group}` | Columns or variables. `group` is the table or file a field belongs to |
| `differs_from_upstream` | list of strings | How this copy differs from the well-known version |
| `gotchas` | list of strings | Sentinel values, unit conventions, known bad files |
| `access` | list of `{goal, snippet, verified, language}` | Code that opens the data. `language` defaults to `python` |
| `samples` | list of `{path, size, format, summary, fields, head_b64}` | Example files. `fields` holds decoded header values. `head_b64` is optional |
| `checked_at` | string | ISO date the card was last checked against the data |

See [`examples/acme-stations.json`](examples/acme-stations.json), a fictional dataset. The same rules are published as JSON Schema in `src/scigantic_cards/card.schema.json`, for validating cards from other languages.

## Lint

```
scigantic-cards lint card.json [--strict] [--budget N]
```

`--strict` counts warnings as failures. `--budget` sets the size limit in bytes (default 4000).

Exit codes: 0 no problems, 1 lint errors (or warnings with `--strict`, or a failed snippet in `verify`), 2 a file could not be read or is not a valid card.

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

The default budget is a guess, not a measured value. Cards that carry many samples or long snippets will exceed it. Raise it with `--budget` if that is what you want.

## Python

```python
from scigantic_cards import Card, CardError, draft_card, lint, verify_card

card = draft_card("~/data")                 # or "s3://bucket/prefix"
card.identity = "Example sequencing run summaries"
card.gotchas.append("results.csv and results.tsv.gz use different separators.")

for issue in lint(card):
    print(issue.severity, issue.rule, issue.message)

card, results = verify_card(card, timeout=60)   # runs the snippets

try:
    Card.from_json(open("card.json", encoding="utf-8").read())
except CardError as e:
    print(e)  # for example: card.fields[2].dtype: required
```

`Card.from_dict`, `Card.from_json` and `Card.from_b64` accept the same documents as the JSON Schema and raise `CardError` with the path of the bad value otherwise.

## Tests

```
pip install -e ".[dev]"
pytest
```

The suite includes property-based tests (Hypothesis). They generate valid and slightly broken cards and check that the Python loader and the JSON Schema agree on every one, and they build random folder trees to check that `init` always produces a loadable card whose byte totals match the files on disk.

## License

MIT-0.
