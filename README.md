# Continuum catalogue

The reporting pipeline for **Project Continuum**, the 6R portfolio assessment
described in `continuum.md` (`continuum.de.md` in German). That document is the
initiative; this repository is the catalogue and the daily report built from it.

Git is the system of record: one YAML file per project, flat in `projects/`.
Everything derived — snapshots, the report model, the five output formats — is
rebuilt from those files and never hand-maintained.

`SPEC.md` is the design document. This README is the operating manual.

**Status: M1 (validation), M2 (snapshot + model), M3 (Excel), M4 (TXT + PNG)
and M5 (PDF + PPTX) complete.** All five output formats come out of one model.
M6 is not built: there is no offline bundle yet, though the `--network=none`
pipeline run it depends on has been enforced by `./verify.sh` since M1.

**The import is CSV only.** The source data is several overlapping CSV
extracts, not one spreadsheet; the xlsx import is gone, and xlsx is an output
format here and nothing else. Both steps run — extract → merge → import →
validate → report works end to end on a clean checkout.

## Requirements

Podman (preferred) or Docker. Nothing else — no Python, no pip on the host.
Every tool runs in a container.

## Quick start

```bash
./verify.sh                         # everything: shellcheck, schema self-test, tests, exit codes
./check.sh tests/fixtures/projects  # validate the fixtures
./check.sh --check-schema           # validate the schema and reference files
./check.sh                          # validate projects/ - see below
./merge/merge.sh --key "Projekt-Nr" # step 1: merge/input/*.csv -> merged.csv
./import/import.sh convert                 # step 2: merged.csv -> projects/ (not built)
./snapshot.sh                       # projects/ -> out/tables/*.jsonl
./report.sh                         # snapshot + model + all five formats -> out/reports/<date>/
./report.sh --lang en               # same, English labels
./serve.sh --detach                 # browse out/reports from another machine
./shell.sh                          # interactive shell in the pipeline image
```

The image builds itself on first use.

**`projects/` does not exist yet on a fresh checkout**, so a bare `./check.sh`
exits 2 and tells you so. It is not part of the repo skeleton: it is produced by
the importer from the CSV extracts, which have to be in `merge/` with a
hand-edited `import/mapping.yaml` (see *Importing* below). Until then, validate
`tests/fixtures/projects`.

Exit codes are the contract:

| code | meaning |
|---|---|
| 0 | valid |
| 1 | invalid data (or, with `--strict`, warnings too) |
| 2 | tool or usage error — including a container runtime failure |

2 is deliberately distinct: CI must never read "the image failed to start" as
"the data is fine".

### Pre-commit hook

```bash
ln -sf ../../scripts/precommit.sh .git/hooks/pre-commit
```

Same code path as `check.sh` and CI. To avoid paying container startup on every
commit, keep a warm container and set `USE_WARM_CONTAINER=1`:

```bash
podman run -d --name continuum-dev --network=none -v "$PWD:/work:z" \
    -w /work "continuum-pipeline:$(cat VERSION)" sleep infinity
```

## Importing

The catalogue is bootstrapped from legacy CSV extracts. There is **no xlsx
import** — several systems each export a CSV, they overlap, and they contradict
each other. Once `projects/` exists the YAML is the system of record and the
CSVs are history.

**Two steps, two directories, each with its own README.**

```bash
./merge/merge.sh --key "Projekt-Nr"   # step 1: merge/input/*.csv -> merged.csv
cp merge/merged.csv import/merged.csv #         the copy is yours to make
./import/import.sh profile                   # step 2: -> import/profile.md
# read profile.md, edit import/mapping.yaml and import/value_map.yaml
./import/import.sh convert                   # step 2: -> projects/*.yaml
./check.sh                            # validate what came out
```

| step | where | what it does |
|---|---|---|
| 1 | **[`merge/`](merge/README.md)** | every `*.csv` in `merge/input/` reduced to one `merged.csv`, first wins per cell. Built |
| 2 | **[`import/`](import/README.md)** | that one file turned into `projects/*.yaml`. Not built yet |

