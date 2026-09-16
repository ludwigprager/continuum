#!/usr/bin/env bash
# Push this project's images to ghcr.io.
#
#   tools/push-ghcr.sh                     # push both project images
#   tools/push-ghcr.sh continuum-import    # push just one
#   tools/push-ghcr.sh --dry-run           # show the plan, ask for nothing
#
# Credentials: GITHUB_USERNAME and GITHUB_TOKEN are read from tools/.env when
# it exists (copy tools/.env.example there); whatever is missing is asked for
# at the terminal, the token without echo.
#
# What goes up is what the entry points build locally -
# continuum-pipeline:<VERSION> and continuum-import:<VERSION>, the same two
# images tools/trivy-scan.sh scans. An image that is not there yet is built
# first, the way every entry point builds its image on first use (SPEC 6.5);
# one that is there is pushed as it is, tagged with the VERSION file.
#
# The script asks for three things, each from tools/.env or the terminal:
#   the ghcr.io owner     a GitHub username or an org; the packages land under
#                         ghcr.io/<owner>/continuum-<name>:<VERSION>. Not read
#                         from the .env; when a username is known it is the
#                         default, which covers the personal-account case.
#   the GitHub username   for the login; GITHUB_USERNAME in tools/.env
#   a personal access token; GITHUB_TOKEN in tools/.env, or read without
#                         echo at the terminal. A classic token needs
#                         write:packages; a fine-grained one needs
#                         "Packages: read and write". It is handed to the
#                         engine with --password-stdin - never on a command
#                         line, never in the process list - and unset as soon
#                         as the login has consumed it.
#
# tools/.env holds a live token, so it is gitignored and should be readable
# by its owner only (the script warns, it does not fix, on looser modes).
# After a successful login the engine also keeps the credentials in its own
# store (~/.config/containers/auth.json for podman, ~/.docker/config.json for
# docker), so a later `podman push` of the same reference works without this
# script.
#
# Deliberately not an entry point in SPEC 6.5, and not run through
# run_in_container: the daily pipeline has no business reaching for a
# registry, and login/push are host-side engine commands - the same reason
# tools/trivy-scan.sh talks to the engine directly. ENGINE and die still come
# from scripts/lib.sh, because that is where the podman/docker knowledge lives
# and nowhere else.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../scripts/lib.sh
source "$(dirname "$0")/../scripts/lib.sh"

DRY_RUN=0
IMAGES=()
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        -*)        die "tools/push-ghcr.sh: unknown flag $1" ;;
        continuum-pipeline|continuum-import) IMAGES+=("$1"); shift ;;
        *)         die "tools/push-ghcr.sh: unknown image '$1' (expected continuum-pipeline or continuum-import)" ;;
    esac
done
[ ${#IMAGES[@]} -gt 0 ] || IMAGES=(continuum-pipeline continuum-import)

# ---------------------------------------------------------------------------
# Credentials from tools/.env, the terminal for whatever is missing.
# ---------------------------------------------------------------------------
ENV_FILE="$REPO_ROOT/tools/.env"

# The first exact NAME=VALUE line of the .env file, with one layer of
# optional surrounding quotes removed. Prints nothing if the file or the line
# is absent. Deliberately not `source`: the file is data, and a value that
# contains a space or a `#` must not become shell syntax.
env_value() {
    local line value
    [ -f "$ENV_FILE" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            "$1="*) ;;
            *) continue ;;
        esac
        value="${line#*=}"
        case "$value" in
            \"*\") value="${value#\"}"; value="${value%\"}" ;;
            \'*\') value="${value#\'}"; value="${value%\'}" ;;
        esac
        printf '%s' "$value"
        return 0
    done < "$ENV_FILE"
    return 0
}

GH_USER="$(env_value GITHUB_USERNAME)"
GH_TOKEN="$(env_value GITHUB_TOKEN)"

if [ -f "$ENV_FILE" ]; then
    # A live token that group or world can read is a token somebody else can
    # read. Warn, do not block: the file is local, the owner may have a reason.
    case "$(stat -c '%a' "$ENV_FILE" 2>/dev/null || true)" in
        *00) : ;;
        *)   printf 'tools/push-ghcr.sh: tools/.env is readable beyond its owner - chmod 600 tools/.env\n' >&2 ;;
    esac
