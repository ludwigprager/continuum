# SPEC: legacy-to-cloud-native project catalogue & reporting pipeline

You are picking up a project mid-build.

**Done: M1 (validation), M2 (tables + report model), M3 (Excel), M4
(TXT + PNG) and M5 (PDF + PPTX).** `./check.sh` and `./report.sh` work end
to end against `projects/` with no network access, under podman or docker,
and produce all five formats: `report.xlsx`, `report.pdf`, `deck.pptx`,
`report.txt` and the chart PNGs. What remains is the offline bundle (M6).
See 10 for the state of each.

**The import has been reopened and is the current work.** The source data is
several overlapping CSV extracts, not one spreadsheet: 3 is the new design and
5.4 is the merge contract. Nothing downstream of `projects/` changes.

Read this whole document before writing code. The **Contracts** and **Do not**
sections are the parts that will cost the most to get wrong.

---

## 1. What this system is for

A large organisation is migrating several hundred projects from legacy
infrastructure to cloud native. Each project is owned by a different team. A
central group assesses all of them and reports status **daily** to management.

- The set of data fields collected is a **moving target**. It changes weekly
  during the assessment.
- Data must be **versioned** and any past report must be reproducible.
- Everything runs in **containers**.
- The production environment is **air-gapped**. Container images can be pulled
  on demand there, or prepared outside and carried in. Build-time network access
  is available outside the air gap; runtime network access inside must be
  assumed to be zero.
- Reports are needed in **xlsx, pdf, pptx, png, txt**. Excel is the one managers
  actually open, because they want to slice the data themselves.
- Primary audience is German-speaking. Source data is German. Output labels must
  support German and English.

## 2. Decisions already made (do not relitigate)

| Decision | Reason |
|---|---|
| **Git is the system of record.** One YAML file per project, flat in `projects/`. | Versioning, blame and review for free. Teams submit merge requests for their own files. The owning team is `ownership.team_id` inside the file, not the path: teams merge, split and get renamed throughout a migration, and a directory layout turns each of those into a repo reorg where the path and the field can disagree. See the note below on CODEOWNERS. |
| **Teams hand-edit YAML.** No web UI in v1. | The schema is still moving; a form built now would drift within weeks. A UI is a client of Git, added later if a specific group is provably blocked. |
| **A derived analytical layer is rebuilt on every run**, never hand-maintained. | DuckDB over jsonl tables in `out/tables/`. Git holds truth and git is the history. **Revised:** this was originally Parquet snapshots holding a time series. There are no trend charts and none are wanted, which was Parquet's only justification here - at ~400 projects the catalogue is about a thousand rows, where columnar storage buys nothing measurable (330 KB/day against 388 KB for jsonl). jsonl is readable, diffable, and one concept fewer. Nothing is carried between runs. |
| **One report model feeds all five renderers.** | Five renderers each querying the data independently produce five subtly different numbers. |
| **Charts are rendered once as PNG** and embedded in PDF, PPTX and XLSX. | Same reason. |
| **`unknown` is a legal value everywhere; incomplete never fails validation.** | If a team cannot commit a half-filled file they will keep the data in their own spreadsheet and you will never see it. |
| **No booleans in the data.** Use `full` / `partial` / `none` / `unknown`. | A boolean cannot distinguish "false" from "nobody has asked yet", and `yes`/`no` are YAML 1.1 booleans (see Gotchas). |
| **No computed scores stored in YAML.** Readiness scores, risk scores, counts are computed in the report builder. | They go stale the instant a field is edited. |

**On CODEOWNERS.** GitLab and GitHub match ownership rules against *paths*,
never against file contents, so a flat layout cannot express "team-beta owns
its own projects" with a wildcard. If per-team review is to be enforced,
generate `CODEOWNERS` from `ownership.team_id` - one exact path per project -
and have CI fail when it is out of date. That keeps the YAML as the single
source of truth instead of duplicating ownership into the directory tree,
where the two can silently disagree.

## 3. The import

`import_csv.py` — converts the legacy CSV extracts into per-project YAML. It is
the **bootstrap**: it runs to populate `projects/`, after which the YAML is the
system of record (§2) and the CSVs are history. Dependencies: `PyYAML` and the
standard library — `csv` is in it. No network access.

**There is no xlsx import.** The legacy extracts arrive as CSV. `openpyxl`
leaves `Dockerfile.import` with it, which is one fewer dependency to carry into
the air gap, and `merge/make_testdata.py` generates CSVs.

### 3.1 Two steps, and a file you can look at

The import is **two jobs, not one**, and the boundary between them is a real
file on disk. They are two entry points for the reason §6.5 gives for all of
them — one script per job, so `ls` shows what can be run — and because they are
run at different times by different people: the merge is re-run whenever an
extract is re-cut, the conversion only once its result has been read.

```bash
./merge/merge.sh --key "Projekt-Nr"    # step 1, in merge/: *.csv -> merged.csv
cp merge/merged.csv import/merged.csv  #   by hand, after reading the result
./import/import.sh profile                    # step 2, in import/: -> profile.md
./import/import.sh convert                    # step 2, in import/: -> projects/
```

`./merge/merge.sh` takes **no filenames**: `merge/input/` is the input, and
sorted filename order within it is the precedence (§5.4). `--key` is its only required
argument.

**Step 1 — merge, in `merge/`.** All source CSVs are read together and reduced
to one CSV, row-per-project, written to `merge/merged.csv`. This is the step
that resolves contradictions, and §5.4 is its contract.

`merge/` is **self-contained**: the entry point, the tool, its tests, its
fixtures and its own `README.md` live there. It is the one component that needs
nothing from the schema (§3.4), so it is the one that can be kept, tested and
read on its own — and it writes nothing into `import/`.

**The extracts go in `merge/input/`, and every `*.csv` there is a source.** The
results are written one level up, beside the tool, so the directory that is read
holds nothing but extracts: no name to remember, no prefix convention, no
exception to the rule. Adding a source is dropping a file in.

**The handoff is a manual copy.** `merge/merged.csv` becomes `import/merged.csv`
by a `cp` that a person runs, printed as the last line of the merge's output.
That is the inspection gate: until it is run, step 2 keeps reading whatever it
read before, so re-cutting an extract cannot change the catalogue behind
anyone's back. A merge that copies itself onward is a pause, not a gate.

**Step 2 — convert, in `import/`.** Everything downstream reads that single
copied file. `profile` scans it, `convert` turns it into YAML. Neither knows
there was ever more than one source.

**A directory each, because they are two jobs.** `merge/` holds the extracts,
the code that merges them and everything derived from them; `import/` holds the
one file step 2 reads, beside the mapping a human edits. It also means `import/`
is never scanned for sources, which is what lets `id_map.csv` sit at its root
without being mistaken for one.

The split exists because the merge is where data gets **discarded** — by design,
but irreversibly as far as the YAML is concerned. A merge whose result is only
visible as 400 YAML files is a merge nobody checks. One CSV, one row per
project, opened in whatever the reviewer already uses, is a result somebody
will actually read before it becomes the system of record.

It also makes §3.2 literal rather than conceptual: the "union" that profiling
wants is now a file, not an idea, and `profile` has one ordinary CSV to scan.

```
merge/                        # step 1: self-contained, see merge/README.md
├── merge.sh                  # the entry point
├── merge_csv.py              # the tool - stdlib only
├── README.md                 # how to run it and what it decides
├── tests/                    # its tests and fixtures
├── 01-sap-export.csv         # sources: EVERY *.csv here except the three
├── 02-cmdb-dump.csv          #   outputs below. Sorted order is precedence
├── 03-team-umfrage.csv
├── merged.csv                # GENERATED - the thing to inspect
├── merge_conflicts.csv       # GENERATED - every value the merge discarded
└── merge_manifest.json       # GENERATED - the sources, for step 2 (§5.4.1)

import/                       # step 2 works here
├── README.md                 # how to run it, with a demo walkthrough
├── merged.csv                # COPIED BY HAND from merge/merged.csv
├── mapping.yaml              # hand-edited: column header -> field path
├── value_map.yaml            # hand-edited: messy value -> canonical value
├── id_map.csv                # generated: source key -> project id, stable
├── profile.md                # generated: the scan report
└── proposals.md              # generated: schema edits for a human (§3.4)
```

