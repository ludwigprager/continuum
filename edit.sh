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
        exit 0
        ;;
    status)
        if container_running "$EDIT_CONTAINER"; then
            running_port="$(serve_running_port "$EDIT_CONTAINER" "$EDIT_PORT")"
            printf 'editor running on port %s\n' "$running_port"
            print_urls "$running_port"
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

printf 'serving the project editor (read-write over projects/) on port %s, bound to %s\n' \
    "$PORT" "$BIND"
printf 'no authentication - anyone who can reach this port can edit project data\n'
print_urls "$PORT"
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

if [ "$DETACH" = 1 ]; then
    run_in_container "${ARGS[@]}" \
        -- python3 tools/editor/app.py --port "$PORT" --bind 0.0.0.0 \
        >/dev/null
else
    run_in_container "${ARGS[@]}" \
        -- python3 tools/editor/app.py --port "$PORT" --bind 0.0.0.0
fi
