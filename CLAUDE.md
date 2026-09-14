# Project: legacy-to-cloud-native catalogue & reporting

@HANDOFF.md is the spec for this repo. Read it before any task.
The Contracts (§5), Do not (§11) and Gotchas (§9) sections are binding.

Working agreement:
- Build one milestone at a time (§10). Do not start the next one unasked.
- Every tool runs in a container. `make report` must work with only Docker installed.
- Do not modify tools/import_xlsx.py unless it has a bug.
- If §12 (open questions) blocks you, ask. Do not invent taxonomy labels.
