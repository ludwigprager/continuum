#!/usr/bin/env bash
# Validate the project files. Exits 0 (ok), 1 (invalid data), 2 (tool error).
#
#   ./check.sh                              # validate projects/
#   ./check.sh tests/fixtures/projects      # validate somewhere else
#   ./check.sh --format json                # machine-readable, for CI and hooks
#   ./check.sh --strict                     # warnings also fail
#   ./check.sh --check-schema               # validate the schema itself
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/scripts/lib.sh"

PROJECTS=""
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --format|--schema) ARGS+=("$1" "$2"); shift 2 ;;
        --strict|--check-schema) ARGS+=("$1"); shift ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        -*) ARGS+=("$1"); shift ;;
        *)  PROJECTS="$1"; shift ;;
    esac
done
[ -n "$PROJECTS" ] || PROJECTS="projects"

run_in_container -- python3 tools/validate.py \
    --projects "$PROJECTS" --schema schema "${ARGS[@]+"${ARGS[@]}"}"
