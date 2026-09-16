#!/usr/bin/env bash
# Shared container plumbing. The ONLY place that knows about podman/docker.
#
# Everything else (check.sh, verify.sh, shell.sh, the pre-commit hook, CI)
# goes through run_in_container. If something about the container runtime
# changes, it changes here and nowhere else.
#
# shellcheck shell=bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO_ROOT

# The image tag follows VERSION, which is also the pipeline_version recorded in
# every manifest. Tying the two together means a run can never silently reuse
# an image built from different requirements: bump VERSION when the image
# changes and the next run rebuilds instead of finding a stale tag.
IMAGE_NAME="${IMAGE_NAME:-continuum-pipeline}"
IMAGE_TAG="${IMAGE_TAG:-$(cat "$REPO_ROOT/VERSION" 2>/dev/null || echo 0)}"
IMAGE_REF="${IMAGE_REF:-${IMAGE_NAME}:${IMAGE_TAG}}"

# The importer runs rarely and does not need the renderer stack, so it has its
# own image (SPEC 8.1). Entry points that need it set DOCKERFILE and
# IMAGE_REF before calling run_in_container.
IMPORT_IMAGE_REF="${IMPORT_IMAGE_REF:-continuum-import:${IMAGE_TAG}}"
DOCKERFILE="${DOCKERFILE:-docker/Dockerfile.pipeline}"

# Switch to the import image for this shell. See SPEC 8.1.
use_import_image() {
    IMAGE_REF="$IMPORT_IMAGE_REF"
    DOCKERFILE="docker/Dockerfile.import"
}

# Name of the optional warm container used by the pre-commit hook.
DEV_CONTAINER="${DEV_CONTAINER:-continuum-dev}"

# The report server (./serve.sh). Named so it can be found and stopped again,
# and so ./report.sh can say whether the URL it prints is live.
SERVE_CONTAINER="${SERVE_CONTAINER:-continuum-serve}"
SERVE_PORT="${SERVE_PORT:-8000}"

# The project editor (./edit.sh, SPEC 6.6, M7). A different name and default
# port from SERVE_*, so both can run at once - the editor is read-write over
# the catalogue and serve.sh's report browser is unrelated to it.
EDIT_CONTAINER="${EDIT_CONTAINER:-continuum-edit}"
EDIT_PORT="${EDIT_PORT:-8001}"

# Exit codes (SPEC 5.3): 0 ok, 1 invalid data, 2 tool/usage error.
readonly EXIT_TOOL=2

die() { printf '%s\n' "$*" >&2; exit "$EXIT_TOOL"; }

# --------------------------------------------------------------------------
# Engine detection. Podman is the default; docker is the variant.
# --------------------------------------------------------------------------
detect_engine() {
    if [ -n "${CONTAINER_ENGINE:-}" ]; then
        command -v "$CONTAINER_ENGINE" >/dev/null 2>&1 \
            || die "CONTAINER_ENGINE=$CONTAINER_ENGINE is not installed"
        printf '%s' "$CONTAINER_ENGINE"
        return
    fi
    if command -v podman >/dev/null 2>&1; then printf 'podman'; return; fi
    if command -v docker >/dev/null 2>&1; then printf 'docker'; return; fi
    die "no container engine found. Install podman (preferred) or docker."
}

ENGINE="$(detect_engine)"
export ENGINE

# Rootless podman maps the host user into the container with --userns=keep-id.
# Docker needs an explicit --user or it writes root-owned files onto the host.
engine_user_args() {
    case "$ENGINE" in
        podman) printf '%s' "--userns=keep-id" ;;
        *)      printf '%s' "--user=$(id -u):$(id -g)" ;;
    esac
}

# --------------------------------------------------------------------------
# Image
# --------------------------------------------------------------------------
image_exists() { "$ENGINE" image inspect "$IMAGE_REF" >/dev/null 2>&1; }

build_image() {
    printf 'building %s from %s with %s (first run only)\n' \
        "$IMAGE_REF" "$DOCKERFILE" "$ENGINE" >&2
    "$ENGINE" build \
        -f "$REPO_ROOT/$DOCKERFILE" \
        -t "$IMAGE_REF" \
        "$REPO_ROOT" >&2 \
        || die "image build failed"
}

ensure_image() { image_exists || build_image; }

# The digest that actually produced a run, for manifest.json from M2 on.
#
# An image built locally has no RepoDigest, and docker then prints a bare
# newline to stdout *before* failing - which ended up inside the digest and,
# once the txt report started printing provenance, inside the middle of a
# line. So both readings are trimmed and the first non-empty one wins.
image_digest() {
    local digest=""
    for format in '{{index .RepoDigests 0}}' '{{.Id}}'; do
        digest="$("$ENGINE" image inspect "$IMAGE_REF" --format "$format" 2>/dev/null \
                  | tr -d '[:space:]')"
        [ -n "$digest" ] && break
    done
    printf '%s' "${digest:-unknown}"
}