The tool still refuses to read a file named like one of its three outputs,
which the layout above makes impossible — it is there for a `--sources` pointed
at the results by mistake, where `merged.csv` would be merged into itself and
win every contest by sorting first. A fixture exists so a test proves it is not.

**Each directory ignores its own output**, in a `.gitignore` of its own rather
than in the root one, so the rule sits beside the files it describes and can say
which of them are hand-edited and must stay tracked. Both anchor their patterns
with a leading slash: unanchored, `merged.csv` matches at any depth and
swallows the fixture that proves the merge does not read its own output back.
The two files are also what make the directories exist on a clean checkout —
git does not track an empty directory, and the merge on a fresh clone would
otherwise fail with `no source CSVs in merge/input/` before it could say more
useful.

### 3.1.1 The join key

`merge` **requires** `--key`, naming the column header that identifies a
project. There is no default and no auto-detection: getting it wrong silently
produces a plausible file with the wrong number of rows, which is the one
failure this step must not have.

- The key column must exist in **every** source. A file missing it is an error
  naming that file, not a file quietly skipped.
- Keys are matched after trimming whitespace and casefolding, so `P-1001`,
  `p-1001` and ` P-1001 ` are one project. Every merge that only happened
  because of that normalisation is listed in the report — it is an assumption,
  and a visible one.
- **A blank key never matches another blank key.** Rows with no key are carried
  through as individual rows, counted loudly in the summary, and never collapsed
  into one project. Collapsing them would silently invent a project holding the
  merged remains of everything nobody keyed.
- Rows sharing a key are merged whether they are in the same file or different
  files. Within one file, earlier rows win.

The key, the resolved file order and the row and column counts are printed at
the end of the run and recorded in the manifest, so the result is reproducible
from the record without knowing what was typed.

### 3.2 Per-file reading happens in step 1, and only there

Files genuinely differ in encoding (`utf-8-sig`, `cp1252`, `latin-1`),
delimiter, quoting dialect, header row and header text. All of that is dealt
with by `merge`, and `merged.csv` is written in one known dialect — so no
later step ever sniffs anything.

Column names are expected to be identical across sources. **Verify that; do not
assume it.** Trailing whitespace, case, and a BOM on the first header all make
"identical" names unequal, and two columns differing by one space become two
columns in the merged file that never merge. A near-miss is a loud warning
naming both files, not a silently separate column.

`merged.csv` carries the **union** of all columns, in first-seen order by
source precedence, so the primary source keeps its familiar layout and columns
that only a later file has are appended rather than interleaved.

Profiling that union — rather than any single file — is what makes four things
better:

- The enum threshold stops depending on file size. "Fewer than 40 distinct
  values" asked of 20 rows is a different question than asked of 1,200.
- The ambiguous decimal separator is often resolvable. A column showing only
  `2.500` is undecidable (§9); one unambiguous `2.500,75` anywhere in that
  column settles it for the whole column.
- Fill rate becomes the *merged* fill rate. A column 20% filled in each of five
  files may be 85% filled after merging, and that is the number that decides
  whether a field is worth promoting.
- Type disagreement surfaces instead of being decided by whichever file was
  read. `16` in one file and `16 Kerne` in another is a finding, not a coin toss.

**Write `merged.csv` for the reader, not for the parser.** The audience is
German-speaking (§1) and will open it in Excel: `;` as the delimiter, UTF-8
**with** BOM, `\r\n` line endings. Without the BOM, Excel renders every umlaut
wrong and the reviewer distrusts the file for the wrong reason. Step 2 reads it
back through the same `utf-8-sig` path as any other source, so nothing downstream
notices.

**It is an artifact, not an input.** Editing `merged.csv` by hand is
overwritten by the next `merge`, and worse, a re-save from Excel damages it in
the ways §9 lists. Corrections belong in the source extract, or in
`value_map.yaml` if the source cannot be fixed.

Like every other derived file in this repository, `merged.csv` is
**byte-identical for identical inputs**: fixed column order, rows sorted by
join key, one fixed dialect, and no clock reading anywhere in it. Two extracts
that changed nothing produce no diff.

### 3.3 Distinguishing a vocabulary from an identifier

Whether a column holds a closed set of codes or free values is guessed from the
data and then confirmed by a human in `mapping.yaml`. The guess is never
load-bearing on its own.

The useful test is **saturation**: a vocabulary has a fixed size that more rows
do not change, an identifier grows with the rows.

```
column               100 rows      1,200 rows     verdict
security_class       4 distinct    4 distinct     plateau -> vocabulary
project_name         98 distinct   1,180 distinct grows   -> identifier
```

Measure it across the union by distinct count at 25 / 50 / 100% of rows, so it
works whether or not the files differ in size. This is strictly better than a
flat distinct-count threshold, which classifies a project-name column as an
enum whenever the sample is small enough.

It still does not decide the business question. A datacenter column and a
security-class column both plateau; one belongs in `sites.yaml` as a reference
list and the other in `taxonomy.yaml` as a code group, and nothing in the data
tells them apart. The scan **proposes**; the schema decides (§6.1.1).

### 3.4 New fields and new values

A CSV will contain things the schema has never seen. The two cases are not
equally safe and are not treated alike.

| what is new | what happens |
|---|---|
| **a value**, in a column already mapped to a field carrying `x-taxonomy` | added to that group in `taxonomy.yaml` automatically, as `label_de: null, label_en: null`, and appended to `order:` before `unknown` |
| **a column** the schema has no field for | the data lands in `_unmapped` verbatim; a ready-to-paste schema stanza goes in `import/proposals.md` for a human to apply |

Adding the value is safe because the convention for an unlabelled code already
exists and is already live: `--check-schema` warns about every null label, and
every output prints the bare code rather than an invented word (§12.1). The
importer is not inventing anything; it is recording that the code occurred.

Adding a *field* is not safe, because a field needs a `$ref` and the
`x-tracked` / `x-column` decisions, and those are not in the data. `x-tracked`
moves every team's coverage percentage; `x-column` adds a column to the report
model and the Excel sheet. Nothing is lost by waiting: `_unmapped` is exempt
from `additionalProperties: false`, so the values sit in the project files,
validate cleanly, and are promoted by one edit whenever somebody decides what
the field is (§6.1.2).

**A new value in a column that is not taxonomy-backed is not a taxonomy event.**
Free text has no vocabulary to extend.

### 3.5 What it guarantees, and what you can rely on

- Every project file has `schema_version`, `id`, and a `_meta` block.
- Unmapped columns land in `_unmapped` verbatim. The validator must allow
  `_unmapped` with arbitrary content.
- `_meta.confidence` is one of `verified | estimated | imported | unknown`.
  Everything an import produces is `imported`, meaning **nobody has looked at
  it** — however many files agreed (§5.4).
- Ids are stable via `import/id_map.csv`, and `_meta.last_reviewed` /
  `reviewed_by` survive a re-import.
- `projects/_import_manifest.yaml` holds the volatile provenance: every source
  file with its sha256 and its precedence rank, and the import date. Do not
  treat it as a project file — skip any file starting with `_`.

**Do not modify `import_csv.py`** unless a bug is found. If you do, keep
`merge/make_testdata.py` passing.

## 4. Repository layout to create

