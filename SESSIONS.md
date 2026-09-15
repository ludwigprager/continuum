# Running the Claude Code sessions

`SPEC.md` is the spec. This is the operating manual for the human driving
the build.

**This file is for you, not for the agent.** It is deliberately not named
`CLAUDE.md` or `AGENTS.md`, which are auto-loaded as instructions *to* the
model — "start in plan mode" read as a directive to itself is the wrong
outcome.

## Setup

Don't start from an empty directory containing only the spec. Claude Code
works far better with existing code and a git history to commit into.

```bash
mkdir beep-catalog && cd beep-catalog
git init

# the work so far
mkdir -p tools templates schema reports tests/fixtures docker
cp ~/Downloads/import_csv.py tools/
cp ~/Downloads/make_testdata.py tools/
cp ~/Downloads/README.md import/README-xlsx-legacy.md
cp ~/Downloads/SPEC.md .

git add -A && git commit -m "importer + handoff"
```

The commit matters. Claude Code makes large multi-file changes and you want
`git diff` to be meaningful at every step.

### CLAUDE.md is the piece that makes it stick

`SPEC.md` is just a file. Claude Code won't read it unless told, and it
won't survive a `/clear`. `CLAUDE.md` is loaded into every session
automatically, so the pointer goes there:

```bash
cat > CLAUDE.md <<'EOF'
# Project: legacy-to-cloud-native catalogue & reporting

@SPEC.md is the spec for this repo. Read it before any task.
The Contracts (§5), Do not (§11) and Gotchas (§9) sections are binding.

Working agreement:
- Build one milestone at a time (§10). Do not start the next one unasked.
- Every tool runs in a container, invoked through the bash entry points at the
  repository root (`./check.sh`, `./report.sh`, ...). There is no Makefile.
  All container knowledge lives in `scripts/lib.sh` and nowhere else.
- Podman is the default engine, docker the fallback; `$CONTAINER_ENGINE` overrides.
  `./report.sh` must work on a clean checkout with only a container engine installed.
- Do not modify tools/import_csv.py or merge/merge_csv.py unless they have a bug.
- If §12 (open questions) blocks you, ask. Do not invent taxonomy labels.
EOF

git add CLAUDE.md && git commit -m "CLAUDE.md"
```

The `@SPEC.md` syntax imports the file, so its content is in context every
session without pasting it.

### Permissions

Add to `.claude/settings.json` so it isn't asking you to approve every
container run. Podman is the default engine, docker the fallback:

```json
{
  "permissions": {
    "allow": [
      "Bash(podman:*)",
      "Bash(docker:*)",
      "Bash(./check.sh:*)",
      "Bash(./verify.sh:*)",
      "Bash(./report.sh:*)",
      "Bash(./snapshot.sh:*)",
      "Bash(./import/import.sh:*)"
    ]
  }
}
```

There is no Makefile — the entry points are the `*.sh` scripts at the repo
root (SPEC §6.5).

### Real data to build against

`merge/make_testdata.py` generates several synthetic legacy CSV extracts — "make" as
in *create*, nothing to do with GNU make. They deliberately overlap, contradict
each other and leave gaps, which is what the merge exists to resolve. Run the
importer on them to produce an actual `projects/` tree, so Claude Code builds
against real data rather than fixtures it invented:

```bash
./shell.sh python3 merge/make_testdata.py   # -> merge/input/*.csv
./merge/merge.sh --key "Projekt-Nr"   # -> merge/merged.csv + merge_conflicts.csv
# LOOK AT merge/merged.csv, and at what the merge threw away
cp merge/merged.csv import/merged.csv # the merge will not do this for you
./import/import.sh profile             # -> import/{profile.md,mapping.yaml,value_map.yaml}
# read profile.md, then edit import/mapping.yaml and import/value_map.yaml
./import/import.sh convert             # -> projects/*.yaml
./check.sh                      # validate what came out
```

Three manual steps, all deliberate: inspecting `merged.csv`, copying it across, and
editing the mapping between `profile` and `convert`. See SPEC §3 and
`import/README-xlsx-legacy.md`.

**`convert` writes into `projects/`.** Existing values win over the CSV
(SPEC §5.4.2), so it will not overwrite work — but to see what a new extract
*would* say, send it elsewhere (`--out`) and diff.

Everything runs in a container. The importer has its own image because it needs
`PyYAML`, which the pipeline image deliberately does not carry — `ruamel.yaml`
is the only YAML library there. There is **no xlsx import**: the extracts arrive
as CSV, so the import image has no `openpyxl` (SPEC §3). xlsx is an output
format only, written by the pipeline image.

## First session