# --------------------------------------------------------------------------
# run_in_container [--rw] [--network NET] [--publish SPEC] [--source DIR]
#                  [--name NAME] [--detach] -- command...
#
#   --rw         mount the source read-write (default: read-only)
#   --network    default "none"; nothing in the daily pipeline may need the
#                net. "default" leaves the engine's own networking alone,
#                which is what publishing a port needs.
#   --publish    publish a port, e.g. 0.0.0.0:8000:8000 (see serve.sh)
#   --source     what to bind at /work. Default the repo root; the report
#                server passes out/reports, so the one container here that
#                listens on a network cannot read projects/ or schema/.
#   --name       name the container so it can be found and stopped again
#   --detach     start it in the background and return
#
# Propagates the command's exit code verbatim, EXCEPT 125-127, which mean the
# runtime itself failed. Those become 2, so CI can never mistake an infra
# failure for a data verdict.
# --------------------------------------------------------------------------
run_in_container() {
    local mount_mode="ro" network="none" interactive=0 detach=0
    local source_dir="$REPO_ROOT" publish="" name=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --rw)       mount_mode="rw"; shift ;;
            --network)  network="$2"; shift 2 ;;
            --publish)  publish="$2"; shift 2 ;;
            --source)   source_dir="$2"; shift 2 ;;
            --name)     name="$2"; shift 2 ;;
            --detach)   detach=1; shift ;;
            --interactive) interactive=1; shift ;;
            --)         shift; break ;;
            *)          die "run_in_container: unexpected argument '$1'" ;;
        esac
    done
    [ $# -gt 0 ] || die "run_in_container: no command given"
    [ -d "$source_dir" ] || die "run_in_container: --source $source_dir does not exist"

    # A published port on a container with no network publishes nothing, and
    # does so silently. There is only one thing the caller can mean.
    if [ -n "$publish" ] && [ "$network" = "none" ]; then
        network="default"
    fi

    ensure_image

    local -a tty_args=()
    if [ "$interactive" = 1 ] && [ -t 0 ] && [ -t 1 ]; then
        tty_args=(-it)
    fi

    # Options that describe a *new* container: the warm-container fast path
    # cannot honour any of them, so it is skipped rather than quietly
    # ignoring the port or the mount the caller asked for.
    local -a extra_args=()
    [ -n "$publish" ] && extra_args+=(--publish "$publish")
    [ -n "$name" ] && extra_args+=(--name "$name")
    if [ "$network" != "default" ]; then
        extra_args+=(--network="$network")
    fi
    if [ "$detach" = 1 ]; then
        extra_args+=(--detach)
    else
        extra_args+=(--rm)
    fi

    local rc=0
    if [ -z "$publish$name" ] && [ "$detach" = 0 ] && warm_container_running; then
        # Fast path for the pre-commit hook: exec into an already-running
        # container instead of paying container startup on every commit.
        set +e
        "$ENGINE" exec "${tty_args[@]}" -w /work "$DEV_CONTAINER" "$@"
        rc=$?
        set -e
    else
        # ':z' relabels the mount for SELinux. Required on RHEL/Fedora,
        # ignored elsewhere, harmless everywhere.
        set +e
        "$ENGINE" run "${extra_args[@]}" "${tty_args[@]}" \
            "$(engine_user_args)" \
            --volume "$source_dir:/work:${mount_mode},z" \
            --workdir /work \
            "$IMAGE_REF" "$@"
        rc=$?
        set -e
    fi

    case "$rc" in
        125|126|127)
            printf '%s: container runtime failure (exit %s), not a data problem\n' \
                "$ENGINE" "$rc" >&2
            return "$EXIT_TOOL"
            ;;
    esac
    return "$rc"
}

warm_container_running() {
    [ "${USE_WARM_CONTAINER:-0}" = "1" ] || return 1
    container_running "$DEV_CONTAINER"
}

container_running() {
    local state
    state="$("$ENGINE" container inspect "$1" \
        --format '{{.State.Running}}' 2>/dev/null)" || return 1
    [ "$state" = "true" ]
}

# Remove a container whether it is running or not. Not an error if it is gone
# already: ./serve.sh --stop should be safe to run twice.
remove_container() {
    "$ENGINE" rm --force "$1" >/dev/null 2>&1 || true
}

