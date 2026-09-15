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
# The machine this runs on has no desktop, so the reports are read over the
# network from somewhere else - which means `localhost` is exactly the wrong
# answer. This picks the address the host uses to reach the outside world,
# which on a single-homed box is the one the browser wants. It is a *hint*: a
# host with several interfaces has several right answers, and the port may be
# behind a firewall. Both facts are worth printing rather than hiding.
# --------------------------------------------------------------------------
serve_host() {
    local host=""
    host="$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')"
    [ -n "$host" ] || host="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [ -n "$host" ] || host="localhost"
    printf '%s' "$host"
}

serve_url() {
    printf 'http://%s:%s/' "$(serve_host)" "${1:-$SERVE_PORT}"
}

# The port the server is actually published on, which is not necessarily the
# default: somebody may have started it with --port. Falls back to the default
# when nothing is running, because the URL is then a suggestion anyway.
serve_running_port() {
    local port=""
    port="$("$ENGINE" port "$SERVE_CONTAINER" 2>/dev/null \
            | awk -F: 'NR==1 {print $NF; exit}')"
    printf '%s' "${port:-$SERVE_PORT}"
}
