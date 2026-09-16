# merge — step 1 of the import

Reduces the CSV extracts in `input/` to one `merged.csv`, one row per project,
resolving the places where they contradict each other.

Everything it needs is here: the entry point, the tool, its tests, its fixtures
and this file. `SPEC.md` §3.1 is the design and §5.4 is the merge contract — the
rules below are binding there, not decided here.

```
merge/
├── input/                  # put the extracts here. Every *.csv is a source
│   └── 01-projekte.csv
├── merged.csv              # the result, written one level up
├── merge_conflicts.csv
├── merge_manifest.json
├── merge.sh                # the entry point
├── merge_csv.py            # the tool, stdlib only
└── tests/
```

```bash
./merge/merge.sh --key "Projekt-Nr"
```

```
$ ./merge/merge.sh --key "Projekt-Nr"
join key : 'Projekt-Nr'
sources  : sorted filename order is the precedence, first wins
  1. 01-projekte.csv                 20 rows   [cp1252, delimiter ';']

merged   : 20 rows in -> 20 rows out, 18 columns
conflicts: 0 value(s) discarded -> merge/merge_conflicts.csv
written  : merge/merged.csv

Inspect merge/merged.csv, then copy it to import/merged.csv:
    cp merge/merged.csv import/merged.csv
```

## What is a source

**Every `*.csv` in `input/`.** That is the whole rule, with no exception to
remember, and it is why the sources have a directory to themselves: the results
are written one level up, so nothing but extracts is ever in there. Drop a new
file in and the next run includes it.

| where | |
|---|---|
| `input/*.csv` | sources. Sorted filename order is the precedence |
| `merged.csv` | **output** — one row per project |
| `merge_conflicts.csv` | **output** — every value discarded |
| `merge_manifest.json` | **output** — the sources, their sha256 and rank |

The tool still refuses to read a file by one of those three output names, which
in this layout can never happen — it is there for a `--sources` pointed at the
results by mistake, where `merged.csv` would otherwise be merged into itself and
win every contest by sorting first. `tests/fixtures/input/merged.csv` exists so
a test proves it does not.

Numeric prefixes are worth using because `ls input/` then shows the precedence:

```
input/01-sap-export.csv      # wins every contest
input/02-cmdb-dump.csv
input/03-team-umfrage.csv    # fills gaps only
```

Renaming a file therefore changes the data. That is the cost of deriving
precedence from the directory listing, and it is why the resolved order is
printed on every run and recorded in `merge_manifest.json`.

## The join key

`--key` names the column that identifies a project. It is **required**: no
default and no auto-detection, because a wrong guess produces a plausible file
with the wrong number of rows, which is the one failure this step must not have.

It is also how the header row is found — the first row containing that column
*is* the header row, so a title row and a blank line above it are skipped, and
"no header found" becomes the error the file actually has: the key column is
missing, named with the file that lacks it.

- Keys match **trimmed and casefolded**, so `P-1001`, `p-1001` and `␣P-1001␣`
  are one project. Every merge that only happened because of that is reported —
  it is an assumption, and a visible one.
- **A blank key never matches another blank key.** Rows with no key stay
  separate rows and are counted loudly. Collapsing them would invent a project
  holding the merged remains of everything nobody keyed.
- Rows sharing a key merge whether they are in one file or several. Within one
  file, earlier rows win.

## First wins, per cell

| rule | why |
|---|---|
| merge per **cell**, not per row | no file wins a whole project; each column comes from the best source that has it |
| first **non-empty** wins | an empty cell is "nobody answered", not a contradiction, so a blank in `01-` is filled from `02-` |
| values merged **verbatim** | the merge never normalises or maps — `MUC-01` and `muc-01` are a reported conflict, and `value_map.yaml` decides in step 2 whether they mean the same thing |
| the key column never conflicts | normalising it is the merge's one licensed normalisation, so every difference it could log is already in the casefold report |
| surrounding whitespace is trimmed | `16 ` against `16` is not a disagreement, and logging it as one buries the ones that are |