Each directory holds its own tool, tests, fixtures and documentation, so the
rules live beside the files they govern. The counting rules the merge applies
are SPEC §5.4; what happens when a CSV contains something the schema has never
seen is SPEC §3.4.

**The handoff between them is a manual `cp`**, printed as the last line of the
merge's output. That is the inspection gate: until you run it, step 2 keeps
reading whatever it read before, so re-cutting an extract cannot change the
catalogue behind anyone's back. A merge that copied itself onward would be a
pause, not a gate.

`merge/make_testdata.py` generates three synthetic extracts that overlap,
contradict each other on 33 cells and leave gaps each other fills — enough for
the merge to have something to show. `import/example-extract.csv` is a single
committed one for a quick look without generating anything, and
[`import/README.md`](import/README.md) walks the whole thing through.

## The pipeline

```
projects/**/*.yaml
      |  snapshot.py        five tables, one row per (project, child)
      v
out/tables/*.jsonl  ->  DuckDB  ->  out/reports/<date>/report_model.json
                                       |  renderers lay out; they never compute
                                       v
                       *.png            <- M4. Runs first; the xlsx
                       report.xlsx      <- M3     dashboard, the PDF and the
                       report.txt       <- M4     deck all embed these PNGs.
                       report.pdf       <- M5, Typst
                       deck.pptx        <- M5, python-pptx
```

`report_model.json` is the only thing renderers read. If a renderer needs a
number that is not in the model, the number goes in the model (SPEC 11).

### Reproducibility

The model is byte-identical for identical input. Everything in
`build_model.py` exists to keep that true: every list is sorted, percentages
have one fixed format, and the only clock reading in the program is
`generated_at`. Pin it to compare two runs:

```bash
./report.sh --generated-at 2026-09-14T04:00:00+02:00
```

`verify.sh` asserts this on every run, and `tests/test_pipeline.py` asserts
both that the model is byte-identical and that two unpinned runs differ in
`generated_at` and nothing else. The jsonl tables are byte-identical too, so
the day-over-day delta compares data rather than noise.

### There is no history, and no state between runs

`out/tables/` is the current run, and that is all there is. Nothing is carried
between runs, nothing accumulates, and there is no day-over-day comparison:
the tables are a pure function of the working tree, so deleting `out/` and
rebuilding gives byte-identical results.

Git is the history. To reproduce an old report, check out that commit and run
the pipeline.

This replaced dated Parquet snapshots. At ~400 projects the catalogue is about
a thousand rows across all five tables — 330 KB/day as Parquet against 388 KB
as jsonl — so columnar storage bought nothing measurable, and its one real
advantage here, a queryable time series, is not wanted. The trade is that a
jsonl file does not carry its own schema, so the column types are declared in
`snapshot.py` and shared with `build_model.py` via `table_schemas()`.

### The Excel report

`out/reports/<date>/report.xlsx` is the output that actually gets used, because
managers slice it themselves.

| sheet | what it is |
|---|---|
| `dashboard` | headline numbers, raw and verified coverage, provenance, the chart PNGs |
| `counts` | every frequency table stacked vertically — no pivot skills needed, and what people paste into emails |
| `projects` | one row per project, plus convenience columns (`datastore_engines`, `sites`, `os_families`, `has_db2`, `open_blockers`) |
| `datastores` `environments` `blockers` `dependencies` | the other grains, one row per child |
| `definitions` | the counting rule behind every number, one line each |
| `pivot_site_os` | a pre-built pivot, refreshed by Excel when the file opens |

Each grain sheet is a real Excel table (`tbl_projects`, `tbl_environments`, …)
with a filter and a frozen header, so a pivot can be built straight off it.
Every coded field is **two columns**: `security_class_code` holds `S3` and
`security_class_label` holds the label. Pivot on the code — it sorts in the
taxonomy's order — and display the label.

Formatting is deliberately thin. Heavy formatting fights pivot tables.

#### The template, and why it is a committed binary

openpyxl cannot create a pivot table. `templates/workbook.xlsx` holds the
pre-built pivots; `xlsx.py` loads it and writes the data into it. Build the
workbook from scratch instead and the pivots are gone — silently, which is why
a missing template is a loud warning and `tests/test_xlsx.py` fails without one.

