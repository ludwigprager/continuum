# legacy-xlsx-import — SUPERSEDED

> **This describes the xlsx importer, which is being replaced.** The source data
> is several overlapping CSV extracts, not one spreadsheet, so there is no xlsx
> import any more: see SPEC §3 for the two-step CSV design and §5.4 for the
> merge contract, and the *Importing* section of the top-level `README.md` for
> how to run it.
>
> The file is kept only while `import/import_xlsx.py` is still in the tree, and
> goes with it. What stays true and is worth reading before rewriting it: the
> reasoning below on reviewing distinct values rather than rows, and the German
> number and date traps, which are CSV problems too (SPEC §9).

Turns a legacy "one row per project" spreadsheet into one YAML file per project,
without needing the target schema to be known up front.

Dependencies: `openpyxl`, `PyYAML`. Nothing else, no network access at any point.

```bash
pip install openpyxl PyYAML
```

## The three steps

```bash
# 1. Look at what is actually in the sheet. Writes profile.md plus starter
#    mapping.yaml and value_map.yaml. Touches nothing else.
python import_xlsx.py profile projekte.xlsx --out import/

# 2. Read import/profile.md. Edit import/mapping.yaml and import/value_map.yaml.
#    This is the manual part, and it is the only manual part.

# 3. Write the project files.
python import_xlsx.py convert projekte.xlsx --config import/ --out projects/

# 4. Optional: a JSON Schema derived from what the data actually contains.
python import_xlsx.py derive-schema projekte.xlsx --config import/ \
    --out schema/project.schema.json
```

## Why you review distinct values, not rows

The profile step lists every distinct value per column. A 900-row sheet typically
has 15 to 30 columns and a few hundred distinct values across all of them.
Reviewing those takes an hour or two. Reviewing 900 rows takes days. That
difference is the entire point of the tool.

## mapping.yaml

One line per spreadsheet column:

```yaml
columns:
  "Standort RZ":        placement.datacenter
  "Datenbank":          datastores[].engine
  "Storage Klasse":     platform.storage_classes[]
  "CPU (Kerne)":        platform.cpu_cores
  "Bemerkung":          null
```

| Path syntax | Result |
|---|---|
| `a.b.c` | nested scalar: `a: {b: {c: value}}` |
| `a.b[]` | list of scalars: `a: {b: [v1, v2]}` |
| `a.b[].c` | list of objects: `a: {b: [{c: v1}, {c: v2}]}` |
| `a.b[0].c` | explicit index |
| `null` | leave the column in `_unmapped` |

**Nothing is ever dropped.** Any column you do not map lands in `_unmapped` in
every project file, verbatim:

```yaml
_unmapped:
  Bemerkung: "Migration 2023 gestoppt, Lizenzthema"
  Ansprechpartner: "M. Huber"
```

Map the 15 columns you understand today, let the other 30 ride along, and promote
them into the real schema as you work out what they mean.
`import-report.md` ranks the unmapped columns by how many values they hold, so
you can see which ones are worth promoting next.

Several columns can feed one list. `datastores[].engine` and
`datastores[].version` are zipped by token position, so a row with
`"DB2; Redis"` and `"11.5; 7.0"` produces two complete entries. If the token
counts differ, the shorter is padded with null and the case is listed in the
report.

## value_map.yaml

Keyed by the *target path*, not the column header, so renaming a header later
does not invalidate it.

```yaml
values:
  datastores[].engine:
    "MS-SQL": mssql
    "MSSQL": mssql
    "MSSQL/Redis": [mssql, redis]   # one cell becomes two values
    "keine": null                   # treated as not set
```

The generator does three things for you:

- **Collapses spelling variants automatically.** `MS-SQL`, `MSSQL` and `mssql`
  are clustered and all proposed as the most frequent spelling. You only correct
  what it got wrong.
- **Proposes `null`** for values that mean "nothing here" (`keine`, `unbekannt`,
  `offen`, `k.A.`).
- **Flags near-misses it will not merge on its own**, as comments, so you decide.

Values you do not list pass through unchanged. That is deliberate: silent
normalisation is worse than visible raw data.

## What the tool decides, and where it asks

| Detected automatically | How to override |
|---|---|
| Header row, including sheets that start with a title block | `--header-row N` |
| Two-row merged headers (joined with `/`, upper row forward-filled) | `--header-rows 2` |
| Column type: integer, number, date, enum, list, free text | edit `mapping.yaml` |
| Units in headers (`Storage (GB)` → `storage_gb`) | edit the field name |
| Multi-value separators (`;` `\|` newline `,` `/`) | edit `value_map.yaml` |
| Id column and grouping column | `--id-column`, `--group-column` |
| CSV encoding (utf-8, cp1252, latin-1) and delimiter | `--encoding` |

**The one thing it cannot decide for you** is `2.500`. That is 2500 in a German
sheet and 2.5 in an English one, and a single cell carries no evidence either
way. The tool decides per column from the other values in it, and when a column
gives no evidence it says so loudly in `profile.md` and defaults to German.
Force it with `--decimal de` or `--decimal en`.

## Re-importing after the sheet changes

The spreadsheet will keep being edited for a few weeks after you start. Run the
import onto a dedicated branch each time:

```bash
git checkout -b import/$(date +%F)
python import_xlsx.py convert projekte.xlsx --config import/ --out projects/
git add -A && git commit -m "re-import $(date +%F)"
git checkout main && git merge import/$(date +%F)
```

Three things make this work:

- **Stable ids.** `import/id_map.csv` remembers which source row became which
  project id, so ids survive rows being added, deleted or reordered. Keep it in
  git.
- **Unchanged files are not rewritten at all.** Change one cell in the sheet and
  exactly one file changes. `git status` shows real changes, not 800 timestamps.
  Volatile provenance (file checksum, import date) lives in
  `projects/_import_manifest.yaml`, not in every project file.
- **Review state survives.** `_meta.last_reviewed` and `_meta.reviewed_by` are
  carried over on re-import. If the underlying data changed,
  `_meta.confidence` drops back to `imported`, because a verification of the old
  values no longer means anything.

Fields a human edited by hand show up as normal git merge conflicts. That is the
intended behaviour: those are the ones that need a decision.

## Confidence is not coverage

Every imported field is written as `confidence: imported`, which means nobody has
looked at it. An import will give you 90% field coverage that means almost
nothing.

Report **verified coverage** separately from raw coverage. The gap between the
two is the real remaining work, and it is the more honest number to put in front
of management.

## Known limits

- One `[]` level per path. Nested lists of lists are not supported and are
  unlikely to be what a flat spreadsheet actually means.
- Formula cells are read from the values Excel cached. If the file was last
  written by a tool that does not cache them, they read as empty. Open and
  re-save the file in Excel or LibreOffice first.
- `.xls` (the old binary format) is not supported. Save as `.xlsx`.
- Free-text columns with fewer than ~40 distinct values are classified as enums.
  Harmless, but you will see them offered in `value_map.yaml`; delete those
  blocks.