```
.
├── merge/                       # step 1, self-contained (§3.1)
│   ├── merge.sh                 # entry point - not at the root, it lives here
│   ├── merge_csv.py             # the tool, stdlib only
│   ├── make_testdata.py         # generates synthetic extracts into input/
│   ├── README.md                # how to run it and what it decides
│   ├── .gitignore               # ignores its three outputs, anchored
│   ├── tests/test_merge.py      # its tests
│   ├── tests/fixtures/          # its fixtures
│   ├── input/                   # SOURCES: every *.csv here, and nothing
│   │   ├── .gitignore           #   else is in here. Ignores nothing; keeps
│   │   ├── 01-sap-export.csv    #   the directory in git. Sorted order is
│   │   └── 02-cmdb-dump.csv     #   precedence (§5.4)
│   ├── merged.csv               # GENERATED - inspect this
│   ├── merge_conflicts.csv      # GENERATED - what was discarded
│   └── merge_manifest.json      # GENERATED - the sources, for step 2
├── import/                      # step 2, self-contained like merge/
│   ├── import.sh                # entry point - lives here, not at the root
│   ├── import_csv.py            # BUILD - replaces import_xlsx.py (§3)
│   ├── README.md                # how to run it, with a demo walkthrough
│   ├── .gitignore               # ignores merged.csv and the reports
│   ├── merged.csv               # COPIED BY HAND from merge/merged.csv
│   ├── mapping.yaml             # hand-edited: column header -> field path
│   ├── value_map.yaml           # hand-edited: messy value -> canonical value
│   ├── id_map.csv               # generated: source key -> project id
│   ├── profile.md               # generated: the scan report
│   ├── proposals.md             # generated: schema edits for a human (§3.4)
│   └── example-extract.csv      # synthetic demo data, tracked. Not an input:
│                                #   the walkthrough copies it to merge/input/
├── projects/                    # system of record, one YAML per project, flat
│   ├── payment-gateway.yaml
│   ├── kundenportal.yaml
│   └── _import_manifest.yaml
├── schema/
│   ├── project.schema.yaml      # YAML, not JSON: it is hand-edited and takes
│   │                            # comments. The recipe for adding a field is
│   │                            # at the top of the file. (.json also loads.)
│   ├── taxonomy.yaml            # enum codes -> labels (de/en), order, colour
│   ├── sites.yaml               # datacenter codes
│   └── teams.yaml               # team ids, contacts
├── reports/
│   ├── daily.yaml               # which fields/sections appear in the daily report
│   └── definitions.yaml         # the counting rules (see §7)
├── tools/
│   ├── validate.py              # BUILD
│   ├── snapshot.py              # BUILD - YAML -> jsonl tables
│   ├── build_model.py           # BUILD - DuckDB -> report_model.json
│   ├── make_workbook_template.py # BUILD - the pre-built pivots, run rarely
│   └── render/
│       ├── charts.py            # BUILD - PNG, run first, others embed its output
│       ├── xlsx.py              # BUILD
│       ├── pptx.py              # BUILD
│       ├── pdf.py               # BUILD - Typst
│       └── txt.py               # BUILD
├── templates/
│   ├── report.typ               # Typst template
│   ├── deck.potx                # PowerPoint template (placeholder until supplied)
│   ├── workbook.xlsx            # Excel template holding the pre-built pivots
│   └── report.txt.j2            # Jinja2
├── docker/
│   ├── Dockerfile.pipeline
│   └── Dockerfile.import
├── out/                         # gitignored
│   ├── tables/*.jsonl           # rebuilt every run, nothing carried over
│   └── reports/<date>/{report.xlsx,report.pdf,deck.pptx,*.png,report.txt,report_model.json,manifest.json}
├── tests/
│   ├── fixtures/projects/       # ~12 hand-written YAML files covering edge cases
│   ├── fixtures/invalid/<case>/ # one directory per failure mode, each with an
│   │                            # expect.txt naming the finding codes it must
│   │                            # report. A new case is a directory, not code.
│   └── test_*.py
├── check.sh                     # entry points, one per job
├── snapshot.sh
├── report.sh
├── verify.sh
├── shell.sh
├── scripts/
│   ├── lib.sh                   # container plumbing, the only place that
│   │                            # knows about podman/docker
│   └── precommit.sh
├── docker-compose.yml
└── README.md
```

## 5. Contracts

These are the interfaces between components. Get them right and the pieces can be
built and tested independently.

### 5.1 Project YAML (input)

Minimum guaranteed shape. Everything else is optional and moving.

```yaml
schema_version: 1
id: payment-gateway
name: Payment Gateway
ownership:
  team_id: team-alpha
classification:
  classification_a: CA-2       # -> taxonomy.yaml
  classification_b: CB-3
  security_class: S3
placement:
  environments:                # may be a single `datacenter` scalar in v1 data
    - name: prod
      datacenter: muc-01
      os: { family: windows, version: "2016" }
platform:
  cpu_cores: 16
  storage_gb: 500
  storage_classes: [block, object]
datastores:
  - engine: db2
    version: "11.5"
migration:
  strategy: replatform         # rehost|replatform|refactor|repurchase|retire|retain|relocate
  status: assessed             # not_assessed|assessed|planned|in_progress|migrated|verified|rolled_back
  blockers:
    - { id: BLK-014, type: licensing, severity: high, status: open }
integration:
  depends_on: [customer-master]
_unmapped: { }
_meta:
  confidence: imported
  last_reviewed: null
```

**Write the code so that every field except `id` and `schema_version` may be
missing.** Missing renders as `unknown` / `n/a`, never as a crash and never as
zero.

### 5.2 `report_model.json` (the central artifact)

This is the only thing renderers read. It contains every number, label and table
that will appear in any output, already aggregated. Renderers are dumb: they lay
out, they do not compute.

```jsonc
{
  "generated_at": "2026-09-14T04:00:00+02:00",
  "as_of_date": "2026-09-14",
  "provenance": {
    "git_sha": "a1b2c3d",
    "git_tag": "snapshot/2026-09-14",
    "schema_version": 1,
    "pipeline_version": "0.3.1",
    "image_digest": "sha256:...",
    "project_count": 412
  },
  "coverage": {
    "projects_total": 412,
    "fields_tracked": 38,
    "field_coverage_pct": 71.2,
    "verified_coverage_pct": 23.8,      // confidence == verified
    "by_team": [ { "team_id": "team-alpha", "projects": 34,
                   "coverage_pct": 88.0, "verified_pct": 40.0 } ]
  },
  "headline": [                          // the numbers on slide 2 / the txt summary
    { "key": "projects_total",   "label_de": "Projekte gesamt", "label_en": "Projects", "value": 412 },
    { "key": "migrated",         "label_de": "Migriert",        "label_en": "Migrated", "value": 47 }
  ],
  "tables": {
    "by_engine": {
      "title_de": "Projekte je Datenbank", "title_en": "Projects by datastore",
      "definition": "projects_with_engine",         // key into definitions.yaml
      "columns": [ {"key":"engine","label_de":"Engine","label_en":"Engine"},
                   {"key":"projects","label_de":"Projekte","label_en":"Projects"} ],
      "rows": [ {"engine":"db2","projects":118}, {"engine":"unknown","projects":61} ]
    },
    "site_x_os": { "...": "cross-tab, same shape" }
  },
  "charts": [
    { "key": "by_engine", "path": "by_engine.png", "width_px": 1600, "height_px": 900,
      "title_de": "...", "title_en": "..." }
  ],
  "flat": {                                // what goes on the Excel data sheets
    "projects":     [ { "id": "...", "...": "..." } ],
    "datastores":   [ { "project_id": "...", "engine": "db2" } ],
    "environments": [ { "project_id": "...", "datacenter": "muc-01", "os_family": "windows" } ],
    "blockers":     [ ],
    "dependencies": [ { "from": "payment-gateway", "to": "customer-master" } ]
  },
  "definitions": [
    { "key": "projects_with_engine",
      "text_de": "Projekte mit mindestens einem Datastore mit engine=X, alle Umgebungen.",
      "text_en": "Projects having at least one datastore with engine=X, all environments." }
  ]
}
```

Rules:
- Keys are stable; labels are looked up from `taxonomy.yaml` at build time and
  baked in. Renderers never touch the taxonomy.
- Every table names the `definition` it was counted under.
- `unknown` is an explicit row in every distribution, never silently dropped.
- The model must be **byte-identical** for the same input. Sort every list, use a
  fixed float format, no `datetime.now()` outside `generated_at`.

### 5.3 CLI contract for every tool