Which pivots exist is a report decision, so it lives in `reports/daily.yaml`
under `pivots:`. After changing that, or after a schema change that moves the
columns a pivot reads (`xlsx.py` warns when it has), rebuild the template:

```bash
./shell.sh python3 tools/make_workbook_template.py \
    --model out/reports/<date>/report_model.json
```

The template is generated rather than hand-made in Excel so that it stays in
git, diffs, and can be rebuilt by someone who has no copy of Excel. It is
built with an empty pivot cache marked *refresh on load*: Excel fills it in
from the sheet when the file is opened, so the pivot follows however many rows
arrived today.

The workbook is byte-identical for identical input, like the model. That takes
pinning both the zip entry timestamps and `docProps/core.xml`, which openpyxl
stamps with the wall clock as it saves.

### The charts

`charts.py` runs first in `report.sh`, and everything else embeds its PNGs
(SPEC 2). Five renderers each drawing their own chart is how five outputs end
up with five different numbers.

One PNG per entry in the `charts:` list of `reports/daily.yaml`, named after
the table it draws (`by_status` -> `by_status.png`), 1600x900. Adding one is a
config change: name a table that already exists in the report and the next run
renders it.

| the table's shape | what gets drawn |
|---|---|
| `distribution` | horizontal bars, one per code, the value at the bar end |
| `cross_tab` | horizontal stacked bars, one segment per column code, plus a legend |
| not available yet | a panel carrying the model's own note, at the same size |

The last row matters: a field named in the report spec that nobody has
collected yet still gets its file, because the Excel dashboard, the deck and
the PDF all reference a chart by the path the model gave them. A hole in the
deck is worse than a panel saying the field is not there.

Every chart carries its counting rule and both coverage numbers in the footer
(SPEC 7) - raw and verified, never merged (SPEC 11). `unknown` is a bar like
any other, in the grey `taxonomy.yaml` gives it; the colours are baked into
the model by `build_model.py`, so the renderer never opens the taxonomy.

Codes with no label yet are drawn as bare codes. That is SPEC 12.1 and it is
deliberate: inventing words would put invented words in front of management.

Two things keep the PNGs reproducible, which matters because the PDF and the
deck will embed them: matplotlib is pinned to the patch version, and the
`Software` chunk - which carries the matplotlib version into the file - is
dropped rather than written.

### The PDF

`pdf.py` copies `templates/report.typ` next to `report_model.json` in the
report directory and runs `typst compile` with that directory as the Typst
root — which is what makes `#let data = json("report_model.json")` and the
chart PNGs resolve, and means the compiler can read nothing else. The copy is
removed afterwards; `--keep-source` leaves it for debugging.

Typst rather than LaTeX (image size and speed) and rather than WeasyPrint (this
is a paginated management report). WeasyPrint stays the documented fallback; a
switch would be a decision to announce, not a detail to change quietly.

Eight pages against the fixtures: title and provenance, the headline numbers
and coverage, the charts, every table in the model, and the counting rules in
full as an appendix. The template contains no field name, no code and no enum
value — it walks `data.tables` and prints what is there.

Two SPEC 8.2 traps live here and both are closed:

* **Fonts.** Typst *warns* about an unknown family, substitutes, and exits 0.
  `pdf.py` turns any such warning into a failure and deletes the PDF Typst had
  already written. Because it matches on the warning rather than on a list of
  names, whatever the template asks for is what gets checked.
* **Packages.** `#import "@preview/..."` reaches for the package registry.
  The template imports nothing, `TYPST_PACKAGE_PATH` points into the image, and
  `tests/test_pdf.py` asserts the template stays that way.

The PDF is byte-identical for identical input. Typst stamps the current time
into a PDF unless told otherwise, so the creation timestamp is always pinned to
the model's own `generated_at` — the PDF metadata then says when the report was
generated *and* two runs of one model agree.

### The deck