Start in plan mode so it doesn't immediately write 2000 lines:

```bash
claude
```

Then:

```text
Read SPEC.md. Don't write any code yet. Tell me: what's ambiguous or
underspecified, what you'd need to decide that isn't written down, and
anything in the spec you think is wrong. Then propose a concrete plan for M1
only.
```

Worth the extra turn. It surfaces where the spec was vague, and it catches a
misread of the model contract before that's baked into five renderers.

## Then one milestone per session

Fresh context each time (`/clear` between) — a session that has already built
three milestones pattern-matches instead of reading the spec.

```text
Implement M<N> from SPEC.md.

Read SPEC.md first — §5 (contracts), §9 (gotchas) and §11 (do not) are
binding, and §6.1.1 means no field names, codes or enum values in code:
everything comes from schema/project.schema.yaml annotations.

Acceptance: <the §10 acceptance text for that milestone, verbatim>.
./verify.sh must stay green under both podman and docker.

Do not start the next milestone. If §12 blocks you, ask — do not invent
taxonomy labels.

Show me it actually running before you say it's done.
```

**"Show me it actually running" does real work.** Without it you get code that
looks right and has never been executed.

**Name the milestone, don't paraphrase it.** Let it read §10 itself. A prompt
that described M1's contents but was labelled M2 nearly produced the wrong
milestone.

One caveat on `./verify.sh must stay green`: a model told to keep tests passing
is tempted to weaken an assertion rather than fix the code. If that worries
you, add: *"if a test fails, say whether the code or the test was wrong before
changing either."*

### Between milestones

```bash
git diff --stat
./verify.sh                 # tests, shellcheck, --network=none pipeline, both engines
git commit -m "M1: validator"
```

`./check.sh` alone validates the data; `./verify.sh` is the full gate and is
what CI runs.

## The prompt that matters most

M6 is the one it will otherwise fake:

```text
Implement M6. The --network=none pipeline run is the acceptance test —
actually run it and show me the output. If any of the traps in §8.2 fire,
fix them in the Dockerfile, not by relaxing the network restriction.
```

Don't wait for M6 to build that check. It is already in `./verify.sh`, and it
stops every air-gap trap at the moment it is introduced rather than six
milestones later. Three have fired so far and were caught there: DuckDB
attempting to download extensions, `enable_external_access=false` turning out
to block local file reads as well as the network, and — in M4, as predicted —
matplotlib's font cache. The third had a sting in the tail: baking the cache
into the image is not enough, because matplotlib refuses a read-only
`MPLCONFIGDIR`, says so in a warning nobody reads, and rebuilds the cache in a
temp directory anyway.

M5 added a fourth and a fifth. The predicted one - Typst fetching `@preview`
packages - was cheap, because the template simply imports nothing and a test
says so. The unpredicted one was Typst's fonts, and it is the same shape as
matplotlib's: `typst compile` **warns** about an unknown family, substitutes,
writes the PDF and exits 0. `pdf.py` turns that warning into a failure and
deletes the PDF Typst had already written.

## Where things stand

| milestone | state |
|---|---|
| M1 validation | done |
| M2 tables + model | done |
| M3 Excel | done |
| M4 TXT + PNG | done |
| M5 PDF + PPTX | done |
| M6 offline bundle | `--network=none` already enforced by `./verify.sh` |
| import (CSV) step 1, merge | done |
| import (CSV) step 2, convert | **current work** |

`import.sh` and `docker/Dockerfile.import` run in a container and that part
stands, but the importer itself is being replaced. The source data turned out
to be several overlapping CSV extracts rather than one spreadsheet, so the xlsx
path is gone and the import is now two steps with a directory each.
`./merge/merge.sh` reduces every CSV in `merge/input/` to one `merged.csv` on a join
key, first-wins per cell, and prints the `cp` that hands it to step 2 — that
step is **built** and self-contained in `merge/`, with its own README and 21
tests. `import_csv.py`, which
converts that single file, is **not**: `./import/import.sh profile|convert` still runs
the xlsx importer until it is. SPEC §3 is the design, §5.4 the merge contract,
§10 the acceptance for each step.

Open questions are in SPEC §12. **§12.1** is still live: the taxonomy codes
have no labels, `taxonomy.yaml` carries `label_de: null` rather than invented
words, and `./check.sh --check-schema` warns about every one until they are
filled in. It turned out not to block M5 - M4 had already settled the answer,
which is that a code with no label prints as the bare code - but it is now
visible in all five outputs rather than just the charts, so it is worth
chasing. **§12.2**, the corporate `.potx`, did not block M5 either: the deck
is built against a generated placeholder and swapping the real one in is an
edit to one dict.
