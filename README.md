# Migration catalogue

Git is the system of record: one YAML file per project, flat in `projects/`.
Everything derived — snapshots, the report model, the five output formats — is
rebuilt from those files and never hand-maintained.

`HANDOFF.md` is the design document. This README is the operating manual.

**Status: M1 (validation) and M2 (snapshot + model) complete.** M3–M6 are not built yet.

## Requirements

Podman (preferred) or Docker. Nothing else — no Python, no pip on the host.
Every tool runs in a container.

## Quick start

```bash
./verify.sh                         # everything: shellcheck, schema self-test, tests, exit codes
./check.sh tests/fixtures/projects  # validate the fixtures
./check.sh --check-schema           # validate the schema and reference files
./check.sh                          # validate projects/ - see below
./snapshot.sh                       # projects/ -> out/tables/*.jsonl
./report.sh                         # snapshot + model -> out/reports/<date>/
./shell.sh                          # interactive shell in the pipeline image
```

The image builds itself on first use.

**`projects/` does not exist yet on a fresh checkout**, so a bare `./check.sh`
exits 2 and tells you so. It is not part of the repo skeleton: it is produced
by the importer from the legacy spreadsheet, and the spreadsheet plus a
hand-edited `import/mapping.yaml` have to exist first (see
`tools/README-import.md`). Until then, validate `tests/fixtures/projects`.

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
podman run -d --name mig-dev --network=none -v "$PWD:/work:z" \
    -w /work mig-pipeline:0.1.0 sleep infinity
```

## The pipeline

```
projects/**/*.yaml
      |  snapshot.py        five tables, one row per (project, child)
      v
out/tables/*.jsonl  ->  DuckDB  ->  out/reports/<date>/report_model.json
                                       |  renderers (M3-M5)  lay out; never compute
                                       v
                       report.xlsx  report.pdf  deck.pptx  *.png  report.txt
```

`report_model.json` is the only thing renderers read. If a renderer needs a
number that is not in the model, the number goes in the model (HANDOFF 11).

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
(HANDOFF 11). Two rules make that true:

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
required. That is deliberate (HANDOFF 2): a team that cannot submit a
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

## Deviations from HANDOFF.md

Recorded so they read as decisions rather than drift.

Bash entry points instead of a Makefile, podman-first, and compose off the
daily path were deviations at first; HANDOFF.md §6.5 and §8.3 have since been
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
- **Dates are normalised before structural validation.** A bare YAML date
  (`2026-09-14`) resolves to a date object and would fail `type: string`.
  Versions get no such treatment: an unquoted `7.9` must fail loudly, because
  it has already lost precision.

## Open questions blocking later milestones

From HANDOFF §12, still unanswered:

1. **Taxonomy labels** (blocks M5). `CA-1..3`, `CB-1..4`, `S1..S4` and the tier
   codes have no labels; `taxonomy.yaml` carries `null` rather than invented
   words, and `--check-schema` warns about every one until they are filled in.
2. **`schema/sites.yaml` and `schema/teams.yaml` are provisional**, seeded from
   the fixtures. Anything missing from them will fail real data. Both are
   flagged by `--check-schema`.
3. **Corporate `.potx`** (blocks M5).
4. **Report scoping** — one deck, or one per team (changes whether
   `build_model.py` runs once or per team).
5. **`placement` shape** (§12.4) — decide before the site × OS cross-tab in M2.
6. **Snapshot retention** — affects whether the Parquet layer needs partitioning.