`pptx.py` fills the placeholders of `templates/deck.potx` by `idx`. It never
builds slides from scratch and never converts anything through LibreOffice
(SPEC 11). Everything it knows about the template is the `LAYOUTS` dict at the
top of the file, checked against the template by name on every run, so a
template swap fails at that dict instead of producing a plausible deck laid out
on the wrong master.

One title slide, the headline numbers, coverage by team, one slide per chart,
one per table in the model, and the counting rules as an appendix. A table too
tall for one slide continues on the next under the same title — nothing is
dropped to make it fit.

**The corporate template has not been supplied (SPEC 12.2).** Until it arrives,
`tools/make_deck_template.py` generates the placeholder:

```bash
./shell.sh python3 tools/make_deck_template.py --out templates/deck.potx
```

Generated rather than hand-made, for the same reason `templates/workbook.xlsx`
is: a binary nobody can rebuild is a binary nobody can review. It is 16:9, uses
Arial, and is a **genuine `.potx`** — which matters, because python-pptx
refuses to open one. A PowerPoint template differs from a presentation by one
OPC content type, and python-pptx checks it and raises `ValueError: ... is not
a PowerPoint file`. `load_template` swaps that content type in a copy held in
memory, leaving the file on disk untouched, so the real template will work the
day it lands.

The chart PNGs are inserted uncropped: a picture placeholder crops an image to
fill its frame, which cuts the category labels off a wide chart, so the crop is
undone and the picture refitted to the frame at the aspect ratio the model
declares. The deck is byte-identical for identical input, the same way the
workbook is — pinned zip timestamps and document properties taken from the
model.

### The text report

`report.txt` is the cheapest output and the one that answers "what changed
since yesterday": there is no day-over-day comparison in the pipeline, so
`diff` on two of these is it.

```bash
diff out/reports/2026-09-13/report.txt out/reports/2026-09-14/report.txt
```

That is why every column is a fixed width, set in `txt.py` and never by the
data. A layout that sizes its columns to the widest value reflows every row
the day one label gets longer, and the diff is then noise rather than news.
A value too long for its column is cut with an ellipsis; the full value is in
the Excel, which is where slicing happens anyway. `tests/test_txt.py` asserts
both: that one changed field changes at most a handful of lines, and that a
much longer project name moves no column at all.

The template is `templates/report.txt.j2`, rendered with `StrictUndefined` -
a typo in the template is an error rather than a section that quietly leaves.

### Looking at the reports

The machine that runs the pipeline has no desktop, and the xlsx, the PDF, the
deck and the PNGs are all files somebody has to actually open. `./serve.sh`
puts a directory listing of `out/reports` on a port and prints the URLs to try:

```
$ ./serve.sh --detach
serving out/reports (read-only) on port 8000, bound to 0.0.0.0
  http://192.168.2.172:8000/         eno1
  http://192.168.2.174:8000/         wlp4s0f0
  http://neptun03:8000/              hostname, if DNS resolves it

stop with ./serve.sh --stop
```

```bash
./serve.sh                 # foreground, Ctrl-C to stop
./serve.sh --detach        # background, survives logout
./serve.sh --status        # running? on what URL?
./serve.sh --stop
./serve.sh --port 9000 --bind 127.0.0.1
```

Every line is a candidate, not a promise: which one works depends on what the
machine holding the browser can resolve and route to, and this machine cannot
know that. With no `--port` it starts at 8000 and moves up to the first free
one, saying so. `--status` reads the port off the running container, so it is
right whichever one it got.

It is `python3 -m http.server` out of the pipeline image rather than the
nginx:alpine SPEC 8.3 sketched. The pipeline image is already built, already
pinned and already carried into the air gap, and Python's server already
produces the listing; nginx would be a second image to pin, bundle and
checksum for M6 forever, to gain nothing this use needs. If it ever has to be
a real web server, `serve.sh` is the one line that changes.

Two things to know before pointing anyone at it:

- **There is no authentication.** Anyone who can reach the port can read every
  report. That is the intent on a trusted internal network and the wrong thing
  anywhere else - use `--bind 127.0.0.1` and an ssh tunnel when the network is
  not trusted.
