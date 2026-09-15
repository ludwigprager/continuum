#!/usr/bin/env bash
# Everything that must pass before a commit is worth pushing.
# Runs with --network=none, which is the cheapest regression test for the
# air-gap traps in SPEC 8.2.
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
# SPEC 8.2: the cheapest regression test for the air-gap traps is running
# the whole thing with no network at all. This is that job, run locally.
PIN="2026-09-14T04:00:00+02:00"
REPORT_DIR="$REPO_ROOT/out/reports/fixtures"
# Everything the run produced, hashed before and after a second run. The
# whole directory rather than a list: a chart added to reports/daily.yaml is
# a config change (SPEC 6.3) and must not need an edit here to be covered,
# and a file that stops being written has to fail this too.
hash_report_dir() { (cd "$REPORT_DIR" && sha256sum ./*); }
rm -rf "$REPORT_DIR"
if "$REPO_ROOT/report.sh" --projects tests/fixtures/projects --date fixtures \
        --generated-at "$PIN" >/dev/null 2>&1; then
    before=$(hash_report_dir)
    "$REPO_ROOT/report.sh" --projects tests/fixtures/projects --date fixtures \
        --generated-at "$PIN" >/dev/null 2>&1
    after=$(hash_report_dir)
    if [ "$before" = "$after" ]; then
        while read -r _ artifact; do
            printf '  ok   %-20s byte-identical across runs\n' "${artifact#./}"
        done <<< "$before"
    else
        printf '  FAIL an artifact differs between runs\n%s\n%s\n' \
            "$before" "$after"
        fail=1
    fi
else
    printf '  FAIL pipeline did not complete\n'; fail=1
fi

step "report server"
# ./serve.sh is the only thing here that listens on a network, so it is the
# only thing a browser can be pointed at - and the only step in this file that
# is allowed a network at all. Its own name and port, so a server somebody is
# already using is not torn down by running the tests.
SERVE_PORT=8099
export SERVE_CONTAINER="continuum-serve-verify"
export SERVE_PORT
serve_probe() {
    local url="http://127.0.0.1:${SERVE_PORT}/"
    "$REPO_ROOT/serve.sh" --detach --port "$SERVE_PORT" --bind 127.0.0.1 \
        --root out/reports >/dev/null 2>&1 || return 1
    # The container needs a moment to bind the port; poll rather than sleep a
    # guessed amount, so a slow machine does not produce a flaky failure.
    local body=""
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        body="$(curl -fsS --max-time 2 "$url" 2>/dev/null)" && break
        sleep 0.3
    done
    printf '%s' "$body"
}
if [ -d "$REPO_ROOT/out/reports" ]; then
    listing="$(serve_probe)"
    if printf '%s' "$listing" | grep -q "Directory listing"; then
        printf '  ok   serves a directory listing on port %s\n' "$SERVE_PORT"
    else
        printf '  FAIL no directory listing from the report server\n%s\n' "$listing"
        fail=1
    fi
    "$REPO_ROOT/serve.sh" --stop >/dev/null 2>&1 || true
else
    printf '  skip no out/reports yet\n'
fi
unset SERVE_CONTAINER SERVE_PORT

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
