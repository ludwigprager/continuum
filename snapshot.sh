#!/usr/bin/env bash
# Flatten projects/ into the jsonl tables the report reads (out/tables/).
#
#   ./snapshot.sh
#   ./snapshot.sh --projects tests/fixtures/projects
#
# There is no snapshot history and nothing carried between runs: git is the
# history. Delete out/ and the next run rebuilds it identically.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/scripts/lib.sh"

PROJECTS="projects"
DATE="$(date +%F)"
OUT="out/tables"
while [ $# -gt 0 ]; do
    case "$1" in
        --projects) PROJECTS="$2"; shift 2 ;;
        --date)     DATE="$2"; shift 2 ;;
        --out)      OUT="$2"; shift 2 ;;
        -h|--help)  sed -n '2,9p' "$0"; exit 0 ;;
        *)          die "snapshot.sh: unexpected argument '$1'" ;;
    esac
done

# The image has no git and the repo is mounted read-only, so provenance is
# resolved here and passed in. snapshot.py falls back to reading .git itself
# when it is run directly inside the container.
GIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
GIT_TAG="$(git describe --tags --exact-match 2>/dev/null || true)"

mkdir -p "$REPO_ROOT/out"

run_in_container --rw -- python3 tools/snapshot.py \
    --projects "$PROJECTS" \
    --schema schema \
    --out "$OUT" \
    --as-of "$DATE" \
    --git-sha "$GIT_SHA" \
    ${GIT_TAG:+--git-tag "$GIT_TAG"} \
    --image-digest "$(image_digest)"