- **It is read-only, and it can see almost nothing.** The container mounts
  `out/reports` and nothing else, read-only: not `projects/`, not `schema/`.
  The one container here that listens on a network is the one that should be
  able to see the least.

`xlsx` and `pptx` arrive as `application/octet-stream`, so a browser downloads
them rather than trying to display them - which is what you want, since they
are opened in Excel and PowerPoint. The PDF, the PNGs and `report.txt` render
in the browser.

`out/reports/` is never pruned (SPEC 12.5), so old dates accumulate and
`fixtures/` from `./verify.sh` sits alongside them. Clear the stale ones before
pointing anyone at the listing; `fixtures/` is test data and looks like a
report from a browser.

`docker compose --profile serve up -d serve` is the same thing for people who
prefer compose.

### Counting rules

"How many projects have DB2" has at least three defensible answers, so every
rule lives in `reports/definitions.yaml`, is applied in exactly one place
(`query_definition` in `build_model.py`), and is named by every table that
used it. The definitions are emitted into the model so they can be printed
next to the numbers.

Each rule declares a **grain** (which table the rows come from) and what a
number **counts**:

| counts | SQL | means |
|---|---|---|
| `projects` | `COUNT(DISTINCT project_id)` | a project with two DB2 instances counts once |
| `rows` | `COUNT(*)` | one per row at that grain — environments, blockers, edges |

So `by_engine` counts projects and its column sums to *more* than the project
total (a project with two engines is in both rows); `site_x_os` counts
environments and its grand total is the environment count. Both are correct;
the model says which is which, in `counts`.

### How `unknown` is counted

`unknown` is an explicit row in every distribution and is never dropped
(SPEC 11). Two rules make that true:

1. A missing or null coded value is flattened to the string `unknown` — not
   NULL, not absent. Numbers stay NULL, because `0` is a real answer.
2. A project with no `datastores:` block at all still gets one datastores row,
   flagged `is_placeholder`. Without it the engine distribution would quietly
   have a smaller denominator than the project count, and two slides would
   disagree.

Taxonomy-backed axes show every code, including ones at zero, so a chart's
axis is stable day to day. `unknown` always sorts last.

### Adding a table to the report

Config, not code. In `reports/daily.yaml`:

```yaml
  by_tier:
    kind: distribution        # or cross_tab
    definition: projects_by_classification   # from definitions.yaml
    column: tier
    taxonomy: tier            # or: reference: sites | teams
    title_de: "Projekte je Tier"
    title_en: "Projects by tier"
```

A column named here that is absent from the data renders `n/a`
(`"available": false` plus a note) rather than crashing — `by_backup_class` in
`daily.yaml` is a permanent test of that path.

## Adding or changing project data

After the bootstrap, `projects/` **is** the system of record and the extracts
are history. New projects and corrections arrive as YAML — teams edit their own
file and submit a merge request — not from another import.

### Adding a project

One file, `projects/<id>.yaml`, flat in the directory. The id is the filename
and must match the `id:` inside. **Only two fields are required:**

```yaml
schema_version: 1
id: partnerportal
```

That validates. Everything else is optional and can be filled in later, which
is deliberate (SPEC §2): a team that cannot submit a half-filled file keeps its
data in a private spreadsheet and you never see it.

A fuller one, showing the shapes that trip people up:

```yaml
schema_version: 1
id: partnerportal
name: Partnerportal
ownership:
  team_id: team-beta            # must exist in schema/teams.yaml
classification:
  security_class: S3            # a code from schema/taxonomy.yaml
datastores:                     # a list: one entry per engine
  - engine: db2
    version: "11.5"             # QUOTED - 7.9 unquoted is a float
placement:
  environments:
    - datacenter: muc-01        # must exist in schema/sites.yaml
      os:
        family: windows
platform:
  cpu_cores: 16
  storage_gb: 500
migration:
  strategy: replatform
  status: assessed
```

Then:

```bash
./check.sh                      # 0 errors, or it tells you file, line and fix
git add projects/partnerportal.yaml && git commit
```

### The rules worth knowing before you type

- **Missing is not zero.** Leave a field out, or write `null`, or write
  `unknown` — all three mean "nobody has answered", and none of them counts
  toward coverage. Never write `0` to mean "not established".