Everything discarded goes to `merge_conflicts.csv`, one row per discarded value:

```csv
key;column;winning_value;winning_file;discarded_value;discarded_file
P-1001;CPU (Kerne);16;01-sap-export.csv;32;02-cmdb-dump.csv
```

**That file is the only record of what was thrown away.** `merged.csv` holds the
winners and step 2 cannot see past it, so an empty conflicts file means the
sources agreed and a large one is a measurement worth reading — it is what
answers whether whole-file precedence is good enough (SPEC §12.6).

It cannot rank its own findings: a contradiction about who owns a project and
one in a free-text remark look identical here, because the merge knows column
headers and not fields. Sorting by what matters needs the mapping, which is
step 2's.

## The handoff is a manual copy

Nothing reaches `import/`. The last line of output is a command to paste, and
until it is run, step 2 keeps reading whatever it read before.

That is the inspection gate. A merge whose result is only visible as 400 YAML
files is a merge nobody checks, and one that copies itself onward is a pause
rather than a gate. `--destination` changes the path that is *named*; it is
still not written.

## The file it writes

`merged.csv` is written for a German Excel reader: `;`-delimited, UTF-8 **with**
BOM, CRLF. Without the BOM every umlaut renders wrong and the reviewer distrusts
the file for the wrong reason. Columns are the union of all sources in
first-seen order by precedence, so the highest-ranked source keeps its familiar
layout and later-only columns are appended rather than interleaved.

It is **byte-identical for identical input** — fixed column order, rows sorted
by join key, one fixed dialect, no clock read anywhere — like the report model,
the workbook and the PDF.

It is an **artifact, not an input**. Editing it is overwritten by the next run,
and re-saving it from Excel damages it in the ways SPEC §9 lists. Corrections
belong in the source extract, or in `import/value_map.yaml`.

## Per-file differences are dealt with here and only here

Encoding (`utf-8-sig`, `cp1252`, `latin-1`), delimiter, quoting dialect, header
row, header text. `merged.csv` comes out in one known dialect, so no later step
sniffs anything.

Column names are expected to be identical across sources — **verify that, do not
assume it.** Trailing whitespace, case, and a BOM on the first header all make
"identical" names unequal. A near-miss is a loud warning naming both files, and
the two stay separate columns; that is allowed to happen, but not silently.

## Test data

`make_testdata.py` generates synthetic extracts into `input/`. It lives here
because extracts are this step's input, not the importer's — SPEC §10 has it
writing several overlapping CSVs, which is what makes the merge worth running.

```bash
./shell.sh python3 merge/make_testdata.py
```

It writes **three** files, not one, because one source gives the merge nothing
to do. They overlap on most projects, disagree on 33 cells, leave gaps each
other fills, spell one key in the wrong case and include a row with no key at
all - plus two encodings and two delimiters between them. Running the merge
over them and reading `merge_conflicts.csv` is the fastest way to see what this
step is for.

Deterministic: the same seed gives the same three files, so regenerating
produces no diff.

## Tests

```bash
./shell.sh python3 -m pytest merge/tests -q      # or ./verify.sh, which runs them
```

`tests/fixtures/input/` holds three sources that deliberately overlap,
contradict on three cells, fill each other's gaps, spell one key in the wrong
case, carry two rows with no key at all, disagree about encoding and delimiter,
and include a header differing from another's by a single space.
`tests/fixtures/no_key_column/` is the failure case.

## It is stdlib-only

Nothing imported here is outside the standard library, and that is load-bearing:
the tests run in the *pipeline* image, which carries `ruamel.yaml` and
deliberately no PyYAML, so anything `merge_csv.py` imports has to be importable
there. It is why `merge_manifest.json` is JSON while
`projects/_import_manifest.yaml`, written by step 2 in the import image, is YAML.
