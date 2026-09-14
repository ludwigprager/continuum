#!/usr/bin/env bash
# Everything that must pass before a commit is worth pushing.
# Runs with --network=none, which is the cheapest regression test for the
# air-gap traps in HANDOFF 8.2.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/scripts/lib.sh"

fail=0
step() { printf '\n== %s ==\n' "$1"; }

step "shellcheck"
run_in_container -- shellcheck scripts/lib.sh ./*.sh || fail=1

step "schema self-test"
run_in_container -- python3 tools/validate.py --schema schema --check-schema || fail=1

step "pytest"
run_in_container -- python3 -m pytest tests -q -p no:cacheprovider || fail=1

step "full pipeline against fixtures, --network=none"
# HANDOFF 8.2: the cheapest regression test for the air-gap traps is running
# the whole thing with no network at all. This is that job, run locally.
PIN="2026-09-14T04:00:00+02:00"
if "$REPO_ROOT/report.sh" --projects tests/fixtures/projects --date fixtures \
        --generated-at "$PIN" >/dev/null 2>&1; then
    a=$(sha256sum "$REPO_ROOT/out/reports/fixtures/report_model.json" | cut -d' ' -f1)
    "$REPO_ROOT/report.sh" --projects tests/fixtures/projects --date fixtures \
        --generated-at "$PIN" >/dev/null 2>&1
    b=$(sha256sum "$REPO_ROOT/out/reports/fixtures/report_model.json" | cut -d' ' -f1)
    if [ "$a" = "$b" ]; then
        printf '  ok   report_model.json byte-identical across runs (%s)\n' "${a:0:16}"
    else
        printf '  FAIL report_model.json differs between runs\n'; fail=1
    fi
else
    printf '  FAIL pipeline did not complete\n'; fail=1
fi

step "exit-code contract, under every engine"
# These run on the host, not in the container: they are testing the wrapper.
# The same command under both engines must agree, or the handover breaks the
# first time someone runs it on the other one.
expect() {
    local want="$1" label="$2"; shift 2
    local out rc
    out=$("$@" 2>&1) && rc=0 || rc=$?
    if [ "$rc" = "$want" ]; then
        printf '  ok   %-28s exit %s\n' "$label" "$rc"
    else
        printf '  FAIL %-28s exit %s, expected %s\n%s\n' "$label" "$rc" "$want" "$out"
        fail=1
    fi
}

for engine in podman docker; do
    if ! command -v "$engine" >/dev/null 2>&1; then
        printf '%s: not installed, skipped\n' "$engine"
        continue
    fi
    printf '%s:\n' "$engine"
    export CONTAINER_ENGINE="$engine"
    expect 0 "valid fixtures"      "$REPO_ROOT/check.sh" tests/fixtures/projects
    expect 1 "invalid fixtures"    "$REPO_ROOT/check.sh" tests/fixtures/invalid/unknown-enum
    expect 1 "warnings + --strict" "$REPO_ROOT/check.sh" tests/fixtures/projects --strict
    expect 2 "missing directory"   "$REPO_ROOT/check.sh" does/not/exist
    # A runtime failure must never be reported as a data verdict.
    expect 2 "runtime failure"     "$REPO_ROOT/shell.sh" this-command-does-not-exist
    unset CONTAINER_ENGINE
done

printf '\n'
[ "$fail" = "0" ] && printf 'verify: OK\n' || printf 'verify: FAILED\n'
exit "$fail"