- **No booleans.** Use `full` / `partial` / `none` / `unknown`. `yes` and `no`
  are YAML 1.1 booleans and parse as `True`/`False` (SPEC §9).
- **Quote every version.** `"11.5"`, `"2016"`, `"7.9"`. Unquoted they become
  floats and integers, and a PDF eventually prints `7.9000000000000004`.
- **Coded fields take codes, not labels.** `S3`, not `Vertraulich`;
  `muc-01`, not `München 01`. The valid set for each is in
  `schema/taxonomy.yaml`, `sites.yaml` and `teams.yaml` — and the error message
  lists them when you get one wrong.
- **A typo in a field name is an error, not a silent no-op.**
  `secrutiy_class` fails validation rather than vanishing, because
  `additionalProperties: false` is set at every level.

### Finding out what may go in a file

`schema/project.schema.yaml` is the list of what exists — every field, its
type, and whether it counts toward coverage. Reading it beats guessing, and it
carries the recipe for changing it at the top.

To see what a *complete* file looks like, `tests/fixtures/projects/` holds a
dozen hand-written ones covering the edge cases: minimal, all-unknown,
all-null, umlauts, multi-environment, both `placement` shapes.

### Correcting imported data

Imported values are `_meta.confidence: imported`, which means **nobody has
looked at them**. When somebody has:

```yaml
_meta:
  confidence: verified
  last_reviewed: 2026-09-15
  reviewed_by: s.bauer
```

Raw coverage and verified coverage are reported as two separate numbers
everywhere (SPEC §11), so this is visible in the report the next morning.

Values the importer could not place sit in `_unmapped` verbatim. Promoting one
into a real field is an edit to `schema/project.schema.yaml` — see the next
section.

## Adding or changing a field

This is the common task and it is deliberately **one file**:
`schema/project.schema.yaml`. That file carries the recipe at the top, so the
instructions are in front of whoever is editing it.

```yaml
  jira_ticket:
    $ref: '#/$defs/text'      # what kind of value
    x-tracked: true           # counts toward the coverage %
    x-column: true            # appears in the report and the Excel sheet
```

Then `./check.sh --check-schema`. That is the whole change — validation,
referential integrity, coverage, the Parquet schema, the report model and the
Excel sheet all follow from it. No Python.

`schema/project.schema.yaml` is YAML, not JSON, because the people who
maintain it hand-edit YAML all day and because YAML takes comments. A `.json`
schema is still loaded if that is what a checkout has.

### Which `$ref` to use

| `$ref` | for | why it exists |
|---|---|---|
| `text` | free text | |
| `code` | one of a fixed set | valid values live in `taxonomy.yaml`, never in the schema |
| `count` | a number | `0` and "not established" must not be the same value |
| `date` | a date | a bare YAML date is accepted and normalised |
| `version` | a version number | unquoted `7.9` is a float that prints as `7.9000000000000004` |

### The annotations

| annotation | effect |
|---|---|
| `x-taxonomy: <group>` | value must be a code in that `taxonomy.yaml` group |
| `x-ref: sites` \| `teams` \| `projects` | value must exist in that reference file |
| `x-tracked: true` | counts toward the coverage percentage |
| `x-column: true` | gets a column in the projects table, the model and the Excel sheet. Use a string to name the column: `x-column: migration_status` |
| `x-graph: <name>` | values are graph edges, checked for cycles |

`x-column` cannot go on a field inside a list — the projects table has one row
per project, so a repeated field has no single value to put in a column. It
belongs on the `datastores` / `environments` table at its own grain, and
`--check-schema` says so if you try.

Use `snake_case`. A hyphenated name is legal YAML but becomes a subtraction in
SQL; identifiers are quoted so it fails loudly rather than silently.

### Optional and mandatory

**Every field is optional by default.** Only `id` and `schema_version` are
required. That is deliberate (SPEC 2): a team that cannot submit a
half-filled file will keep its data in a private spreadsheet, and you will
never see it.

So the question is never "how do I make this optional" but "how hard do I
press". Four levels:

