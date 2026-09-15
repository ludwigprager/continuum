# SPEC: legacy-to-cloud-native project catalogue & reporting pipeline

You are picking up a project mid-build.

**Done: M1 (validation), M2 (tables + report model), M3 (Excel) and M4
(TXT + PNG).** `./check.sh` and `./report.sh` work end to end against
`projects/` with no network access, under podman or docker, and produce
`report.xlsx`, `report.txt` and the chart PNGs. What remains is two of the
five renderers (M5) and the offline bundle (M6). See 10 for the state of
each.

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

## 3. What already exists

`import_xlsx.py` — a standalone, tested importer that converts the legacy
"one row per project" spreadsheet into per-project YAML. Dependencies: `openpyxl`,
`PyYAML`. No network access. See `README.md` for full documentation.

```bash
python import_xlsx.py profile        projekte.xlsx --out import/
python import_xlsx.py convert        projekte.xlsx --config import/ --out projects/
python import_xlsx.py derive-schema  projekte.xlsx --config import/ --out schema/project.schema.json
```

**What it guarantees, and what you can rely on:**

- Every project file has `schema_version`, `id`, and a `_meta` block.
- Unmapped spreadsheet columns land in `_unmapped` verbatim. Your validator must
  allow `_unmapped` with arbitrary content.
- `_meta.confidence` is one of `verified | estimated | imported | unknown`.
  Everything an import produces is `imported`, meaning **nobody has looked at it**.
- Re-import is idempotent: unchanged files are not rewritten, ids are stable via
  `import/id_map.csv`, and `_meta.last_reviewed` / `reviewed_by` survive.
- `projects/_import_manifest.yaml` holds the volatile provenance (source file
  sha256, import date). Do not treat it as a project file — skip any file
  starting with `_`.

**Do not modify `import_xlsx.py`** unless a bug is found. If you do, keep
`make_testdata.py` passing.

## 4. Repository layout to create

```
.
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
│   ├── import_xlsx.py           # EXISTS
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
├── import.sh
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
tools/validate.py     [--projects DIR] [--schema DIR] [--format text|json] [--strict]
tools/snapshot.py     [--projects DIR] [--schema DIR] [--out out/tables/] [--as-of DATE]
tools/build_model.py  [--snapshot out/tables/] [--spec reports/daily.yaml] [--schema DIR]
                      --out out/reports/<date>/report_model.json [--as-of DATE]
                      [--generated-at ISO]
tools/render/*.py     --model MODEL.json --out DIR [--lang de|en]
```

Exit codes: `0` ok, `1` validation errors (invalid data), `2` tool/usage error.
Warnings never change the exit code unless `--strict`.

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

Bash scripts, not a Makefile. One script per job, at the repository root so
that `ls` shows you what can be run:

```
./import.sh      # profile + convert from the legacy sheet
./check.sh       # validate.py, exits non-zero on invalid data
./snapshot.sh    # flatten projects/ into out/tables/*.jsonl
./report.sh      # full pipeline -> out/reports/<date>/
./verify.sh      # tests + a --network=none pipeline run against tests/fixtures
./shell.sh       # interactive shell in the pipeline image
```

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

**`Dockerfile.import`** — same base, only `openpyxl` and `pyyaml`. Kept separate
because the import runs rarely and does not need the renderer stack.

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
  import:    # profile + convert from the legacy sheet
  check:     # validate.py
  report:    # full pipeline: snapshot -> model -> renderers
  shell:     # interactive, for debugging
  serve:     # profile: serve  - nginx:alpine over ./out/reports, read-only
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
  everywhere because there are no cached values.
- **Pin renderer versions tightly.** Typst and matplotlib both shift layout
  between minor versions, which breaks byte-level reproducibility of the PDFs.

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

**M5 — PDF and PPTX.**

**Not a milestone of its own, and done:** `import.sh` and
`docker/Dockerfile.import`. The importer and `make_testdata.py` now run in a
container like everything else. `import_xlsx.py` gained one line:
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

1. **Taxonomy semantics. STILL OPEN — top blocker for M5.** CA-1..3, CB-1..4,
   S1..S4 and the tier codes are opaque outside the room they were invented in.
   `taxonomy.yaml` carries `label_de: null` for each rather than invented words,
   and `--check-schema` warns about every one until they are filled in. Charts
   would otherwise be labelled with bare codes.
2. **Corporate PowerPoint template.** Needed as `.potx` for M5. Until it arrives,
   the placeholder mapping stays in one dict.
3. **Report scoping.** One deck for everyone, or a per-team deck as well? Affects
   whether `build_model.py` runs once or once per team.
4. **`placement`: single datacenter or multiple environments? STILL OPEN, but
   no longer blocking.** Both shapes validate, and `snapshot.py` normalises
   them into environment rows — a flat `datacenter` scalar becomes one
   environment with no name, because the legacy sheet has nothing to derive a
   name from. A file using both at once gets a plausibility warning. This is
   recorded in `definitions.yaml` because it changes reported numbers: the
   site × OS cross-tab counts environments, so the choice affects the totals.
5. ~~**Retention.** How many daily snapshots are kept before rolling up?~~
   **Answered:** none. There is no snapshot history, no trend reporting and
   no day-over-day comparison. `out/tables/` is the current run and nothing
   else is kept. Reports under `out/reports/<date>/` are the deliverable and
   are not pruned.
