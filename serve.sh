#!/usr/bin/env bash
# Serve out/reports over HTTP so the reports can be opened from a browser.
#
#   ./serve.sh                 # foreground, Ctrl-C to stop
#   ./serve.sh --detach        # background, survives logout
#   ./serve.sh --status        # is it running, and on what URL
#   ./serve.sh --stop
#   ./serve.sh --port 9000
#   ./serve.sh --bind 127.0.0.1    # loopback only (see below)
#
# The machine that runs the pipeline has no desktop. The xlsx, the PDF, the
# deck and the PNGs are all files a person has to look at, so something has to
# hand them to a browser on another machine. A directory listing is enough for
# that, and this is a directory listing.
#
# **It is `python3 -m http.server` out of the pipeline image, not nginx.**
# SPEC 8.3 sketched nginx:alpine for this. The pipeline image is already built,
# already pinned and already carried into the air gap, and Python's server
# already produces the listing; nginx would be a second image to pin, bundle
# and checksum for M6 forever, to gain nothing this use needs. If it ever needs
# to be a real web server, this is the one line that changes.
#
# **There is no authentication and the data is not public.** The listing is
# read-only - the container mounts out/reports read-only and can see nothing
# else of the repo, not projects/, not schema/ - but anyone who can reach the
# port can read every report. That is the intent on a trusted internal
# network, and the wrong thing anywhere else; `--bind 127.0.0.1` plus an ssh
# tunnel is the answer when the network is not trusted.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/scripts/lib.sh"

PORT="$SERVE_PORT"
BIND="0.0.0.0"
ROOT="out/reports"
ACTION="start"
DETACH=0

while [ $# -gt 0 ]; do
    case "$1" in
        --detach|-d)  DETACH=1; shift ;;
        --stop)       ACTION="stop"; shift ;;
        --status)     ACTION="status"; shift ;;
        --port)       PORT="$2"; shift 2 ;;
        --bind)       BIND="$2"; shift 2 ;;
        --root)       ROOT="$2"; shift 2 ;;
        -h|--help)    sed -n '2,10p' "$0"; exit 0 ;;
        *)            die "serve.sh: unexpected argument '$1'" ;;
    esac
done

case "$ACTION" in
    stop)
        if container_running "$SERVE_CONTAINER"; then
            remove_container "$SERVE_CONTAINER"
            printf 'stopped %s\n' "$SERVE_CONTAINER"
        else
            printf '%s is not running\n' "$SERVE_CONTAINER"
        fi
        exit 0
        ;;
    status)
        if container_running "$SERVE_CONTAINER"; then
            printf 'serving  %s\n' "$(serve_url "$PORT")"
            exit 0
        fi
        printf 'not running. Start it with ./serve.sh\n'
        exit 1
        ;;
esac

[ -d "$REPO_ROOT/$ROOT" ] \
    || die "serve.sh: $ROOT does not exist yet. Run ./report.sh first."

# One server at a time on this name. Restarting is the common case - a new
# report has been written and somebody wants the port back - so a stale
# container is removed rather than reported as a conflict.
remove_container "$SERVE_CONTAINER"

printf 'serving %s\n' "$(serve_url "$PORT")"
printf '  root     %s (read-only)\n' "$ROOT"
printf '  bind     %s:%s\n' "$BIND" "$PORT"
if [ "$DETACH" = 1 ]; then
    printf '  stop     ./serve.sh --stop\n'
else
    printf '  stop     Ctrl-C\n'
fi

# `--bind` is the *host* side of the published port, which is the one that
# decides who can reach this. The server inside the container always binds
# 0.0.0.0: a process bound to 127.0.0.1 inside a container is reachable from
# nothing at all, including the host.
ARGS=(--name "$SERVE_CONTAINER"
      --publish "${BIND}:${PORT}:${PORT}"
      --source "$REPO_ROOT/$ROOT")
[ "$DETACH" = 1 ] && ARGS+=(--detach)

# Detached, the engine prints the new container id. The URL printed above it
# is what the reader wants, so the id goes to /dev/null rather than being the
# last thing on screen.
if [ "$DETACH" = 1 ]; then
    run_in_container "${ARGS[@]}" \
        -- python3 -m http.server "$PORT" --bind 0.0.0.0 --directory /work \
        >/dev/null
else
    run_in_container "${ARGS[@]}" \
        -- python3 -m http.server "$PORT" --bind 0.0.0.0 --directory /work
fi
