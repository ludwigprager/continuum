#!/usr/bin/env bash
# Scan this project's own images with a containerized Trivy, against
# Podman's local image store over its API socket. Full walkthrough and the
# reasoning behind each flag: trivy.md at the repo root.
#
# Deliberately does NOT go through scripts/lib.sh's run_in_container: that
# always mounts the repo read-only at /work and defaults to --network=none,
# neither of which fits scanning an image, and it does not build or touch
# the pipeline/import images this repo ships. This is a separate dev/security
# tool, not one of the entry points in SPEC 6.5.
#
# Usage:
#   tools/trivy-scan.sh                     # scan both project images
#   tools/trivy-scan.sh continuum-pipeline  # scan just one
#   tools/trivy-scan.sh --format table      # human-readable instead of json
#   tools/trivy-scan.sh --gate              # CI gate: fail (exit 1) only on
#                                            # fixable HIGH/CRITICAL CVEs
#   tools/trivy-scan.sh -- --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed
#                                            # extra flags, passed to `trivy image`
#                                            # (--gate is shorthand for exactly this)
#
# --gate exists because "any CRITICAL CVE" is not a gate that can ever pass: a
# base Debian image reliably carries a handful with no fix published yet (this
# repo's own continuum-pipeline image currently does - libsqlite3-0, perl-base,
# zlib1g, all upstream-unfixed, nothing a Dockerfile change here can act on).
# --ignore-unfixed is what makes the gate about vulnerabilities that can
# actually be remediated by rebuilding, rather than blocking forever on ones
# that can't. --gate and a trailing `-- ...` are mutually exclusive - pick one.
#
# Requires the Podman socket:
#   systemctl --user enable --now podman.socket   # rootless (this host)
# Rootful Podman uses /run/podman/podman.sock instead - see trivy.md.
#
# Results land in out/scans/<image>-<version>.<json|txt>, one file per image,
# same as every other run's output in this repo (SPEC 4: out/ is gitignored).
# A log per image lands next to it (<image>-<version>.log), because Trivy's
# INFO/WARN lines otherwise vanish with the terminal.
#
# --format json also gets an out/scans/<image>-<version>.html rendering,
# via `trivy convert` against the json file already on disk - not a second
# scan, and not a hand-rolled JSON->HTML conversion: the trivy image ships
# /contrib/html.tpl (aquasecurity/trivy's own template) for exactly this.
# Browse it with ./serve.sh --root out/scans (SPEC's serve.sh takes any
# directory under the repo, out/reports is only its default).
set -euo pipefail
cd "$(dirname "$0")/.."

TRIVY_IMAGE="${TRIVY_IMAGE:-docker.io/aquasec/trivy:0.74.0}"
V="$(cat VERSION)"
FORMAT="json"
IMAGES=()
EXTRA_ARGS=()
GATE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --format)  FORMAT="$2"; shift 2 ;;
        --gate)    GATE=1; shift ;;
        --)        shift; EXTRA_ARGS+=("$@"); break ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        -*)        echo "tools/trivy-scan.sh: unknown flag $1" >&2; exit 2 ;;
        *)         IMAGES+=("$1"); shift ;;
    esac
