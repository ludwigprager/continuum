#!/usr/bin/env bash
# Full pipeline: snapshot -> model -> renderers, into out/reports/<date>/.
#
#   ./report.sh                        # today, from projects/
#   ./report.sh --projects tests/fixtures/projects
#   ./report.sh --date 2026-09-13
#   ./report.sh --generated-at 2026-09-14T04:00:00+02:00   # pin for reproducibility
#
# Reports stay dated: they are the deliverable and people keep them. The
# intermediate tables do not - there is one current generation and one
# previous, and git is the history.
#
# Renderers arrive in M3-M5; today this produces report_model.json.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/scripts/lib.sh"

PROJECTS="projects"
DATE="$(date +%F)"
GENERATED_AT=""
TABLES="out/tables"
while [ $# -gt 0 ]; do
    case "$1" in
        --projects)     PROJECTS="$2"; shift 2 ;;
        --date)         DATE="$2"; shift 2 ;;
        --generated-at) GENERATED_AT="$2"; shift 2 ;;
        -h|--help)      sed -n '2,9p' "$0"; exit 0 ;;
        *)              die "report.sh: unexpected argument '$1'" ;;
    esac
done

"$REPO_ROOT/snapshot.sh" --projects "$PROJECTS" --date "$DATE" --out "$TABLES"

mkdir -p "$REPO_ROOT/out/reports/$DATE"
run_in_container --rw -- python3 tools/build_model.py \
    --snapshot "$TABLES" \
    --spec reports/daily.yaml \
    --definitions reports/definitions.yaml \
    --schema schema \
    --out "out/reports/$DATE/report_model.json" \
    --as-of "$DATE" \
    ${GENERATED_AT:+--generated-at "$GENERATED_AT"}
