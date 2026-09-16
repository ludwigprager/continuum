#!/usr/bin/env bash
# Step 2 of the import: the one merged CSV -> projects/*.yaml. SPEC 3.1.
# Runs in the import image (SPEC 8.1). See import/README.md.
#
#   ./import/import.sh profile           # scan merged.csv -> profile.md
#   ./import/import.sh convert           # -> projects/*.yaml
#
# convert is a BOOTSTRAP and runs once. It refuses a populated projects/:
# after the first run that directory is the system of record (SPEC 2) and the
# extracts are history.
#
# Step 1 is ./merge/merge.sh, self-contained in merge/ (see merge/README.md):
# it reduces every CSV in merge/input/ to one merged.csv, which you then copy
# here by hand as import/merged.csv. Run it first; it prints the cp command.
# Test extracts come from ./merge/make_testdata.py.
#
# There is no xlsx import: xlsx is an output format only. The reader takes a
# CSV and refuses a .xlsx by name, saying so.
#
#   ./import/import.sh profile import/merged.csv   # -> profile.md + starters
#   ./import/import.sh convert import/merged.csv   # -> projects/*.yaml
#   ./import/import.sh schema  import/merged.csv   # -> a derived JSON Schema
#
# The manual step between profile and convert is the point of the tool, and the
# only one: read import/profile.md, edit import/mapping.yaml and
# import/value_map.yaml. See import/README.md.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../scripts/lib.sh
source "$(dirname "$0")/../scripts/lib.sh"
use_import_image

usage() { sed -n '2,24p' "$0"; }

# --------------------------------------------------------------------------
# The import is a bootstrap: it runs once, and after it projects/ is the system
# of record (SPEC 2, 3). So convert refuses to write into a populated one.
#
# There is no --update. Merging an extract into an existing catalogue was
# considered and dropped: a new project arrives as YAML from the team that owns
# it, not from another dump of a legacy system that is being switched off. And
# a convert whose result depended on what was already on disk would be the one
# thing in this pipeline that is not a pure function of its inputs - everything
# else rebuilds identically from nothing.
#
# Redoing the bootstrap is two commands on purpose. git is the undo.
# --------------------------------------------------------------------------
# Splits the caller's arguments into OUT (where the catalogue goes) and ARGS
# (everything else, passed through).
#
# --out has to be parsed rather than appended: this script used to add
# `--out projects/` AFTER "$@", so a caller's own --out was silently overridden
# by argparse taking the last one - which made "write it somewhere else and
# diff" write to projects/ anyway. Exactly the accident the guard exists to
# prevent, caused by the guard's own advice.
OUT="projects"
ARGS=()
parse_convert_args() {
    OUT="projects"; ARGS=()
    local want_out=0 arg
    for arg in "$@"; do
        if [ "$want_out" = 1 ]; then OUT="$arg"; want_out=0; continue; fi
        case "$arg" in
            --out)      want_out=1 ;;
            --out=*)    OUT="${arg#--out=}" ;;
            # This flag existed briefly. Say so, rather than letting it reach
            # the importer and come back as "unrecognized arguments".
            --update)   die "import.sh convert: --update is gone. The import is a bootstrap and runs once (SPEC 5.4.2); clear $OUT/ to redo it." ;;
            *)          ARGS+=("$arg") ;;
        esac
    done
    [ "$want_out" = 0 ] || die "import.sh convert: --out needs a directory"
}

guard_existing_catalogue() {
    [ -d "$REPO_ROOT/$OUT" ] || return 0

    local count
    count="$(find "$REPO_ROOT/$OUT" -maxdepth 1 -name '*.yaml' ! -name '_*' | wc -l)"
    [ "$count" -gt 0 ] || return 0

    cat >&2 <<MSG
error: $OUT/ already holds $count project file(s).

  The import is a bootstrap. Once $OUT/ exists it is the system of record
  (SPEC 2) and the extracts are history - a new project arrives as YAML from
  the team that owns it, not from another dump.

  To redo the bootstrap, clear it first. git is how you get it back:

      rm $OUT/*.yaml && ./import/import.sh convert <extract>
      git checkout -- $OUT/          # if that was a mistake

  To see what this extract would say without touching anything:

      ./import/import.sh convert --out out/import-preview
      diff -r $OUT/ out/import-preview

  (out/ is gitignored and inside the repo. A path outside it - /tmp/neu - is
  written inside the container and is gone when it exits.)
MSG
    exit 1
}

[ $# -ge 1 ] || { usage; exit 2; }
CMD="$1"; shift

case "$CMD" in
    profile)
        [ $# -ge 1 ] || die "import.sh profile: need the CSV path (normally import/merged.csv)"
        run_in_container --rw -- python3 import/import_csv.py profile "$@" --out import/
        ;;
    convert)
        [ $# -ge 1 ] || die "import.sh convert: need the CSV path (normally import/merged.csv)"
        parse_convert_args "$@"
        guard_existing_catalogue
        # projects/ is flat: one file per project, the owning team is
        # ownership.team_id inside it (SPEC 2). --group-column none stops the
        # importer auto-detecting a Team column and recreating per-team
        # directories.
        run_in_container --rw -- python3 import/import_csv.py convert \
            ${ARGS[@]+"${ARGS[@]}"} \
            --config import/ --out "$OUT" --group-column none
        ;;
    schema)
        [ $# -ge 1 ] || die "import.sh schema: need the CSV path (normally import/merged.csv)"
        run_in_container --rw -- python3 import/import_csv.py derive-schema "$@" \
            --config import/ --out schema/derived.schema.json
        ;;
    -h|--help) usage ;;
    testdata)
        die "import.sh: test extracts come from the merge now - run ./merge/make_testdata.py via ./shell.sh, see merge/README.md" ;;
    merge)
        die "import.sh: the merge lives in merge/ - run ./merge/merge.sh --key HEADER" ;;
    *) die "import.sh: unknown command '$CMD' (profile, convert, schema)" ;;
esac
