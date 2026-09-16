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
- The import is CSV only and is two jobs with a directory each (SPEC 3.1):
  ./merge/merge.sh merges every CSV in merge/input/ into merge/merged.csv on a --key
  join column, and a person copies that to import/merged.csv;
  ./import/import.sh converts it. There is no xlsx import. xlsx is an output
  format only. The import is a BOOTSTRAP: convert refuses a populated
  projects/ and there is no merge-into-existing mode (SPEC 5.4.2).
- merge/ is self-contained - code, tests, fixtures and its own README live
  there. Read merge/README.md before touching it, and keep it stdlib-only.
  import/ has its own README.md too. The root README.md points at both rather
  than repeating them.
- Do not modify import/import_csv.py or merge/merge_csv.py unless they have a bug.
- If §12 (open questions) blocks you, ask. Do not invent taxonomy labels.