| level | how | if the value is missing |
|---|---|---|
| silent | just add the field | nothing happens, nobody notices |
| **tracked** ← use this | `x-tracked: true` | lowers that team's coverage %. Visible, never blocking. |
| warned | a rule in the plausibility layer | a warning, still exit 0 |
| mandatory | add the name to `required:` at the top | error, exit 1, commit rejected |

`schema/project.schema.yaml` carries a worked example of each —
`my_optional_field` and `my_mandatory_field` — with the trade-offs written
next to them. Delete them once you have your own.

**Before adding a mandatory field**, know the cost: every existing project
file must be edited before validation passes again. Adding
`my_mandatory_field` broke all 10 projects and all 21 fixtures until each was
given a value.

And a trap: `required:` **inside a block** only applies when that block is
present, so deleting the block is a legal way around it. Only `required:` at
the root of the file is unconditional.

### Always run `--check-schema` after editing the schema

An annotation that is misspelled (`x-taxonmy`) would otherwise be ignored, and
the field would be validated less than you think — silently. That is the worst
failure mode this design has, so it has its own check:

```
$ ./check.sh --check-schema
schema/project.schema.yaml
  classification.security_class: unknown annotation 'x-taxonmy'
  known: x-column, x-doc, x-graph, x-kind, x-ref, x-taxonomy, x-tracked
```

## Validation

Five layers, all in `tools/validate.py`:

1. **Parse** — `ruamel.yaml` in round-trip mode. Reports line and column for
   every node and rejects duplicate keys. (Round-trip is not a preference:
   it is the only mode that populates `.lc`, which is where every `line:col`
   in every message comes from. PyYAML keeps the last of two duplicate keys
   without a word.)
2. **Structure** — JSON Schema draft 2020-12, `additionalProperties: false`
   at every level so `secrutiy_class` fails instead of vanishing.
3. **Referential** — taxonomy codes, site/team/project references, duplicate
   ids, and dependency cycles. A cycle is reported as the ring itself.
4. **Plausibility** — warnings, never errors. One small function per rule at
   the bottom of `validate.py`; copy one to add one.
5. **Completeness** — a coverage number, not a verdict. Raw coverage and
   verified coverage are reported as two separate numbers, always.

Errors name file, line and fix:

```
projects/payment-gateway.yaml:9:21
  classification_b: 'CB-7' is not a known code
  did you mean: CB-4
  valid: CB-1, CB-2, CB-3, CB-4, unknown  (see schema/taxonomy.yaml, group 'classification_b')
  To add a code, add it to that group in taxonomy.yaml. Nothing else changes.
```

A missing key, an explicit `null` and the string `unknown` all mean the same
thing: nobody has answered. None of them is an error, and none of them counts
toward coverage. A team that cannot submit a half-filled file will keep its
data in a private spreadsheet instead.

## Tests

```bash
./verify.sh
```

`tests/fixtures/projects/` holds 12 valid files covering the edge cases
(minimal, all-unknown, all-null, umlauts, multi-environment, both `placement`
shapes, unquoted-version traps). `tests/fixtures/invalid/` holds one directory
per failure mode, each with an `expect.txt` naming the finding codes that must
be reported. **Adding a failure case is a new directory, not a Python change.**

## Deviations from SPEC.md

Recorded so they read as decisions rather than drift.

Bash entry points instead of a Makefile, podman-first, and compose off the
daily path were deviations at first; SPEC.md §6.5 and §8.3 have since been
updated to specify them, so they are no longer deviations. What remains:

- **A warm container is allowed for the pre-commit hook.** §8.3 says one-shot
  jobs, and the canonical path still is; `exec` into a warm container is a
  latency optimisation for the hook only and nothing depends on it.
- **Both `placement` shapes validate** — a single `datacenter` scalar and an
  `environments` list. §12.4 is still open; `snapshot.py` normalises in M2.
  A file using both at once gets a warning.
