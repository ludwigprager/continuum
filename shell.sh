#!/usr/bin/env bash
# Interactive shell in the pipeline image. Repo mounted read-write at /work.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/scripts/lib.sh"
run_in_container --rw --interactive -- "${@:-bash}"
