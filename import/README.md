# import — step 2 of the import

Turns the one merged CSV into one YAML file per project in `projects/`, which
from then on is the system of record.

**Not built yet.** `./import/import.sh profile` and `./import/import.sh convert` still run the
old xlsx importer against a single spreadsheet. SPEC §3 is the design, §3.4 the
new-field/new-value rules and §5.4.2 the bootstrap rule. What follows
describes the target; the walkthrough at the bottom works today only as far as
the merge.

Step 1 is [`merge/`](../merge/README.md), which is where the extracts go and
where they are reduced to one file.

```
import/
├── merged.csv           # the input. Copied here BY HAND from merge/merged.csv
├── mapping.yaml         # hand-edited: column header -> field path
├── value_map.yaml       # hand-edited: messy value -> canonical value
├── id_map.csv           # generated: source key -> project id, stable
├── profile.md           # generated: the scan report
├── proposals.md         # generated: schema edits for a human (§3.4)
└── example-extract.csv  # demo data - see the walkthrough below
```

`merged.csv` is the only input, and nothing here ever reads the extracts: by
this point there is one file in one known dialect, and whether it came from one
source or five is not visible and does not matter.

## The two commands

```bash
./import/import.sh profile     # scan merged.csv -> profile.md, and starter mapping files
# READ profile.md, then edit mapping.yaml and value_map.yaml
./import/import.sh convert     # -> projects/*.yaml
./check.sh              # validate what came out
```

The edit between them is the manual step, and the only one. `profile` proposes;
you decide. See SPEC §3.3 for why the proposal is a guess worth checking — a
column of short repeated values might be a vocabulary or might just be a small
sample of an identifier, and nothing in the data tells them apart.

## What is hand-maintained here, and what is not

| file | |
|---|---|
| `mapping.yaml`, `value_map.yaml` | **yours.** Tracked in git. They are the whole manual step |
| `id_map.csv` | generated, but **tracked** — it is what keeps project ids stable across re-imports |
| `merged.csv`, `profile.*`, `proposals.md` | derived, ignored |

`.gitignore` says the same thing beside the files. Losing `mapping.yaml` means
redoing the mapping; losing `id_map.csv` means every project gets a new id and
every `depends_on` breaks.

## It runs once

`convert` is a **bootstrap**, and it refuses a populated `projects/`. After the
first run that directory is the system of record (SPEC §2) and the extracts are
history: teams hand-edit their YAML and submit merge requests, and a project
that turns up later arrives that way — not from another dump of a legacy system
that is being switched off.

```
error: projects/ already holds 412 project file(s).

  The import is a bootstrap. Once projects/ exists it is the system of record
  (SPEC 2) and the extracts are history - a new project arrives as YAML from
  the team that owns it, not from another dump.

  To redo the bootstrap, clear it first. git is how you get it back:

      rm projects/*.yaml && ./import/import.sh convert <extract>
      git checkout -- projects/          # if that was a mistake
```

Two commands rather than a `--force`: the destructive one should be the one you
typed on purpose, and git is already the undo.

**There is no merge-into-existing mode** (SPEC §5.4.2). It was designed and
dropped — nothing needs it, and a `convert` whose result depended on what was
already on disk would be the one thing in this pipeline that is not a pure
function of its inputs.

To see what an extract *would* say without touching anything, convert it
elsewhere and diff. **The path has to be inside the repo** — everything runs in
a container with the repo bind-mounted, so `/tmp/anything` is written inside the
container and is gone when it exits:

```bash
./import/import.sh convert --out out/import-preview
diff -r projects/ out/import-preview
```

Unchanged files are not rewritten, so `git status` shows only real changes.

## When the CSV has something the schema does not

Two cases, treated differently on purpose (SPEC §3.4):

| what is new | what happens |
|---|---|
| a **value**, in a column already mapped to a field with `x-taxonomy` | added to that group in `taxonomy.yaml` automatically, with `label_de: null` |
| a **column** with no field | lands in `_unmapped` verbatim; a paste-ready schema stanza goes in `proposals.md` |

Adding the value is safe: the convention for an unlabelled code is already live
— `./check.sh --check-schema` warns about every null label, and every output
prints the bare code rather than an invented word. Adding a *field* is not,
because `x-tracked` moves every team's coverage percentage and `x-column` adds a
column to the report and the Excel sheet, and neither of those is in the data.

Nothing is lost by waiting. `_unmapped` is exempt from
`additionalProperties: false`, so the values sit in the project files and
validate cleanly until somebody decides what the field is.

## Walkthrough, with demo data

`example-extract.csv` is a synthetic legacy extract — 20 projects, generated
from `merge/make_testdata.py` and converted to CSV. It is deliberately messy in
the ways real extracts are: a title row and a blank line above the header,
cp1252 encoding, `;` delimiter, `muc-01` and `MUC-01` in one column, `800 GB`
beside bare integers, and two date formats in another.

It is committed, unlike a real extract: it is invented data, so it does not
touch SPEC §12.8.

```bash
# 1. put the extract where step 1 reads
cp import/example-extract.csv merge/input/

# 2. merge. With one source there is nothing to resolve, but you get the
#    merged.csv, the empty conflicts file and the manifest
./merge/merge.sh --key "Projekt-Nr"

# 3. read the result, then do what its last line tells you
cp merge/merged.csv import/merged.csv

# 4. scan it, edit the mapping, convert          <- NOT BUILT YET
./import/import.sh profile
./import/import.sh convert
./check.sh
```

To see the merge actually do its job, copy the extract in twice under different
names and change a few cells in one of them — the second file loses every
contest it enters, and `merge/merge_conflicts.csv` lists exactly what it lost.

**`merge/input/` is not cleaned up for you.** Take the extract out again when
you are done, or the next real merge will include it.
