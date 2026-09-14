# Migration catalogue

Git is the system of record: one YAML file per project under `projects/<team>/`.
Everything derived — snapshots, the report model, the five output formats — is
rebuilt from those files and never hand-maintained.

`HANDOFF.md` is the design document. This README is the operating manual.

**Status: M1 (validation) complete.** M2–M6 are not built yet.

## Requirements

Podman (preferred) or Docker. Nothing else — no Python, no pip on the host.
Every tool runs in a container.

## Quick start

```bash
./verify.sh                         # everything: shellcheck, schema self-test, tests, exit codes
./check.sh tests/fixtures/projects  # validate the fixtures
./check.sh --check-schema           # validate the schema and reference files
./check.sh                          # validate projects/ - see below
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

## Adding or changing a field

This is the common task and it is deliberately a **configuration change, not a
code change**. `tools/validate.py` contains no field names, no codes and no
enum values; it is driven by annotations in `schema/project.schema.json`:

| annotation | effect |
|---|---|
| `"x-taxonomy": "<group>"` | value must be a code in that `taxonomy.yaml` group |
| `"x-ref": "sites"` \| `"teams"` \| `"projects"` | value must exist in that reference file |
| `"x-tracked": true` | field counts toward the coverage percentage |
| `"x-graph": "<name>"` | the field's values are graph edges, checked for cycles |
| `"x-kind": "<kind>"` | wording of the error message (set on `$defs`, not on fields) |

**Add a coded field** — two files, no code:

```jsonc
// 1. schema/project.schema.json
"operations": {
  "properties": {
    "backup_class": {"$ref": "#/$defs/code", "x-taxonomy": "backup_class", "x-tracked": true}
  }
}
```
```yaml
# 2. schema/taxonomy.yaml
  backup_class:
    order: [gold, silver, none, unknown]
    codes:
      gold:    {label_de: Gold, label_en: Gold, colour: null}
      silver:  {label_de: Silber, label_en: Silver, colour: null}
      none:    {label_de: Keine, label_en: None, colour: null}
      unknown: {label_de: Unbekannt, label_en: Unknown, colour: null}
```

Then `./check.sh --check-schema`. That is the whole change — validation,
referential integrity and coverage all pick it up.
`tests/test_validate.py::test_a_new_coded_field_needs_only_schema_and_taxonomy_edits`
asserts exactly this, including that `validate.py` is byte-identical afterwards.

**Add a code to an existing field**: edit `schema/taxonomy.yaml` only.
**Add a site or team**: edit `schema/sites.yaml` / `schema/teams.yaml` only.

### Always run `--check-schema` after editing the schema

An annotation that is misspelled (`x-taxonmy`) would otherwise be ignored, and
the field would be validated less than you think — silently. That is the worst
failure mode this design has, so it has its own check:

```
$ ./check.sh --check-schema
schema/project.schema.json
  classification.security_class: unknown annotation 'x-taxonmy'
  known: x-doc, x-graph, x-kind, x-ref, x-taxonomy, x-tracked
```

### Pick a `$def` rather than writing types by hand

`$defs` exist so the YAML traps in HANDOFF §9 are solved once:

| `$def` | use for | why |
|---|---|---|
| `code` | any enum-like value | valid values live in `taxonomy.yaml`, never in the schema |
| `version` | every version number | unquoted `7.9` is a float that prints as `7.9000000000000004` |
| `date` | every date | a bare YAML date is accepted and normalised to a string |
| `count` | every number | `0` and "not established" must not be the same value |
| `text` | free text | |

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
projects/team-alpha/payment-gateway.yaml:9:21
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

- **Shell scripts instead of a Makefile** (§6.5 specifies `make check` etc.).
  Requested. Same targets, same containers.
- **The pipeline does not use docker-compose.** `podman compose` in 4.x
  delegates to whichever compose implementation is installed, which is not
  something to put under a pre-commit hook. `check.sh` calls `podman run`
  directly. `docker-compose.yml` is kept for the genuinely long-lived services
  (`serve`, `scheduler`) and for anyone who prefers it.
- **A warm container is allowed for the pre-commit hook.** §8.3 says one-shot
  jobs, and the canonical path still is; `exec` into a warm container is a
  latency optimisation for the hook only and nothing depends on it.
- **Both `placement` shapes validate** — a single `datacenter` scalar and an
  `environments` list. §12.4 is still open; `snapshot.py` normalises in M2.
  A file using both at once gets a warning.
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
