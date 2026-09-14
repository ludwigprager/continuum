#!/usr/bin/env bash
# Pre-commit hook. Validates only what is staged, through the same code path
# as check.sh and CI (SPEC 6.1: one code path, three call sites).
#
# Install:  ln -sf ../../scripts/precommit.sh .git/hooks/pre-commit
# Skip once: git commit --no-verify
#
# Set USE_WARM_CONTAINER=1 and keep a `mig-dev` container running to avoid
# paying container startup on every commit.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

mapfile -t staged < <(git diff --cached --name-only --diff-filter=ACM \
    | grep -E '^projects/.*\.yaml$' || true)

if [ "${#staged[@]}" -eq 0 ]; then
    exit 0
fi

printf 'validating %d staged project file(s)\n' "${#staged[@]}"
if ! ./check.sh projects; then
    printf '\ncommit blocked by validation errors. `git commit --no-verify` to override.\n' >&2
    exit 1
fi
