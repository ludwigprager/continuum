#!/usr/bin/env bash
# Merge the CSV extracts in merge/input/ into one merged.csv. SPEC 3.1, 5.4.
#
#   ./merge/merge.sh --key "Projekt-Nr"
#   ./merge/merge.sh --key "Projekt-Nr" --sources merge/tests/fixtures/input
#
# Step 1 of the import, and self-contained: the tool, its tests, its fixtures
# and its documentation all live in this directory. See merge/README.md.
# Step 2 is ../import/import.sh.
#
# Takes no filenames. EVERY *.csv in merge/input/ is a source - that directory
# holds nothing else, which is why there is no rule to remember - and sorted
# filename order is the precedence, so `ls merge/input/` shows which file wins
# a contest. --key names the column that identifies a project and is required.
#
# Writes one level up from the sources, here:
#   merged.csv             one row per project, the thing to inspect
#   merge_conflicts.csv    every value the merge discarded - the only record
#   merge_manifest.json    the sources and their sha256, for step 2
#
# It does NOT copy anything into import/. The last line of output says which
# command to run once you have read the result; until you run it, step 2 keeps
# reading whatever it was reading before.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../scripts/lib.sh
source "$(dirname "$0")/../scripts/lib.sh"

# The import image (SPEC 8.1). merge_csv.py is stdlib-only and would run in
# either, but merging is part of importing and the import image is the one
# that gets carried in for it.
use_import_image

case "${1:-}" in
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
esac

# --rw: it writes its three outputs into this directory.
run_in_container --rw -- python3 merge/merge_csv.py "$@"
