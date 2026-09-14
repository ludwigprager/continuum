#!/usr/bin/env bash
# Shared container plumbing. The ONLY place that knows about podman/docker.
#
# Everything else (check.sh, verify.sh, shell.sh, the pre-commit hook, CI)
# goes through run_in_container. If something about the container runtime
# changes, it changes here and nowhere else.
#
# shellcheck shell=bash

set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-mig-pipeline}"
IMAGE_TAG="${IMAGE_TAG:-0.1.0}"
IMAGE_REF="${IMAGE_REF:-${IMAGE_NAME}:${IMAGE_TAG}}"

# Name of the optional warm container used by the pre-commit hook.
DEV_CONTAINER="${DEV_CONTAINER:-mig-dev}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO_ROOT

# Exit codes (HANDOFF 5.3): 0 ok, 1 invalid data, 2 tool/usage error.
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
    printf 'building %s with %s (first run only)\n' "$IMAGE_REF" "$ENGINE" >&2
    "$ENGINE" build \
        -f "$REPO_ROOT/docker/Dockerfile.pipeline" \
        -t "$IMAGE_REF" \
        "$REPO_ROOT" >&2 \
        || die "image build failed"
}

ensure_image() { image_exists || build_image; }

# The digest that actually produced a run, for manifest.json from M2 on.
image_digest() {
    "$ENGINE" image inspect "$IMAGE_REF" --format '{{index .RepoDigests 0}}' 2>/dev/null \
        || "$ENGINE" image inspect "$IMAGE_REF" --format '{{.Id}}' 2>/dev/null \
        || printf 'unknown'
}

# --------------------------------------------------------------------------
# run_in_container [--rw] [--network NET] -- command...
#
#   --rw         mount the repo read-write (default: read-only)
#   --network    default "none"; nothing in the daily pipeline may need the net
#
# Propagates the command's exit code verbatim, EXCEPT 125-127, which mean the
# runtime itself failed. Those become 2, so CI can never mistake an infra
# failure for a data verdict.
# --------------------------------------------------------------------------
run_in_container() {
    local mount_mode="ro" network="none" interactive=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --rw)       mount_mode="rw"; shift ;;
            --network)  network="$2"; shift 2 ;;
            --interactive) interactive=1; shift ;;
            --)         shift; break ;;
            *)          die "run_in_container: unexpected argument '$1'" ;;
        esac
    done
    [ $# -gt 0 ] || die "run_in_container: no command given"

    ensure_image

    local -a tty_args=()
    if [ "$interactive" = 1 ] && [ -t 0 ] && [ -t 1 ]; then
        tty_args=(-it)
    fi

    local rc=0
    if warm_container_running; then
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
        "$ENGINE" run --rm "${tty_args[@]}" \
            "$(engine_user_args)" \
            --network="$network" \
            --volume "$REPO_ROOT:/work:${mount_mode},z" \
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
    local state
    state="$("$ENGINE" container inspect "$DEV_CONTAINER" \
        --format '{{.State.Running}}' 2>/dev/null)" || return 1
    [ "$state" = "true" ]
}