fi

printf 'ghcr.io owner - a GitHub username or the organisation the packages belong to\n'
if [ -n "$GH_USER" ]; then
    # A personal account pushes to its own name; that is the default, not the assumption.
    read -r -e -p "  owner [$GH_USER]: " OWNER
    OWNER="${OWNER:-$GH_USER}"
else
    read -r -e -p '  owner: ' OWNER
fi
[ -n "$OWNER" ] || die "tools/push-ghcr.sh: no owner given"

# The local reference an entry point would build for this image, from the same
# names lib.sh uses, so the plan and the run can never drift apart.
image_ref() {
    if [ "$1" = "continuum-import" ]; then
        printf '%s' "$IMPORT_IMAGE_REF"
    else
        printf '%s' "${IMAGE_NAME}:${IMAGE_TAG}"
    fi
}

if [ "$DRY_RUN" = 1 ]; then
    # A plan must not build anything and must not ask for credentials.
    printf '\ndry run - nothing is built, nothing is pushed\n'
    for image in "${IMAGES[@]}"; do
        ref="$(image_ref "$image")"
        if "$ENGINE" image inspect "$ref" >/dev/null 2>&1; then
            printf '  %-24s present\n' "$ref"
        else
            printf '  %-24s not built yet - a real run would build it first\n' "$ref"
        fi
        printf '  would tag + push  ghcr.io/%s/%s:%s\n' "$OWNER" "$image" "$IMAGE_TAG"
    done
    exit 0
fi

# Build on first use, like every entry point (SPEC 6.5).
REFS=()
for image in "${IMAGES[@]}"; do
    if [ "$image" = "continuum-import" ]; then
        use_import_image
    else
        IMAGE_REF="${IMAGE_NAME}:${IMAGE_TAG}"
        DOCKERFILE="docker/Dockerfile.pipeline"
    fi
    ensure_image
    REFS+=("$IMAGE_REF")
done

printf '\nlogin to ghcr.io - the engine keeps the credentials for later pushes\n'
if [ -n "$GH_USER" ]; then
    printf '  GitHub username  %s  (tools/.env)\n' "$GH_USER"
else
    read -r -e -p "  GitHub username [$OWNER]: " GH_USER
    GH_USER="${GH_USER:-$OWNER}"
fi
if [ -z "$GH_TOKEN" ]; then
    read -r -s -e -p '  GitHub token (classic: write:packages; fine-grained: Packages: read and write): ' GH_TOKEN
    printf '\n'
    [ -n "$GH_TOKEN" ] || die "tools/push-ghcr.sh: no token given"
else
    printf '  GitHub token     (tools/.env)\n'
fi
printf '%s' "$GH_TOKEN" \
    | "$ENGINE" login ghcr.io --username "$GH_USER" --password-stdin \
    || die "tools/push-ghcr.sh: ghcr.io login failed - check the username and the token's package write access"
unset GH_TOKEN

rc=0
for i in "${!IMAGES[@]}"; do
    image="${IMAGES[$i]}"
    local_ref="${REFS[$i]}"
    remote_ref="ghcr.io/${OWNER}/${image}:${IMAGE_TAG}"
    printf '\ntagging %s -> %s\n' "$local_ref" "$remote_ref"
    "$ENGINE" tag "$local_ref" "$remote_ref"
    printf 'pushing %s\n' "$remote_ref"
    if ! "$ENGINE" push "$remote_ref"; then
        printf 'tools/push-ghcr.sh: push of %s failed\n' "$remote_ref" >&2
        rc=1
    fi
done

if [ "$rc" = 0 ]; then
    printf '\npushed:\n'
    for image in "${IMAGES[@]}"; do
        printf '  ghcr.io/%s/%s:%s\n' "$OWNER" "$image" "$IMAGE_TAG"
    done
fi
exit "$rc"