- **`definitions.yaml` declares `grain` + `counts`, not raw SQL.** §7 sketches
  a `sql:` string per rule. Raw SQL in YAML is a second language for a junior
  to get wrong and an injection path into the query builder; the shapes
  actually needed are few, so each rule declares its grain and what it counts
  and `build_model.py` composes the SQL. The prose `text_de` / `text_en` are
  unchanged and still printed next to the numbers.
- **`docker-compose.yml` has no `report` service.** §8.3 lists one. The order
  of the pipeline lives in `./report.sh`, which also passes the git provenance
  in; a compose service would be a second copy of that order in a file nothing
  tests. `serve` and `scheduler` are genuinely long-lived and will be added
  when they are needed.
- **`templates/workbook.xlsx` is generated, not hand-built.** §6.4 assumes a
  template someone made in Excel. `tools/make_workbook_template.py` builds it
  from `reports/daily.yaml`, which keeps it in git and rebuildable without
  Excel. The pivot XML it writes was verified by opening the result in a
  spreadsheet application and checking the refreshed numbers against the
  `counts` sheet; it has not been opened in Microsoft Excel itself.
- **The model carries two things §5.2 does not list: a `kind` on every table
  and a colour on every code.** Both exist so the renderers stay dumb. A
  renderer that guesses a table's shape from its column count draws the wrong
  chart the day a table grows a column, so the shape is named. And §5.2 says
  renderers never touch the taxonomy, which would leave `charts.py` unable to
  honour the `colour` the taxonomy carries - so the colours are looked up once
  and baked in beside the labels. Where a column's codes come from a reference
  list rather than a taxonomy group (sites, teams), `unknown` takes the colour
  the taxonomy groups agree on, so the unknown bar is the same grey on every
  chart. Nothing is invented in code: if the groups disagree, there is no
  fallback and the renderer's own palette applies.
- **Dates are normalised before structural validation.** A bare YAML date
  (`2026-09-14`) resolves to a date object and would fail `type: string`.
  Versions get no such treatment: an unquoted `7.9` must fail loudly, because
  it has already lost precision.

## Open questions blocking later milestones

From SPEC §12, still unanswered:

1. **Taxonomy labels.** `CA-1..3`, `CB-1..4`, `S1..S4` and the tier codes have
   no labels; `taxonomy.yaml` carries `null` rather than invented words, and
   `--check-schema` warns about every one until they are filled in. This does
   not block a milestone but it is visible in every output: those codes print
   bare, in the charts, the PDF, the deck and the Excel alike. The labels are
   a one-line edit per code once somebody answers.
2. **`schema/sites.yaml` and `schema/teams.yaml` are provisional**, seeded from
   the fixtures. Anything missing from them will fail real data. Both are
   flagged by `--check-schema`.
3. **Corporate `.potx`.** M5 is built against the generated placeholder
   described above. Swapping in the real template is an edit to the `LAYOUTS`
   dict in `pptx.py` and nothing else.
4. **Report scoping** — one deck, or one per team (changes whether
   `build_model.py` runs once or per team).

4b. **Per-field precedence** (SPEC §12.6) — the merge ranks whole files, one
   order for every column. Real sources are not like that: an SAP extract is
   authoritative for ownership while being stale on hardware, and a CMDB dump is
   the reverse. Not built, deliberately — `merge_conflicts.csv` from a real
   import is what answers whether it is needed, and building it first means
   guessing which columns matter.

4c. **Multi-value columns** (SPEC §12.7) — `Datenbank: DB2` in one extract and
   `Datenbank: Redis` in another is probably not a contradiction, and first-wins
   throws one away. The merge cannot tell a repeated field from a scalar one and
   deliberately holds no knowledge of types, so this stays first-wins until a
   real extract shows which columns need a union.

4d. **Are the source CSVs committed?** (SPEC §12.8) — they are the evidence
   behind every project file, but may carry contact data, and
   `_import_manifest.yaml` already records each sha256. Currently gitignored.
   Decide before the first real extract: removing a file from git history
   afterwards is a rewrite, not a delete.
5. **`placement` shape** (§12.4) — no longer blocking: both shapes normalise
   into environment rows, and the site × OS cross-tab counts environments.
6. ~~**Snapshot retention**~~ — answered: nothing is kept between runs.
