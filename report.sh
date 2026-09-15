#!/usr/bin/env bash
# Full pipeline: snapshot -> model -> renderers, into out/reports/<date>/.
#
#   ./report.sh                        # today, from projects/
#   ./report.sh --projects tests/fixtures/projects
#   ./report.sh --date 2026-09-13
#   ./report.sh --generated-at 2026-09-14T04:00:00+02:00   # pin for reproducibility
#   ./report.sh --lang en                 # English labels
#
# Reports stay dated: they are the deliverable and people keep them. The
# intermediate tables do not - there is one current generation and one
# previous, and git is the history.
#
# The renderers read report_model.json and nothing else. M3 shipped the Excel,
# M4 the charts and the txt; pdf and pptx follow in M5.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/scripts/lib.sh"

PROJECTS="projects"
DATE="$(date +%F)"
GENERATED_AT=""
LANG_CODE="de"
TABLES="out/tables"
while [ $# -gt 0 ]; do
    case "$1" in
        --projects)     PROJECTS="$2"; shift 2 ;;
        --date)         DATE="$2"; shift 2 ;;
        --generated-at) GENERATED_AT="$2"; shift 2 ;;
        --lang)         LANG_CODE="$2"; shift 2 ;;
        -h|--help)      sed -n '2,10p' "$0"; exit 0 ;;
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

# Renderers, in order. charts.py runs first: everything else embeds its PNGs
# rather than drawing its own, which is what keeps five outputs showing one
# set of numbers (SPEC 2).
for renderer in charts xlsx txt; do
    run_in_container --rw -- python3 "tools/render/$renderer.py" \
        --model "out/reports/$DATE/report_model.json" \
        --out "out/reports/$DATE" \
        --lang "$LANG_CODE"
done
