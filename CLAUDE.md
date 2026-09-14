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
- Do not modify tools/import_xlsx.py unless it has a bug.
- If §12 (open questions) blocks you, ask. Do not invent taxonomy labels.
