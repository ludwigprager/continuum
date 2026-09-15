#!/usr/bin/env bash
# Serve out/reports over HTTP so the reports can be opened from a browser.
#
#   ./serve.sh                 # foreground, Ctrl-C to stop
#   ./serve.sh --detach        # background, survives logout
#   ./serve.sh --status        # is it running, and on what URL
#   ./serve.sh --stop
#   ./serve.sh --port 9000     # exact port; fails if it is taken
#
# With no --port it starts at 8000 and moves up to the first free one.
#   ./serve.sh --bind 127.0.0.1    # loopback only (see below)
#   CONTAINER_ENGINE=docker ./serve.sh --detach    # when podman's port is
#                                                  # not reachable - see below
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
# **If the URL answers on the host but times out from another machine, try
# `CONTAINER_ENGINE=docker ./serve.sh`.** Found the hard way on the first host
# this ran on. Rootless podman publishes a port as an ordinary socket on the
# host, held open by a userspace proxy, and it is therefore subject to
# whatever filters the host applies to incoming traffic. Docker instead
# installs its own DNAT and accept rules and so arrives by a different route.
# On a host that drops unsolicited inbound traffic by default, the docker one
# is reachable and the podman one is not - it times out rather than being
# refused, which is what a dropped packet looks like.
#
# `ss -ltn` shows the difference: docker binds `0.0.0.0:8000`, rootless podman
# shows `*:8000`. Both answer `curl` *on the host*, because that connection
# never leaves it - which is why testing from the host proves nothing about
# this. Only a browser on another machine settles it.
#
# The other fix is to open the port on the host firewall, which needs root and
# is the operator's call, not this script's. SPEC 6.5 keeps podman as the
# default engine everywhere; this is the documented override for the one entry
# point that has to be reachable from outside.
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

# One URL per line a browser might use, aligned. There is deliberately no
# single "the" URL: this machine has no desktop, so whoever reads this is
# typing it somewhere else, and only they know which of these their machine
# can resolve and route to.
print_urls() {
    serve_urls "$1" | while IFS="$(printf '\t')" read -r url what; do
        printf '  %-34s %s\n' "$url" "$what"
    done
}

PORT="$SERVE_PORT"
PORT_GIVEN=0
BIND="0.0.0.0"
ROOT="out/reports"
ACTION="start"
DETACH=0

while [ $# -gt 0 ]; do
    case "$1" in
        --detach|-d)  DETACH=1; shift ;;
        --stop)       ACTION="stop"; shift ;;
        --status)     ACTION="status"; shift ;;
        --port)       PORT="$2"; PORT_GIVEN=1; shift 2 ;;
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
            # The port the container is actually published on, not the
            # default: `--status` after `--port 9000` must not print 8000.
            printf 'serving on port %s\n' "$(serve_running_port)"
            print_urls "$(serve_running_port)"
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

# Find a free port unless one was asked for by name.
#
# The common way to lose 8000 is to have started this yourself under the other
# engine: a detached docker server survives a logout, and podman then cannot
# bind a port it cannot see the owner of. Moving up one is nicer than failing,
# and --status reads the port off the container rather than assuming 8000, so
# nothing downstream needs to know which one was taken.
#
# An explicit --port is a request, not a preference, so it is not second-
# guessed: if it is taken, that is an error worth stopping for.
if [ "$PORT_GIVEN" = 1 ]; then
    if port_in_use "$PORT"; then
        die "serve.sh: port $PORT is already in use. Pick another with --port, or see what is on it:
    ss -ltnp | grep :$PORT
  A server this repo started under the other engine is invisible to this one -
  try: CONTAINER_ENGINE=docker ./serve.sh --status"
    fi
else
    WANTED="$PORT"
    PORT="$(find_free_port "$PORT")" \
        || die "serve.sh: no free port in $WANTED..$((WANTED + 19)). Free one, or pass --port."
    if [ "$PORT" != "$WANTED" ]; then
        OTHER="$(other_engine_serving || true)"
        if [ -n "$OTHER" ]; then
            # Not a clash with a stranger: it is this repo's own server, under
            # the engine whose published ports other machines can actually
            # reach. Moving up would hand back a host-only server, so say what
            # is going on rather than quietly doing the less useful thing.
            printf 'port %s is held by %s under %s - this repo'"'"'s own server.\n' \
                "$WANTED" "$SERVE_CONTAINER" "$OTHER"
            printf '  It is probably the one you want: a %s-published port is reachable\n' "$OTHER"
            printf '  from other machines where a rootless podman one often is not.\n\n'
            printf '      CONTAINER_ENGINE=%s ./serve.sh --status   # its URL\n' "$OTHER"
            printf '      CONTAINER_ENGINE=%s ./serve.sh --stop     # or take the port back\n\n' "$OTHER"
            printf '  Starting a second one on %s anyway; it will answer on this host.\n' "$PORT"
        else
            printf 'port %s is in use, using %s instead\n' "$WANTED" "$PORT"
        fi
    fi
fi

printf 'serving %s (read-only) on port %s, bound to %s\n' "$ROOT" "$PORT" "$BIND"
print_urls "$PORT"
if [ "$DETACH" = 1 ]; then
    printf '\nstop with ./serve.sh --stop\n'
else
    printf '\nstop with Ctrl-C\n'
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