```
merge/merge_csv.py    --key HEADER [--sources DIR] [--out FILE]
                      [--destination import/merged.csv]
                      # step 1. No file arguments: every *.csv in --sources
                      # (default merge/input/) is a source, sorted order is
                      # precedence (§5.4). Results go to its PARENT.
                      # --key is required. --destination is NAMED in the
                      # printed instruction, never written.
tools/import_csv.py   profile|convert [--merged import/merged.csv] [--out DIR]
                      [--config import/]
                      # step 2. Reads the one published CSV, never the sources.
tools/validate.py     [--projects DIR] [--schema DIR] [--format text|json] [--strict]
tools/snapshot.py     [--projects DIR] [--schema DIR] [--out out/tables/] [--as-of DATE]
tools/build_model.py  [--snapshot out/tables/] [--spec reports/daily.yaml] [--schema DIR]
                      --out out/reports/<date>/report_model.json [--as-of DATE]
                      [--generated-at ISO]
tools/render/*.py     --model MODEL.json --out DIR [--lang de|en]
```

Exit codes: `0` ok, `1` validation errors (invalid data), `2` tool/usage error.
Warnings never change the exit code unless `--strict`.

### 5.4 The merge — first wins

Several CSVs describe overlapping, redundant sets of projects. They will
disagree. How that is resolved decides what every later number says, so it is a
contract and not an implementation detail.

The merge is step 1 (§3.1). Its input is every source CSV in `merge/input/`;
its output is `merge/merged.csv`, one row per join key, plus
`merge/merge_conflicts.csv`. Handing the result to step 2 is a manual copy to
`import/merged.csv`.

**Precedence is sorted filename order.** The first file to supply a value for a
cell wins. `ls merge/input/` therefore shows the precedence, which is why sources
are worth naming with a numeric prefix:

```
merge/input/01-sap-export.csv   # wins every contest
merge/input/02-cmdb-dump.csv
merge/input/03-team-umfrage.csv # fills gaps only
```

The resolved order is printed at the start of every run and recorded in the
manifest, so a result is reproducible from the record without knowing what was
typed. A file whose name does not sort where it should is a rename, and a
rename changes the data — which is the cost of this choice, and the reason the
order is echoed rather than assumed.

**The rules, in order of application:**

1. **Merge at cell grain, not row grain.** A project present in three files
   produces one row, each column filled from the highest-precedence source that
   has a value for it. No file wins the whole row.
2. **Empty is not a contradiction.** First *non-empty* wins. A blank in `01-` is
   filled from `02-`. This follows §5.1: a missing column, an empty cell and the
   string `unknown` all mean nobody has answered, none is an error, and none
   counts toward coverage. Only two real values can conflict.
3. **Every conflict is recorded.** See below. Silently discarding half a dump is
   how two systems disagree for six months without anyone noticing — the same
   rule as §11's "do not silently drop `unknown`", applied upstream.
4. **Confidence does not rise with agreement.** Three files agreeing is three
   exports of the same legacy database, not a human having checked. Everything
   an import writes is `imported` (§11).
5. **Values are merged verbatim.** The merge does not normalise, coerce, split
   or map anything — that is `value_map.yaml`'s job in step 2. `MUC-01` and
   `muc-01` are two different values here and the second is a discarded
   conflict, which is correct: the merge reports the disagreement and the value
   map decides they are the same thing.

   Two exceptions, both narrow. Surrounding whitespace is trimmed from every
   cell, because `16 ` and `16` are not a disagreement about anything and
   logging them as one would bury the disagreements that are real. And the join
   key is normalised per §3.1.1, because without that there is nothing to merge
   *on* — so the key column never produces a conflict of its own: every
   difference it could show is exactly what the casefold-merge report already
   names, and logging it twice would drown the rest.

### 5.4.1 `merge_conflicts.csv`

A CSV rather than prose, because the useful question is "which columns disagree
most", and that is a sort. Same dialect as `merged.csv` — `;`, BOM, CRLF — for
the same reason: it is opened by the same reader, in the same spreadsheet.

```csv
key;column;winning_value;winning_file;discarded_value;discarded_file
P-1001;CPU (Kerne);16;01-sap-export.csv;32;02-cmdb-dump.csv
P-1001;Standort RZ;MUC-01;01-sap-export.csv;muc-01;02-cmdb-dump.csv
```

One row per discarded value, not per contested cell: a cell contested by three
files produces two rows. Sorted by key then column then discarded file, so it
diffs cleanly between runs.

An empty conflicts file means the sources agreed everywhere they overlapped.
A large one is not a failure — it is the measurement that answers §12.6.

**This file is the only record of what was discarded.** `merged.csv` holds the
winners and nothing else, and step 2 cannot see past it. That is the price of
having an inspectable intermediate, and it is the right trade, but it means
per-field provenance does not reach the project YAML: `_meta` records the source
*set* and the manifest records each file's sha256, not which file supplied
`cpu_cores`. That manifest is `merge/merge_manifest.json`, written by step 1
because step 2 cannot see the sources to describe them — JSON rather than YAML
because the merge is stdlib-only, so that it stays importable in the pipeline
image where the tests run, which carries `ruamel.yaml` and no PyYAML. It holds
no timestamp: it is part of what must be byte-identical between two runs, and
the import date is stamped by step 2 (§3.5). If per-field provenance is ever needed in the YAML, it comes from
joining `merge_conflicts.csv` back on the key, not from a column smuggled into
`merged.csv`.

### 5.4.2 Step 2 is a bootstrap and runs once

`convert` populates `projects/`. After that, `projects/` **is** the system of
record (§2) and the extracts are history: teams hand-edit their YAML and submit
merge requests, and a project that appears later arrives that way, not from
another dump of a legacy system that is being switched off.

So `convert` **refuses a populated `projects/`**, naming the count and saying
how to redo the bootstrap deliberately:

```bash
rm projects/*.yaml && ./import/import.sh convert <extract>
git checkout -- projects/          # if that was a mistake
```

Two commands rather than a `--force`, because the destructive one should be the
one you typed on purpose, and git is already the undo — it is the system of
record, so nothing here needs its own recovery mechanism.

**There is no merge-into-existing mode.** An earlier draft of this section had
one — a per-field "what is already in `projects/` wins" rule, reached by
`--update`. It was dropped for two reasons. There is no use for it: the import
is the bootstrap, and the case it was built for (adding a project that turned up
in a later extract) is answered by a team committing a YAML file. And it would
have cost the property everything else in this pipeline has — `merged.csv`, the
jsonl tables, the report model and all five outputs are **pure functions of
their inputs**, rebuilt identically from nothing. A `convert` whose result
depended on what happened to be on disk would be the single exception, and it
would have pushed per-field merge semantics into `import_csv.py` to pay for it.

To see what an extract *would* say without touching anything, convert it
elsewhere and diff. **The path must be inside the repo**: everything runs in a
container with the repo bind-mounted, so `/tmp/anything` is written inside the
container and is gone when it exits.

```bash
./import/import.sh convert --out out/import-preview
diff -r projects/ out/import-preview
```

**Unchanged files are not rewritten**, so `git status` after an import shows
only real changes.

## 6. Components to build

### 6.1 `validate.py` — five layers

Syntax checking alone is too weak. Build all five.

1. **Parse.** Use `ruamel.yaml`, not PyYAML, for two reasons: it reports line and
   column for every node, and it rejects duplicate keys. PyYAML silently keeps
   the last one, so a file with two `storage:` blocks would be quietly wrong
   forever.
2. **Structure.** JSON Schema (`jsonschema`, draft 2020-12), written as YAML.
   `additionalProperties: false` at **every** level, not just the root, so a
   typo like `classification.secrutiy_class` fails instead of vanishing.
   `_unmapped` is exempt.
3. **Referential integrity.** This is where the real bugs are.
   - `ownership.team_id` resolves against `teams.yaml`
   - `datacenter` resolves against `sites.yaml`
   - every enum code exists in `taxonomy.yaml`
   - every `integration.depends_on` entry names a project that exists
   - the dependency graph has no cycles (report the cycle, not just its existence)

   Drive all of this from annotations in the schema, never from field names in
   the code. See 6.1.1.
4. **Plausibility (warnings, not errors).** `deployment_model: container` with
   `containerised: none`; a tier-1 project with `rto_hours: 168`; a declared
   storage class with size 0; no `prod` environment; `migration.status: migrated`
   with open blockers.