# --------------------------------------------------------------------------
# Where a browser on another machine should point.
#
# Every address a browser might reach this machine on, one "url<TAB>what it is"
# per line.
#
# This used to guess a single address from the default route. That is right on
# a single-homed box and wrong the moment there are two NICs, which is not
# rare - and a wrong guess looks exactly like a broken server, which cost an
# afternoon. So the choice is handed to the reader instead: interface
# addresses, then the machine's own name, which is what most people would
# rather type and the only one that survives the address changing.
#
# Never a bind address. `0.0.0.0` means "listen on everything" and is not
# something anyone can type into a browser; printing it next to a URL, as an
# earlier version did, reads as though it were one.
#
# Container and virtual interfaces are left out. A dozen `br-*` bridges are
# noise no browser will ever use.
serve_addresses() {
    ip -o -4 addr show scope global 2>/dev/null \
        | awk '{split($4, a, "/"); print $2, a[1]}' \
        | grep -Ev '^(docker|br-|veth|virbr|cni|podman|kube|flannel|tun|tap|lo)'
}

serve_urls() {
    local port="${1:-$SERVE_PORT}" name="" fqdn=""
    serve_addresses | while read -r interface address; do
        printf 'http://%s:%s/\t%s\n' "$address" "$port" "$interface"
    done

    # The name is a candidate, not a promise: it only works if whatever the
    # browser asks for DNS resolves it. Said plainly rather than printed as
    # though it were checked.
    name="$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)"
    [ -n "$name" ] && printf 'http://%s:%s/\thostname, if DNS resolves it\n' \
        "$name" "$port"
    fqdn="$(hostname -f 2>/dev/null || true)"
    [ -n "$fqdn" ] && [ "$fqdn" != "$name" ] \
        && printf 'http://%s:%s/\tFQDN, if DNS resolves it\n' "$fqdn" "$port"

    # No .local line. mDNS was offered here as a candidate for the case where
    # a bare hostname does not resolve across a LAN, but it needs avahi running
    # on both ends and was one more line to rule out on a list where every line
    # already has to be tried. Dropped on request.
    return 0
}

# The port the server is actually published on, which is not necessarily the
# default: somebody may have started it with --port. Falls back to the default
# when nothing is running, because the URL is then a suggestion anyway.
#
# Takes the container name and fallback port so ./edit.sh can reuse it for
# its own container instead of a second copy of this lookup. Both are
# optional - serve.sh calls it bare and gets its own defaults.
serve_running_port() {
    local container="${1:-$SERVE_CONTAINER}" fallback="${2:-$SERVE_PORT}" port=""
    port="$("$ENGINE" port "$container" 2>/dev/null \
            | awk -F: 'NR==1 {print $NF; exit}')"
    printf '%s' "${port:-$fallback}"
}

# --------------------------------------------------------------------------
# Is anything listening on this TCP port, whoever owns it?
#
# Deliberately not "is one of OUR containers on it": the case this exists for
# is a server this repo started under the *other* engine, which the current
# engine cannot see at all. Podman then fails to bind with
# "rootlessport listen tcp 0.0.0.0:8000: bind: address already in use", which
# reads like a conflict with a stranger rather than with yourself.
# --------------------------------------------------------------------------
port_in_use() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN
        return
    fi
    # No ss. Connecting proves a listener that accepts; one that binds without
    # accepting is missed, which is rare enough to live with.
    (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null || return 1
    exec 3<&-
    return 0
}

# Prints the name of the OTHER engine if our own serve container is running
# under it, nothing otherwise.
#
# This is the common reason the default port is taken, and it matters more than
# a port clash usually would: the two engines are not interchangeable here. A
# docker-published port is reachable from other machines, a rootless-podman one
# generally is not (see serve_addresses above), so quietly moving to the next
# port hands back a server that works on this host and nowhere else.
other_engine_serving() {
    local other=""
    case "$ENGINE" in
        podman) other="docker" ;;
        docker) other="podman" ;;
        *)      return 1 ;;
    esac
    command -v "$other" >/dev/null 2>&1 || return 1
    "$other" ps --filter "name=^${SERVE_CONTAINER}$" --format '{{.Names}}' 2>/dev/null \
        | grep -q . || return 1
    printf '%s' "$other"
}

# The first free port at or after $1, trying $2 of them (default 20).
# Prints nothing and returns 1 if they are all taken.
find_free_port() {
    local port="$1" tries="${2:-20}" i=0
    while [ "$i" -lt "$tries" ]; do
        if ! port_in_use "$port"; then
            printf '%s' "$port"
            return 0
        fi
        port=$((port + 1))
        i=$((i + 1))
    done
    return 1
}
