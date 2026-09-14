#!/usr/bin/env bash
# Import the legacy spreadsheet into projects/. Runs in the import image,
# which carries openpyxl and PyYAML and nothing else (HANDOFF 8.1).
#
#   ./import.sh testdata                 # generate a synthetic projekte.xlsx
#   ./import.sh profile  projekte.xlsx   # -> import/profile.md, mapping.yaml, value_map.yaml
#   ./import.sh convert  projekte.xlsx   # -> projects/*.yaml   (edit the mapping first)
#   ./import.sh schema   projekte.xlsx   # -> schema/derived.schema.json (a starting point)
#
# The manual step between profile and convert is the point of the tool: read
# import/profile.md, edit import/mapping.yaml and import/value_map.yaml. See
# tools/README-import.md.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/scripts/lib.sh"
use_import_image

usage() { sed -n '2,13p' "$0"; }

[ $# -ge 1 ] || { usage; exit 2; }
CMD="$1"; shift

case "$CMD" in
    testdata)
        run_in_container --rw -- python3 tools/make_testdata.py "$@"
        ;;
    profile)
        [ $# -ge 1 ] || die "import.sh profile: need the spreadsheet path"
        run_in_container --rw -- python3 tools/import_xlsx.py profile "$@" --out import/
        ;;
    convert)
        [ $# -ge 1 ] || die "import.sh convert: need the spreadsheet path"
        # projects/ is flat: one file per project, the owning team is
        # ownership.team_id inside it (HANDOFF 2). --group-column none stops
        # the importer auto-detecting a Team column and recreating per-team
        # directories.
        run_in_container --rw -- python3 tools/import_xlsx.py convert "$@" \
            --config import/ --out projects/ --group-column none
        ;;
    schema)
        [ $# -ge 1 ] || die "import.sh schema: need the spreadsheet path"
        run_in_container --rw -- python3 tools/import_xlsx.py derive-schema "$@" \
            --config import/ --out schema/derived.schema.json
        ;;
    -h|--help) usage ;;
    *) die "import.sh: unknown command '$CMD' (testdata, profile, convert, schema)" ;;
esac