5. **Completeness (a number, not a verdict).** Percent of assessed fields per
   project and per team. Feeds `coverage` in the report model.

**Error messages must name file, line and fix.** This is the single
highest-leverage piece of the whole validator:

```
projects/payment-gateway.yaml:14:22
  classification_b: 'CB-7' is not a known code
  valid: CB-1, CB-2, CB-3, CB-4  (see schema/taxonomy.yaml)
```

Not `ValidationError: 'CB-7' is not one of [...]`.

One code path, three call sites: `./check.sh` locally, a pre-commit hook, and CI.

### 6.1.1 The schema drives everything — no field names in code

The set of collected fields is a moving target (1). If adding a field means
editing Python, it will not happen at the rate the assessment needs, and the
people who must do it are the ones least able to. So `validate.py`,
`snapshot.py` and `build_model.py` contain **no field names, no codes and no
enum values**. Everything is declared in `schema/project.schema.yaml`:

| annotation | effect |
|---|---|
| `x-taxonomy: <group>` | value must be a code in that `taxonomy.yaml` group |
| `x-ref: sites \| teams \| projects` | value must exist in that reference file |
| `x-tracked: true` | counts toward the coverage percentage |
| `x-column: true` | gets a column in the projects table, the model and the Excel sheet. A string names the column: `x-column: migration_status` |
| `x-graph: <name>` | values are edges of a graph that must stay acyclic |
| `x-kind: <kind>` | wording of the error message. Set on the `$defs`, not on fields |

**Adding a field is one edit to one file.** `x-column` must not appear on a
field inside a list: the projects table has one row per project, so a repeated
field has no single value to put in a column — it belongs on the
`datastores`/`environments` table at its own grain.

The `$defs` exist so the YAML traps in 9 are solved once for everybody:
`text`, `code`, `count` (never 0 for missing), `date`, `version` (always a
quoted string). Point a field at a `$def` rather than writing types by hand.

**`--check-schema` is not optional.** A misspelled annotation (`x-taxonmy`) is
ignored, and the field is then validated less than the author thinks —
silently. That is the worst failure mode this design has, so it gets its own
check: the schema is validated against the meta-schema, every `x-taxonomy`
must name a real group, every `x-ref` a real file, and every taxonomy code
must appear in its group's `order`. Run it in CI.

### 6.1.2 Optional is the default; think before making anything mandatory

Only `id` and `schema_version` are required, and that is the decision in 2:
incomplete never fails validation. The question is never how to make a field
optional but how hard to press for it:

| level | how | if the value is missing |
|---|---|---|
| silent | just add the field | nothing happens |
| **tracked** — use this | `x-tracked: true` | lowers that team's coverage %. Visible, never blocking. |
| warned | a rule in the plausibility layer | a warning, still exit 0 |
| mandatory | add the name to `required:` at the root | error, exit 1 |

A mandatory field means every existing project file must be edited before
validation passes again. And `required:` **inside a block** only applies when
that block is present, so deleting the block is a legal way around it — only
`required:` at the root of the file is unconditional.

### 6.2 `snapshot.py`

Walk `projects/**/*.yaml` (skip files starting with `_`), flatten into the five
tables listed under `flat` in the model, and write `out/tables/*.jsonl`. DuckDB
reads them directly.

Why jsonl: DuckDB cannot read YAML, and this keeps pandas out of the image.
Each table is one row per (project, child) pair — this is what makes "how many
projects have DB2" a `COUNT(DISTINCT project_id)` rather than a row count that
double-counts.

Declare the column types rather than letting `read_json_auto` infer them. A
column that is null in every row today would otherwise get a different type
tomorrow, and an empty table would lose its columns entirely — a jsonl file,
unlike Parquet, does not carry its own schema. `snapshot.py` and
`build_model.py` share one declaration so they cannot disagree.

Nothing is carried over from the previous run. The tables are a pure function
of the working tree: delete `out/` and the next run rebuilds it identically.
Git is the history.

Also write `out/tables/manifest.json`: git sha, git tag, schema version, file
count, pipeline version.

### 6.3 `build_model.py`

DuckDB SQL over the jsonl tables, driven by `reports/daily.yaml` so that
adding a newly-collected field to the deck is a config change, not a code change.
A field named in the spec but absent from the data renders `n/a`; it must not
crash the run.

### 6.4 Renderers

`charts.py` runs first; everything else embeds its PNGs.

**`xlsx.py` — the one that matters most.** Managers slice this themselves.

- Multiple sheets at different grains, each a real Excel table (`ws.add_table`)
  with autofilter and frozen header: `projects`, `datastores`, `environments`,
  `blockers`, `dependencies`.
- On `projects`, also carry flattened convenience columns computed in
  `build_model.py`: `datastore_engines: "db2, redis"`, `has_db2: yes`,
  `sites: "muc-01"`, `os_families: "windows"`. Managers filter on these without
  thinking about grain; the multi-grain sheets are there for when they need to
  be correct.
- **Two columns per coded field**: `security_class_code` = `S3` and
  `security_class_label` = `Vertraulich`. Pivot on the code so sort order is
  right, display the label.
- A `counts` sheet: every single-field frequency table stacked vertically, no
  pivot skills required. This answers most of what gets asked and is what people
  paste into emails.
- A `definitions` sheet with the counting rules, one line each.
- A `dashboard` sheet with the PNGs.
- **openpyxl cannot create pivot tables.** Ship `templates/workbook.xlsx`
  containing the pre-built pivots and empty tables, load it, write the data into
  the tables, and set the pivot caches to refresh on open. If you write the
  workbook from scratch, the pivots are gone and this feature fails silently.
- Resist heavy formatting on the data sheets. It fights pivot tables. Fonts:
  Arial or Calibri, nothing exotic.

**`pdf.py` — Typst.** Write `report_model.json` next to the template and read it
with `#let data = json("report_model.json")`. Invoke `typst compile`. Typst is
chosen over LaTeX for image size and speed, and over WeasyPrint because the
output is a paginated management report. If Typst turns out to be a problem,
WeasyPrint (HTML/CSS, pure pip) is the fallback — but do not switch without
saying so.

**`pptx.py` — python-pptx.** Work from `templates/deck.potx`, filling named
placeholders by `idx`. Do not build slides from scratch and do not use
LibreOffice headless to convert. The corporate template is not yet supplied:
build against a plausible placeholder template and keep the layout/placeholder
mapping in one dict at the top of the file so swapping templates is a
five-minute job.

**`png`** — matplotlib, `Agg` backend, one chart per file, deterministic
filenames from the chart `key`.

**`txt.py`** — Jinja2, plain text, fixed column widths. Cheap, and it diffs
cleanly day over day, which turns out to be the fastest way to answer "what
changed since yesterday".

### 6.5 Entry points

Bash scripts, not a Makefile. One script per job. The pipeline's own jobs sit
at the repository root so that `ls` shows what can be run; the two import steps
live in the directories they work in, each self-contained with its tool, tests
and README (§3.1):

```
./merge/merge.sh    # step 1: merge/input/*.csv -> merge/merged.csv (§3.1, §5.4)
./import/import.sh  # step 2: import/merged.csv -> projects/ (§3.1)
./check.sh          # validate.py, exits non-zero on invalid data
./snapshot.sh       # flatten projects/ into out/tables/*.jsonl
./report.sh         # full pipeline -> out/reports/<date>/
./serve.sh          # HTTP directory listing of out/reports, for a browser
./verify.sh         # tests + a --network=none pipeline run against tests/fixtures
./shell.sh          # interactive shell in the pipeline image
```

`./serve.sh` is the exception to "nothing needs the network": the machine that
runs the pipeline has no desktop, so the only way to look at an xlsx or a PNG
is to open it from a browser elsewhere. It publishes a port, mounts
`out/reports` read-only and nothing else, and prints the URL. There is no
authentication - see `--bind`. `./report.sh` says nothing about it: the URL
does not change between runs, and one more line on every daily run to repeat
what starting the server already said is noise.

Each script is a few lines: source `scripts/lib.sh`, call `run_in_container`.
**All container knowledge lives in `scripts/lib.sh`** — engine detection, mount
flags, `--network=none`, user mapping, exit-code translation. When something
about the runtime changes it changes there and nowhere else.

