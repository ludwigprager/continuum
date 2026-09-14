# Running the Claude Code sessions

`HANDOFF.md` is the spec. This is the operating manual for the human driving
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
cp ~/Downloads/import_xlsx.py tools/
cp ~/Downloads/make_testdata.py tools/
cp ~/Downloads/README.md tools/README-import.md
cp ~/Downloads/HANDOFF.md .

git add -A && git commit -m "importer + handoff"
```

The commit matters. Claude Code makes large multi-file changes and you want
`git diff` to be meaningful at every step.

### CLAUDE.md is the piece that makes it stick

`HANDOFF.md` is just a file. Claude Code won't read it unless told, and it
won't survive a `/clear`. `CLAUDE.md` is loaded into every session
automatically, so the pointer goes there:

```bash
cat > CLAUDE.md <<'EOF'
# Project: legacy-to-cloud-native catalogue & reporting

@HANDOFF.md is the spec for this repo. Read it before any task.
The Contracts (§5), Do not (§11) and Gotchas (§9) sections are binding.

Working agreement:
- Build one milestone at a time (§10). Do not start the next one unasked.
- Every tool runs in a container, invoked through the bash entry points at the
  repository root (`./check.sh`, `./report.sh`, ...). There is no Makefile.
  All container knowledge lives in `scripts/lib.sh` and nowhere else.
- Podman is the default engine, docker the fallback; `$CONTAINER_ENGINE` overrides.
  `./report.sh` must work on a clean checkout with only a container engine installed.
- Do not modify tools/import_xlsx.py unless it has a bug.
- If §12 (open questions) blocks you, ask. Do not invent taxonomy labels.
EOF

git add CLAUDE.md && git commit -m "CLAUDE.md"
```

The `@HANDOFF.md` syntax imports the file, so its content is in context every
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
      "Bash(./import.sh:*)"
    ]
  }
}
```

There is no Makefile — the entry points are the `*.sh` scripts at the repo
root (HANDOFF §6.5).

### Real data to build against

`make_testdata.py` generates a synthetic legacy sheet — "make" as in *create*,
nothing to do with GNU make. Run the importer on it to produce an actual
`projects/` tree, so Claude Code builds against real data rather than fixtures
it invented:

```bash
./import.sh testdata                # -> projekte.xlsx (gitignored)
./import.sh profile projekte.xlsx   # -> import/profile.md, mapping.yaml, value_map.yaml
# read import/profile.md, then edit import/mapping.yaml and import/value_map.yaml
./import.sh convert projekte.xlsx   # -> projects/*.yaml
./check.sh                          # validate what came out
```

The edit between `profile` and `convert` is the manual step, and the only one.
See `tools/README-import.md`.

**`convert` writes into `projects/`.** If you already have project files there,
send it somewhere else first (`--out`) and diff, rather than discovering what
it overwrote.

Everything runs in a container. The importer has its own image because it
needs `openpyxl` and `PyYAML`, which the pipeline image deliberately does not
carry.

## First session

Start in plan mode so it doesn't immediately write 2000 lines:

```bash
claude
```

Then:

```text
Read HANDOFF.md. Don't write any code yet. Tell me: what's ambiguous or
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
Implement M<N> from HANDOFF.md.

Read HANDOFF.md first — §5 (contracts), §9 (gotchas) and §11 (do not) are
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
milestones later. Two have fired so far and were caught there: DuckDB
attempting to download extensions, and `enable_external_access=false` turning
out to block local file reads as well as the network. matplotlib's font cache
is still ahead, in M4.

## Where things stand

| milestone | state |
|---|---|
| M1 validation | done |
| M2 tables + model | done |
| M3 Excel | next |
| M4 TXT + PNG | |
| M5 PDF + PPTX | |
| M6 offline bundle | `--network=none` already enforced by `./verify.sh` |

`import.sh` and `docker/Dockerfile.import` are done too, though they are not a
milestone of their own.

Open questions that block later work are in HANDOFF §12. The live one is
**§12.1**: the taxonomy codes have no labels, `taxonomy.yaml` carries
`label_de: null` rather than invented words, and `./check.sh --check-schema`
warns about every one until they are filled in. That blocks M5.