done
[ ${#IMAGES[@]} -gt 0 ] || IMAGES=(continuum-pipeline continuum-import)

if [ "$GATE" = 1 ]; then
    [ ${#EXTRA_ARGS[@]} -eq 0 ] \
        || { echo "tools/trivy-scan.sh: --gate already sets --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 - don't combine it with '-- ...'" >&2; exit 2; }
    EXTRA_ARGS=(--severity HIGH,CRITICAL --ignore-unfixed --exit-code 1)
fi

case "$FORMAT" in
    json)  EXT="json" ;;
    table) EXT="txt" ;;
    *) echo "tools/trivy-scan.sh: unsupported --format $FORMAT (use json or table)" >&2
       exit 2 ;;
esac

SOCKET="/run/user/$(id -u)/podman/podman.sock"
if [ ! -S "$SOCKET" ]; then
    echo "tools/trivy-scan.sh: no Podman socket at $SOCKET" >&2
    echo "  systemctl --user enable --now podman.socket" >&2
    echo "(rootful Podman uses /run/podman/podman.sock - see trivy.md)" >&2
    exit 2
fi

mkdir -p out/scans

# A json result can read back with the OS/language target identified but
# `"Vulnerabilities": null` on it - not an empty list, null - immediately
# after the `podman run` that wrote it exits with status 0. This is not
# Trivy getting it wrong: Trivy's own log (saved to $log below) shows it
# genuinely found and scanned the packages every time this has been seen
# ("pkg_num=112", "[python-pkg] Detecting vulnerabilities..."), and re-reading
# the exact same file later, from a separate process, with nothing re-run,
# shows it fully populated - sometimes seconds later, sometimes not within
# a script-internal retry loop that kept re-reading for over a minute. This
# looks like a read-after-write race on the host side specific to this
# sandboxed environment (`--output` writes through the `:z` bind mount from
# inside the container, and rootless Podman's storage - fuse-overlayfs here -
# does not guarantee that write is visible to every reader the instant
# `podman run` returns), not a scan defect, so the check below cross-checks
# the log rather than just failing: if Trivy's own log says it found and
# scanned real packages for a target that still reads back null, that is
# reported as a warning to double-check by hand, not as a failed scan -
# calling it a failure has been wrong every time this was tested. --format
# table has no structure to check this way, so it is not validated.
looks_incomplete() {
    local file="$1"
    command -v jq >/dev/null 2>&1 || {
        echo "tools/trivy-scan.sh: jq not found, cannot validate $file - trusting it" >&2
        return 1
    }
    jq -e '[.Results[]? | select((.Class == "os-pkgs" or .Class == "lang-pkgs") and .Vulnerabilities == null)] | length > 0' \
        "$file" >/dev/null 2>&1
}

# True if Trivy's own log shows it actually found and scanned packages,
# regardless of what the json file currently reads back.
log_shows_real_scan() {
    grep -qE 'pkg_num=[1-9]|Detecting vulnerabilities' "$1" 2>/dev/null
}

rc=0
any_html=0
for image in "${IMAGES[@]}"; do
    ref="localhost/${image}:${V}"
    dest="out/scans/${image}-${V}.${EXT}"
    log="out/scans/${image}-${V}.log"
    echo "scanning $ref -> $dest" >&2
    st=0
    podman run --rm \
        -v "${SOCKET}:/run/podman/podman.sock:z" \
        -v trivy-cache:/root/.cache \
        -v "$(pwd)/out/scans:/out:z" \
        "$TRIVY_IMAGE" \
        image --image-src podman --podman-host /run/podman/podman.sock \
        --format "$FORMAT" --output "/out/${image}-${V}.${EXT}" \
        "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}" \
        "$ref" 2> >(tee "$log" >&2) || st=$?
    if [ "$st" -ne 0 ]; then
        # --gate's --exit-code 1 fires for the same reason a real tool
        # failure would (both exit 1), so exit status alone can't tell them
        # apart. log_shows_real_scan can: it means Trivy got as far as
        # scanning packages, so exit 1 here is the gate result, not a crash -
        # the scan still ran and $dest is still worth keeping and converting.
        if [ "$GATE" = 1 ] && log_shows_real_scan "$log"; then
            echo "tools/trivy-scan.sh: GATE FAILED for $ref - fixable HIGH/CRITICAL vulnerabilities found, see $dest" >&2
            rc=1
        else
            echo "tools/trivy-scan.sh: scan of $ref failed (exit $st)" >&2
            rc=$st
            continue
        fi
    elif [ "$GATE" = 1 ]; then
        echo "tools/trivy-scan.sh: gate OK for $ref - no fixable HIGH/CRITICAL vulnerabilities" >&2
    fi
    if [ "$FORMAT" = "json" ]; then
        settled=0
        for wait in 0 1 2 4 8 16; do
            [ "$wait" -eq 0 ] || sleep "$wait"
            looks_incomplete "$dest" || { settled=1; break; }
        done
        if [ "$settled" -ne 1 ]; then
            if log_shows_real_scan "$log"; then
                echo "tools/trivy-scan.sh: WARNING: $dest still reads back a null Vulnerabilities field, but $log shows Trivy actually scanned real packages - this looks like the read-after-write race documented in trivy.md, not a failed scan. Re-check by hand: jq '.Results[].Vulnerabilities' $dest" >&2
            else
                echo "tools/trivy-scan.sh: $dest has a null Vulnerabilities field and $log shows no evidence Trivy scanned real packages - treating as a failed scan" >&2
                rc=1
            fi
        fi

        html="out/scans/${image}-${V}.html"
        echo "converting $dest -> $html" >&2
        html_ok=1
        if ! podman run --rm \
                -v "$(pwd)/out/scans:/out:z" \
                "$TRIVY_IMAGE" \
                convert --format template --template "@/contrib/html.tpl" \
                --output "/out/${image}-${V}.html" "/out/${image}-${V}.json" \
                2>>"$log"; then
            echo "tools/trivy-scan.sh: html conversion of $dest failed" >&2
            rc=1
            html_ok=0
        else
            any_html=1
        fi
        if [ "$html_ok" = 1 ] && [ "$settled" -ne 1 ] && log_shows_real_scan "$log"; then
            # $html was rendered from $dest while $dest still hadn't cleared
            # the same read-after-write race (warned about above), so it
            # likely shows the same null Vulnerabilities. Not worth a second
            # retry loop for a file that is cheap to regenerate once $dest
            # itself settles.
            echo "tools/trivy-scan.sh: WARNING: $html was converted from $dest before that race cleared - once \`jq '.Results[].Vulnerabilities' $dest\` shows real data, redo just the conversion: podman run --rm -v \"\$(pwd)/out/scans:/out:z\" $TRIVY_IMAGE convert --format template --template \"@/contrib/html.tpl\" --output \"/out/${image}-${V}.html\" \"/out/${image}-${V}.json\"" >&2
        fi
    fi
done
if [ "$any_html" = 1 ]; then
    echo "browse the html report(s):  ./serve.sh --root out/scans" >&2
fi
exit "$rc"