`scripts/lib.sh` must:

- Prefer **podman**, fall back to docker, and honour `$CONTAINER_ENGINE`.
  Podman's semantics are the baseline: `--userns=keep-id` for rootless uid
  mapping (docker gets `--user`), and `:z` on every bind mount so SELinux
  hosts do not fail with permission denied.
- **Propagate the exit code verbatim**, except 125-127, which mean the runtime
  itself failed rather than the data. Those become 2. CI must never read
  "the image failed to start" as "the data is fine".
- Build the image on first use, so a clean checkout needs nothing but the
  container engine.

Every target runs in a container. `./report.sh` must work on a clean checkout
with only podman (or docker) installed.

The pipeline does **not** shell out to `docker compose`: `podman compose` in
4.x delegates to whichever compose implementation happens to be installed, and
that is not something to put underneath a pre-commit hook. The scripts call
`podman run` / `docker run` directly. See §8.3 for what compose is still for.

## 7. Counting rules — settle this before writing SQL

"How many projects have DB2" has at least three defensible answers: projects with
any DB2 instance, projects where DB2 is primary, and total DB2 instances. If two
slides disagree, the meeting is about the numbers instead of the migration.

Put every rule in `reports/definitions.yaml`, apply it in exactly one place in
`build_model.py`, reference its key from every table in the model, and print the
definitions on a sheet in the Excel and an appendix page in the PDF.

Starting set:

```yaml
projects_with_engine:
  grain: datastores        # which table the rows come from
  count: projects          # COUNT(DISTINCT project_id), never double counted
  text_de: "Projekte mit mindestens einem Datastore mit engine=X, alle Umgebungen."
  text_en: "Projects having at least one datastore with engine=X."
projects_by_status:
  grain: projects
  count: projects          # one row per project
environments_by_site_and_os:
  grain: environments
  count: rows              # per ENVIRONMENT, not per project
  text_de: "Umgebungen an Standort X mit OS-Familie Y. Ein Projekt kann an mehreren Standorten zählen."
```

A rule declares its **grain** and what a number **counts**; `build_model.py`
composes the SQL from those two in one place. Deliberately not a raw `sql:`
string per rule: that is a second language for a junior to get wrong and an
injection path into the query builder, and the shapes actually needed are few.

**Decide how `unknown` is counted, and show it.** If 40% of projects have not
declared an OS, "how many run Windows" has a denominator problem. The honest
chart shows the unknown bar. Put coverage next to every count, at least for the
first months when it will be embarrassing and therefore useful.

## 8. Docker

### 8.1 Images

Two, both built outside the air gap and carried in.

**`Dockerfile.pipeline`** — `python:3.12-slim-bookworm` base.
- pip, pinned exactly, installed at build time: `ruamel.yaml jsonschema duckdb
  pytest`, plus `openpyxl python-pptx matplotlib jinja2` as M3-M5 need them.
  **Not PyYAML** — `ruamel.yaml` is the only YAML library, so there is one
  parser with one set of behaviours rather than two that disagree about
  duplicate keys.
- `shellcheck` (apt): the entry points are shell and juniors maintain them.
- Typst: download the release tarball in a builder stage from
  `github.com/typst/typst/releases`, copy the single binary into the final image.
  Pin the version and record it. Check what is current; do not assume.
- Fonts: `fonts-dejavu fonts-liberation`. Add the corporate font when supplied.
- Locale `de_DE.UTF-8` and `TZ=Europe/Berlin`. Install `locales`, generate, set
  `LANG`. Without this, German month names and number formatting are wrong.
- Non-root user, `WORKDIR /work`.

**`Dockerfile.import`** — same base, only `pyyaml`. Kept separate because the
import runs rarely and does not need the renderer stack. `csv` is in the
standard library, and with the xlsx path gone (§3) `openpyxl` is gone with it:
one fewer dependency to pin, bundle and checksum for M6.

### 8.2 Air-gap traps

These all reach for the network at *runtime* when you least expect it. Every one
of them must be neutralised at build time.

| Component | Trap | Fix |
|---|---|---|
| DuckDB | Downloads extensions on first use (`httpfs`, `excel`, `spatial`). `parquet` and `json` are built in. | Avoid the optional ones. If unavoidable, pre-install at build time, set `extension_directory`, and `SET autoinstall_known_extensions=false; SET autoload_known_extensions=false;` |
| Typst | Fetches `@preview/...` packages from its registry | Write templates with no external packages, or vendor them into the local package dir |
| matplotlib | Builds a font cache on first run | Bake the cache at build time, set `MPLCONFIGDIR=/opt/mpl` |
| Fonts | A missing font does not error, it silently substitutes | Install explicitly; assert at startup that the fonts you use resolve |
| pip | A `requirements.txt` installed at runtime | Install everything at image build time. No pip at runtime. |
| CA certs | Internal GitLab uses an internal CA | Mount the CA bundle; do not bake it into a public image |

**The discipline that catches all of these:** a CI job *outside* the air gap that
runs the complete pipeline with `--network=none` against `tests/fixtures/`. If it
passes there, it passes inside. Without that job you will discover each trap one
at a time, on the wrong side of the boundary, with a multi-day feedback loop.
Build this job early, not last.

### 8.3 `docker-compose.yml`

Compose is **not** on the path of the daily pipeline — the bash entry points
in §6.5 call the container engine directly. It is kept for the services that
genuinely are long-lived, and as a convenience for people who prefer it.
Services are one-shot jobs, not daemons.

```yaml
services:
  merge:     # step 1: merge/input/*.csv -> merge/merged.csv
  import:    # step 2: profile + convert
  check:     # validate.py
  report:    # full pipeline: snapshot -> model -> renderers
  shell:     # interactive, for debugging
  serve:     # profile: serve  - directory listing over ./out/reports, read-only
  scheduler: # profile: schedule - supercronic running `report` daily
```

- All services use the same pinned image digest.
- Bind-mount the repo at `/work`; bind-mount `./out`. No named volumes for
  outputs — people need to find the files.
- `read_only: true` with a `tmpfs` for `/tmp` on the report service. It should
  not be writing anywhere except `out/`.
- `network_mode: none` on `check` and `report`. This is not paranoia, it is the
  cheapest possible regression test for §8.2.
- MinIO is **optional**, behind a `storage` profile. Default is the bind mount.
  Do not make object storage a prerequisite for a first run.

**Revised: `serve` is the pipeline image, not nginx:alpine.** The entry point
is `./serve.sh` (6.5) and the compose service mirrors it. `python3 -m
http.server` over `out/reports` produces the directory listing this needs, out
of an image that is already built, already pinned and already carried in;
nginx would be a second image to pin, bundle and checksum in the M6 offline
bundle forever, for nothing this use asks for. It is one line in `serve.sh` if
that stops being true.

### 8.4 Scheduling

Default to `./report.sh` driven by a GitLab CI schedule — the
runner already exists and it is the smallest thing that works. The `scheduler`
compose profile is the fallback for environments with no CI. Do not reach for
Airflow. Argo Workflows becomes worth it only when collectors fan out per team,
which is not yet the case.

## 9. Gotchas already paid for

These were found the hard way while building the importer. Do not rediscover them.

- **`yes` and `no` are booleans in YAML 1.1**, which PyYAML and most tooling
  still implement. `in_version_control: no` parses as `False` and will not
  compare equal to the string your renderer expects. This is why the vocabulary
  is `full` / `partial` / `none` / `unknown`. Same family: the Norway problem.
- **Version numbers must be strings.** `version: 7.9` is a float that loses
  trailing zeros; `version: "2016"` unquoted is an integer; `1.2.3` is a string
  but `1.2` is not. Force `type: string` plus a `pattern` in the schema for every
  version field, or a PDF will eventually print `7.9000000000000004`.
- **`2.500` is 2500 in German and 2.5 in English**, and a single cell carries no
  evidence. The importer decides per column and flags the ambiguous case. If you
  ever parse numbers from text again, do the same.
- **A decimal comma looks exactly like a multi-value separator.** Detect numbers
  and dates *before* attempting to split cells.
