#!/usr/bin/env bash
# Start the project editor: a generic form, built from schema/project.schema.yaml,
# for creating and editing projects/*.yaml in a browser. SPEC 6.6, milestone M7.
#
#   ./edit.sh                  # foreground, Ctrl-C to stop, reachable on the LAN
#   ./edit.sh --detach         # background, survives logout
#   ./edit.sh --status
#   ./edit.sh --stop
#   ./edit.sh --port 9001
#   ./edit.sh --bind 127.0.0.1 # loopback only - see below
#
# This tool ONLY writes local YAML. It never runs git: `git add`/`git commit`
# is still a manual step after saving, exactly like a hand-edited file
# (SPEC 2). It exists for one specific case - a team member who cannot use
# the CLI or git at all - not as a replacement for hand-editing YAML and
# submitting a merge request, which stays the default path for everyone else.
#
# **Also starts the report server (./serve.sh) if it is not already running,
# on the same --bind.** The editor has a "generate report" button (it runs
# the same pipeline ./report.sh does, in-process - see
# tools/editor/reportgen.py for why), and a person who cannot use the CLI has
# no other way to look at what it produced. `./edit.sh --stop` stops only the
# editor - the report server is left running, since other people may be
# looking at reports through it; stop it separately with ./serve.sh --stop.
#
# **Binds to 0.0.0.0 by default, same as ./serve.sh - decided in SPEC 12.9.**
# Remote reachability is essential (the person this is for is not on the host
# running the container) and no authentication was explicitly accepted as the
# tradeoff for that. There is still no auth of any kind: anyone who can reach
# the port can create, edit and corrupt project data. Pass `--bind 127.0.0.1`
# to fall back to loopback-only (plus an SSH tunnel) if that trust boundary
# ever needs tightening for a given run.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/scripts/lib.sh"

print_urls() {
    local port="$1"
    case "$BIND" in
        127.0.0.1|localhost|::1)
            printf '  %-34s %s\n' "http://127.0.0.1:${port}/" \
                "loopback only - pass --bind 0.0.0.0 to reach this from another machine"
            return
            ;;
    esac
    serve_urls "$port" | while IFS="$(printf '\t')" read -r url what; do
        printf '  %-34s %s\n' "$url" "$what"
    done
}

# Make sure the report server is up alongside the editor, starting it if it
# is not already running. Shells out to ./serve.sh itself rather than
# reimplementing any part of it - container knowledge stays in scripts/lib.sh
# and nowhere else. Prints the port it ended up on, or nothing if it could
# not be started (a warning goes to stderr; the editor still works either
# way, the "generate report" button just has nothing to link to).
ensure_report_server() {
    mkdir -p "$REPO_ROOT/out/reports"  # serve.sh refuses a missing directory
    if ! container_running "$SERVE_CONTAINER"; then
        "$REPO_ROOT/serve.sh" --detach --bind "$BIND" >/dev/null \
            || printf 'warning: could not start the report server (./serve.sh) alongside the editor\n' >&2
    fi
    if container_running "$SERVE_CONTAINER"; then
        # shellcheck disable=SC2119  # bare call is intentional, see lib.sh
        serve_running_port
    fi
}

PORT="$EDIT_PORT"
PORT_GIVEN=0
BIND="0.0.0.0"
ACTION="start"
DETACH=0

while [ $# -gt 0 ]; do
    case "$1" in
        --detach|-d)  DETACH=1; shift ;;
        --stop)       ACTION="stop"; shift ;;
        --status)     ACTION="status"; shift ;;
        --port)       PORT="$2"; PORT_GIVEN=1; shift 2 ;;
        --bind)       BIND="$2"; shift 2 ;;
        -h|--help)    sed -n '2,20p' "$0"; exit 0 ;;
        *)            die "edit.sh: unexpected argument '$1'" ;;
    esac
done

case "$ACTION" in
    stop)
        if container_running "$EDIT_CONTAINER"; then
            remove_container "$EDIT_CONTAINER"
            printf 'stopped %s\n' "$EDIT_CONTAINER"
        else
            printf '%s is not running\n' "$EDIT_CONTAINER"
        fi
        if container_running "$SERVE_CONTAINER"; then
            printf 'the report server (./serve.sh) is still running - stop it separately with ./serve.sh --stop if you no longer need it\n'
        fi
        exit 0
        ;;
    status)
        if container_running "$EDIT_CONTAINER"; then
            running_port="$(serve_running_port "$EDIT_CONTAINER" "$EDIT_PORT")"
            printf 'editor running on port %s\n' "$running_port"
            print_urls "$running_port"
            if container_running "$SERVE_CONTAINER"; then
                # shellcheck disable=SC2119
                printf '\nreport server running on port %s (./serve.sh --status for its URLs)\n' \
                    "$(serve_running_port)"
            else
                printf '\nreport server (./serve.sh) is not running\n'
            fi
            exit 0
        fi
        printf 'not running. Start it with ./edit.sh\n'
        exit 1
        ;;
esac

# One editor at a time on this name - restarting is the common case.
remove_container "$EDIT_CONTAINER"

if [ "$PORT_GIVEN" = 1 ]; then
    if port_in_use "$PORT"; then
        die "edit.sh: port $PORT is already in use. Pick another with --port, or see what is on it:
    ss -ltnp | grep :$PORT"
    fi
else
    WANTED="$PORT"
    PORT="$(find_free_port "$PORT")" \
        || die "edit.sh: no free port in $WANTED..$((WANTED + 19)). Free one, or pass --port."
    [ "$PORT" != "$WANTED" ] && printf 'port %s is in use, using %s instead\n' "$WANTED" "$PORT"
fi

# Started before the banner below so its port is known in time to print it,
# and before the editor's own container so a report can be viewed the moment
# the "generate report" button is used, not after some second manual step.
REPORT_PORT="$(ensure_report_server || true)"

printf 'serving the project editor (read-write over projects/) on port %s, bound to %s\n' \
    "$PORT" "$BIND"
printf 'no authentication - anyone who can reach this port can edit project data\n'
print_urls "$PORT"
if [ -n "$REPORT_PORT" ]; then
    printf '\nreport server (./serve.sh) running on port %s - reports generated from the\n' \
        "$REPORT_PORT"
    printf 'editor'"'"'s "generate report" button will be linked from there\n'
else
    printf '\nreport server (./serve.sh) could not be started - "generate report" will still\n'
    printf 'work, there is just nothing to link the result to\n'
fi
if [ "$DETACH" = 1 ]; then
    printf '\nstop with ./edit.sh --stop\n'
else
    printf '\nstop with Ctrl-C\n'
fi

# --rw: this is the one entry point that writes into projects/. Full repo
# mounted, not just projects/, because it also reads schema/, taxonomy.yaml,
# sites.yaml and teams.yaml to build the form and its dropdowns.
ARGS=(--rw --name "$EDIT_CONTAINER"
      --publish "${BIND}:${PORT}:${PORT}")
[ "$DETACH" = 1 ] && ARGS+=(--detach)

PYARGS=(--port "$PORT" --bind 0.0.0.0)
[ -n "$REPORT_PORT" ] && PYARGS+=(--serve-port "$REPORT_PORT")

if [ "$DETACH" = 1 ]; then
    run_in_container "${ARGS[@]}" \
        -- python3 tools/editor/app.py "${PYARGS[@]}" \
        >/dev/null
else
    run_in_container "${ARGS[@]}" \
        -- python3 tools/editor/app.py "${PYARGS[@]}"
fi