- **`data_only=True` in openpyxl is destructive if you save**; the workbook has
  no formulas left. And on a file openpyxl just wrote it returns `None`
  everywhere because there are no cached values. *(No longer reachable from the
  import path — §3 removed it — but `xlsx.py` still writes workbooks.)*
- **Pin renderer versions tightly.** Typst and matplotlib both shift layout
  between minor versions, which breaks byte-level reproducibility of the PDFs.

CSV adds its own, and they matter more now that CSV is the only input (§3):

- **A CSV has no types. Every cell is a string.** `007` is not `7` and must not
  become it: an id, a cost centre or a Projekt-Nr with a leading zero is
  destroyed by a numeric coercion, silently and irreversibly. Coerce only where
  the mapping says to.
- **A CSV Excel has touched is already damaged**, and the damage is upstream and
  undetectable from the file: dates rewritten to the export locale, long numbers
  in scientific notation, leading zeros gone. Ask for an extract written by the
  source system, not one re-saved from a spreadsheet, and record in the import
  report which files look re-saved.
- **The delimiter is per file, and German exports use `;`** — because the
  decimal comma has taken `,`. Sniff it per file (§3.2) and never assume one
  file's dialect holds for the next.
- **A quoted field may contain newlines.** That is valid CSV. `wc -l` and
  `split('\n')` both get the row count wrong; the `csv` module does not. Never
  count rows by counting lines.
- **A BOM makes the first column name unequal to itself.** `utf-8-sig` strips
  it; plain `utf-8` leaves `\ufeff` glued to the first header, and that column
  then merges with nothing (§3.2).
- **An empty cell is not a zero and not a `no`.** It is "nobody answered", which
  is §5.1's rule and §5.4's reason first-*non-empty* wins.

## 10. Milestones and acceptance

Build in this order. Each milestone must be demonstrably working before the next.

**M1 — Validation runs in a container. DONE.**
`./check.sh` against `tests/fixtures/` exits 0 on valid files and 1 on a
deliberately broken one, printing file:line:fix. Includes a fixture with a
dependency cycle and one with an unknown enum code.

**M2 — Tables and model. DONE.**
`./snapshot.sh && ./report.sh` produces `report_model.json`. Running it twice on
identical input produces byte-identical output. Asserted in a test, and in
`./verify.sh` on every run.

**M3 — Excel first. DONE.** It is the output that gets used.
Acceptance: open it, build a pivot of site × OS family from the `environments`
sheet without touching the data, and have the numbers match the `counts` sheet.
`./report.sh` writes `out/reports/<date>/report.xlsx`, and the workbook is
byte-identical for identical input, as the model is. The pre-built pivots live
in `templates/workbook.xlsx`, which `tools/make_workbook_template.py`
generates from the `pivots:` section of `reports/daily.yaml` - openpyxl cannot
create a pivot table, and a hand-made template could not be rebuilt after a
schema change by anyone without Excel.

**M4 — TXT and PNG. DONE.** Cheap, and they make the model easy to eyeball.
`charts.py` runs first in `./report.sh` and everything else embeds its PNGs;
one file per entry in the `charts:` list of `reports/daily.yaml`, named from
the chart key. A table the data cannot support yet still gets its PNG,
carrying the model's own note, because the dashboard, the deck and the PDF
reference a chart by the path the model gave them. The PNGs and `report.txt`
are byte-identical for identical input, like the model and the workbook, and
`./verify.sh` now hashes the whole report directory rather than a list of
names so a newly configured chart is covered without editing it.

The matplotlib font cache trap in 8.2 fired here and is closed in
`Dockerfile.pipeline`: the cache is baked at build time into `$MPLCONFIGDIR`,
which must also be *writable* - matplotlib rejects a read-only one, warns, and
silently rebuilds the cache somewhere else, which is the failure this was
meant to prevent.

**M5 — PDF and PPTX. DONE.** All five formats now come out of one model.
`./report.sh` writes `report.pdf` and `deck.pptx` next to the rest, both
byte-identical for identical input like everything before them, and
`./verify.sh` covers them because it hashes the whole report directory.

The PDF is Typst 0.15.1, pinned by version and sha256 in a builder stage of
`Dockerfile.pipeline`. `pdf.py` copies `templates/report.typ` next to the
model, compiles with the report directory as the Typst root - which is what
makes `json("report_model.json")` and the chart paths resolve, and means the
compiler can read nothing outside it - and removes the copy afterwards, so the
report directory stays the set of files 4 documents.

Both Typst traps in 8.2 fired and are closed. The package registry one was
predicted: the template imports nothing, `TYPST_PACKAGE_PATH` points into the
image, and a test asserts the template stays that way. The font one was not,
and is the same shape as M4's: `typst compile` **warns** about an unknown
family, substitutes, writes the PDF and exits 0. `pdf.py` turns any such
warning into a failure and deletes the PDF Typst had already written. It
matches on the warning rather than on a list of font names, so whatever the
template asks for is what gets checked. Determinism needed one more pin:
Typst stamps the wall clock into the PDF unless `--creation-timestamp` says
otherwise, which is wired to the model's own `generated_at`.

The deck is python-pptx over `templates/deck.potx`, with the layout and
placeholder mapping in one dict at the top of `pptx.py` as 6.4 asks. Two
things bit:

- **python-pptx refuses a real `.potx`.** A PowerPoint template differs from a
  presentation by one OPC content type, and python-pptx checks it and raises
  `ValueError: ... is not a PowerPoint file`. The corporate template (12.2)
  will be a genuine .potx, so `load_template` swaps that content type in a
  copy held in memory rather than asking whoever delivers it to rename the
  file into something it is not. The placeholder template is a genuine .potx
  for the same reason: so the swap is not the first time this path runs.
- **`tools/render/pptx.py` is called `pptx.py` and so is python-pptx.** Run as
  a script, `sys.path[0]` is `tools/render`, so `import pptx` finds the
  renderer, imports it a second time under that name, and fails against a
  half-initialised module - reported as "python-pptx is not installed", which
  is a lie. The renderer drops its own directory from the search path before
  importing and puts it back afterwards, and `tests/conftest.py` keeps the
  renderer directory at the *end* of `sys.path` so a test asking for the
  library gets the library.

Until the corporate template arrives, `tools/make_deck_template.py` generates
the placeholder, for the same reason `make_workbook_template.py` generates the
workbook: a binary nobody can rebuild is a binary nobody can review. It also
paid for a third trap - writing `left` on a layout placeholder that *inherits*
its geometry from the master creates an offset with no extent, and the
placeholder ends up at height zero. Only shapes that own their geometry are
scaled; the rest follow the master.

12.1 is still open and is visible in the output: codes with no label print
bare, in the PDF and the deck as they already did in the charts. Inventing
words was not on the table.

**Not a milestone of its own, and REOPENED by §3:** `import.sh` and
`docker/Dockerfile.import`. Both run in a container and that part stands, but
the importer itself is being replaced: the xlsx reader goes and the
import becomes two steps (§3.1).

**Step 1, `merge/merge_csv.py`. DONE.** Every source CSV in `merge/input/`
reduced to one `merge/merged.csv` on a `--key` join column, first-wins per cell (§5.4),
with `merge_conflicts.csv` and `merge_manifest.json` beside it.
`merge/merge.sh` runs it in the import image. It writes nothing outside
`merge/`: handing the result to step 2 is a manual `cp`, printed as the last
line of its output.

The whole step is **self-contained in `merge/`** — entry point, tool, tests,
fixtures and its own README. It is the one component that needs nothing from
the schema, the taxonomy or the mapping (§3.4), and could not use them if it
wanted to: knowing which column is behind which field needs `mapping.yaml`,
which `profile` writes, which runs on this step's own output. The ordering
forces the independence, and the independence is what lets it be read and
tested alone.

The acceptance was: fixture CSVs with deliberate overlaps, blanks,
contradictions, a key differing only by case and a row with a blank key merge
into the expected `merged.csv`; every discarded value appears in
`merge_conflicts.csv`; the blank-key rows survive as separate rows; and a
second run is byte-identical. `merge/tests/fixtures/input/` holds three
sources covering all of it — they also disagree about encoding and delimiter, and one
carries a header differing from another's by a single space — and
`merge/tests/test_merge.py` asserts each, 21 tests.

Two things the build settled that the design had not. The **key column produces
no conflicts of its own**: normalising it is the merge's one licensed
normalisation, so every difference it can show is already named by the
casefold-merge report, and logging it twice buried the three real conflicts
under the noise. And **surrounding whitespace is trimmed from every cell**,
because `16 ` against `16` is not a disagreement about anything. Both are
written into §5.4 rule 5.

The merge is **stdlib-only**, which is not an aesthetic choice: the tests run in
the pipeline image, which carries `ruamel.yaml` and deliberately no PyYAML, so
anything `merge_csv.py` imports has to be importable there. That is why its
manifest is JSON while `projects/_import_manifest.yaml`, written by step 2 in
the import image, is YAML.

**Step 2, `import_xlsx.py` becomes `import_csv.py`.** Reads only `merged.csv`.
Adds the new-field / new-value handling (§3.4) and the bootstrap refusal
(§5.4.2). `merge/make_testdata.py` generates several overlapping CSVs rather than one
spreadsheet. Acceptance: `merged.csv` converts to the expected `projects/`
tree, a new taxonomy value lands in `taxonomy.yaml` with a null label, a new
column lands in `_unmapped` with a stanza in `proposals.md`, and a second run
**refuses**, naming the file count and how to redo the bootstrap (§5.4.2) —
rather than rewriting anything.

It also needs tests, which is a gap worth naming: the importer has none today,
and step 1 shipped with 21. Two are load-bearing. One end-to-end across the
seam — merge the fixtures, convert the result, assert the project files — which
is the only thing that checks step 1 and step 2 agree about the dialect of the
file one writes and the other reads. And one on real-shaped data: the hand-made
fixtures are small and tidy, while `import/example-extract.csv` carries the
preamble rows, the mixed units, the two date formats and the spelling variants
that `value_map.yaml` exists for.

The paragraph below describes the importer as it was, and is kept because the
`--group-column` trap it records still applies. `import_xlsx.py` gained one line:
`--group-column none` switches off the auto-detected grouping column, without
which a re-import would recreate the per-team directories that 2 removed
(`--group-column ""` falls through to auto-detection, because the empty string
is falsy). Auto-detection is unchanged when the flag is absent.

**M6 — `--network=none` CI job passing**, and the offline bundle documented:
images as OCI archives pinned by digest, the repo as a `git bundle` so history
comes along, schema, taxonomy, fonts, and a manifest with checksums.

**Definition of done for the whole thing:** on a machine with only a container
engine and the bundle, `./report.sh` produces all five formats in
`out/reports/<date>/` with no
network access, and `out/reports/<date>/manifest.json` records the git sha,
schema version and image digest that produced them.

## 11. Do not

- Do not let renderers compute anything. They lay out the model. If a renderer
  needs a number that is not in the model, add it to the model.
- Do not use `localStorage` or any browser storage; there is no browser here.
- Do not install packages at runtime.
- Do not make the Excel pretty at the expense of being pivotable.
- Do not silently drop `unknown` from any distribution.
- Do not store computed scores in the project YAML.
- Do not convert xlsx or pptx to PDF via LibreOffice headless. Render the PDF
  from the model directly.
- Do not add a web UI.
- Do not treat imported data as verified. Report raw coverage and verified
  coverage as two separate numbers everywhere they appear.

## 12. Open questions for the human

Ask before guessing on these; each one changes the shape of the code.

1. **Taxonomy semantics. STILL OPEN.** CA-1..3, CB-1..4, S1..S4 and the tier
   codes are opaque outside the room they were invented in. `taxonomy.yaml`
   carries `label_de: null` for each rather than invented words, and
   `--check-schema` warns about every one until they are filled in. It did not
   block M5 in the end, because M4 had already settled what to do about it: a
   code with no label is printed as the bare code. That is now true of the
   charts, the PDF, the deck and the Excel alike - honest, and visible to
   management, which is the point. Filling the labels in is one edit per code
   and changes every output at once.
2. **Corporate PowerPoint template. STILL OPEN, no longer blocking.** M5 is
   built against the placeholder `tools/make_deck_template.py` generates - a
   genuine 16:9 `.potx`, so the loader has already met the file type the real
   one will be. Swapping it in is an edit to the `LAYOUTS` dict at the top of
   `tools/render/pptx.py` and nothing else; the layout names in that dict are
   checked against the template on every run, so a swap that moves a layout
   says so instead of producing a deck laid out on the wrong master.
3. **Report scoping.** One deck for everyone, or a per-team deck as well? Affects
   whether `build_model.py` runs once or once per team.
4. **`placement`: single datacenter or multiple environments? STILL OPEN, but
   no longer blocking.** Both shapes validate, and `snapshot.py` normalises
   them into environment rows — a flat `datacenter` scalar becomes one
   environment with no name, because the legacy extract has nothing to derive
   a name from. A file using both at once gets a plausibility warning. This is
   recorded in `definitions.yaml` because it changes reported numbers: the
   site × OS cross-tab counts environments, so the choice affects the totals.
5. ~~**Retention.** How many daily snapshots are kept before rolling up?~~
   **Answered:** none. There is no snapshot history, no trend reporting and
   no day-over-day comparison. `out/tables/` is the current run and nothing
   else is kept. Reports under `out/reports/<date>/` are the deliverable and
   are not pruned.

6. **Per-field precedence. OPEN, and the likeliest thing to be wrong.** §5.4
   ranks whole files: one order, applied to every column. Real sources are not
   like that — an HR or SAP extract is authoritative for ownership and cost
   centre while being months stale on hardware, and a CMDB dump is the reverse.
   Whole-file ordering cannot express that, so wherever it is wrong the answer
   is to fix the data upstream, not to tune the order.

   If it turns out to be needed, the shape is an override in the config keyed by
   field, and it stays declarative — no per-file scripts:

   ```yaml
   # import/precedence.yaml
   default: [01-sap-export.csv, 02-cmdb-dump.csv, 03-team-umfrage.csv]
   overrides:
     platform.cpu_cores:   [02-cmdb-dump.csv, 01-sap-export.csv]
     ownership.team_id:    [01-sap-export.csv]
   ```

   **Do not build this until the conflict report from a real import shows it is
   needed.** The report exists precisely to answer the question with evidence:
   run the import, read which columns actually contradict and how often, and
   decide then. Building it first means guessing which columns matter.

7. **Multi-value columns: first wins, or union? OPEN.** `Datenbank: DB2` in the
   SAP extract and `Datenbank: Redis` in the CMDB dump is not really a
   contradiction — the project probably has both, and first-wins throws one
   away. The union is likely right for genuinely repeated fields (datastores,
   storage classes, dependencies) and clearly wrong for scalar ones (a project
   has one owning team).

   The merge cannot tell them apart, and deliberately so: §5.4 rule 5 keeps it
   free of any knowledge of types, and the column kinds are not known until
   `profile` runs on its output. So the merge stays first-wins and the case
   shows up in `merge_conflicts.csv`, where a column whose "conflicts" are
   nearly all distinct short tokens is the signature to look for.

   If the union is wanted, the shape is a declared list of columns in the
   config — never inference:

   ```yaml
   # merge/precedence.yaml
   key: "Projekt-Nr"
   union_columns: ["Datenbank", "Storage Klasse"]
   ```

   Two things to settle with it: what separator joins the union (the value map
   in step 2 splits on it, so they must agree), and whether the union is ordered
   by precedence or sorted. **Do not build it before a real extract shows which
   columns it matters for.**

8. **Are the CSVs committed?** They are the evidence behind every project file
   and they are what makes an import reproducible, which argues for committing
   them. Against: they may be large, they may carry contact details or other
   data that has no business in a repo every team can read, and
   `_import_manifest.yaml` already records each one's sha256 — enough to prove
   *which* file was used without holding the file. Currently `projekte.xlsx` is
   gitignored, so the status quo answer is no. Decide before the first real
   extract lands, because removing a file from git history afterwards is a
   rewrite, not a delete.
