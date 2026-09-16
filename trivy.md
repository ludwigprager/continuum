# Scanning the project images with Trivy

This project builds two images (`SPEC.md` §8.1):

| image | Dockerfile | built by |
|---|---|---|
| `continuum-pipeline:<VERSION>` | `docker/Dockerfile.pipeline` | `./check.sh`, `./snapshot.sh`, `./report.sh`, `./verify.sh`, `./shell.sh`, `./serve.sh` |
| `continuum-import:<VERSION>` | `docker/Dockerfile.import` | `./merge/merge.sh`, `./import/import.sh` |

`<VERSION>` is the contents of `VERSION` at the repo root. Neither image is
pulled from a registry — `scripts/lib.sh` builds them locally on first use
(`ensure_image`) — so scanning them means scanning what you just built.

This howto runs **Trivy itself as a container**, scanning the images in
Podman's local store through Podman's API socket via Trivy's own
`--podman-host` flag. That's Trivy's documented, native Podman integration —
not a `DOCKER_HOST` compatibility trick — so nothing has to be exported to a
tarball first and nothing has to be installed on the host. See
[Podman support in Trivy's container-image target docs](https://trivy.dev/docs/dev/guide/target/container_image/).

**`tools/trivy-scan.sh` packages everything below into one command:**

```bash
tools/trivy-scan.sh                     # scan both project images
tools/trivy-scan.sh continuum-pipeline  # scan just one
tools/trivy-scan.sh --format table      # human-readable instead of json
tools/trivy-scan.sh -- --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed
```

The rest of this document is the walkthrough behind it — read on for the
socket setup, the two gotchas, and the offline/air-gapped variant, none of
which the script can do for you (enabling the socket) or hides from you
(everything else is just its own source, `tools/trivy-scan.sh --help`).

Scan results are written to **`out/scans/`** on the host — gitignored, next
to `out/tables/` and `out/reports/`, the same place every other derived
artifact in this repo lands (SPEC 4). `--output` inside the Trivy container
only writes inside that container, so getting a file onto the host means
mounting a directory for it — every command below does that.

## Build the images first

```bash
cd /path/to/continuum
V=$(cat VERSION)
podman build -f docker/Dockerfile.pipeline -t continuum-pipeline:$V .
podman build -f docker/Dockerfile.import  -t continuum-import:$V  .
```

(Or just run `./check.sh` / `./import/import.sh` once — either builds the
image it needs.)

## 1. Enable Podman's socket

This host runs **rootless** Podman (check with `podman info --format
'{{.Host.Security.Rootless}}'`), so the socket is a per-user systemd unit:

```bash
systemctl --user enable --now podman.socket
systemctl --user status podman.socket   # confirm it's active
```

The socket lands at `/run/user/$(id -u)/podman/podman.sock`. On **rootful**
Podman it's `/run/podman/podman.sock` instead, owned by root — the mount and
socket path below both change accordingly; run as root or via `sudo` and drop
the `--user` from the systemctl calls.

## 2. Run Trivy in a container, results into `out/scans/`

Pin the Trivy image version rather than `:latest` — this project pins every
renderer for reproducibility (`SPEC.md` §9), and a scanner that silently
changes version between runs is the same trap. Check
[the current release](https://github.com/aquasecurity/trivy/releases) and
update the tag below when you bump it deliberately.

```bash
TRIVY_IMAGE=docker.io/aquasec/trivy:0.74.0
V=$(cat VERSION)
mkdir -p out/scans

podman run --rm \
  -v "/run/user/$(id -u)/podman/podman.sock:/run/podman/podman.sock:z" \
  -v trivy-cache:/root/.cache \
  -v "$(pwd)/out/scans:/out:z" \
  "$TRIVY_IMAGE" \
  image --image-src podman --podman-host /run/podman/podman.sock \
  --format json --output "/out/continuum-pipeline-$V.json" \
  "localhost/continuum-pipeline:$V"

podman run --rm \
  -v "/run/user/$(id -u)/podman/podman.sock:/run/podman/podman.sock:z" \
  -v trivy-cache:/root/.cache \
  -v "$(pwd)/out/scans:/out:z" \
  "$TRIVY_IMAGE" \
  image --image-src podman --podman-host /run/podman/podman.sock \
  --format json --output "/out/continuum-import-$V.json" \
  "localhost/continuum-import:$V"
```

Results land at `out/scans/continuum-pipeline-<VERSION>.json` and
`out/scans/continuum-import-<VERSION>.json` on the host. Prefer a plain-text
read instead of JSON: swap `--format json` for `--format table` and the
`.json` extension for `.txt` — same mount, same everything else.

The `:z` on both mounts relabels them for SELinux hosts, the same reason
every bind mount in `scripts/lib.sh` carries one — harmless where SELinux
isn't enforcing, required where it is. `trivy-cache` is a named volume so the
vulnerability DB survives between scans instead of redownloading every run.

**Two details that matter, both from hitting them:**

- **Name the image with its `localhost/` prefix.** Podman stores a locally
  built, unpushed image as `localhost/continuum-pipeline:0.5.0` (check with
  `podman images`), but Trivy's client normalizes a bare
  `continuum-pipeline:0.5.0` to `docker.io/library/...` before asking the
  socket for it — a mismatch, not a missing image. Without the prefix, Trivy
  fails to find it on `podman`, falls through to `docker` and `containerd`
  (neither present here), and ends by actually trying to pull the name from
  Docker Hub, which is where an `UNAUTHORIZED` error comes from.
- **`--image-src podman`** skips the `docker`/`containerd`/`remote` probing
  entirely (Trivy's default tries all four in order) — worth it since here
  only `podman` will ever succeed, and the failed docker-socket and
  containerd-socket attempts add nothing but noise to the output.
- **Reading the json result right after `podman run --rm` exits can show
  `"Vulnerabilities": null`** on the os-pkgs or lang-pkgs target instead of a
  populated (or even empty) list. This is not Trivy misreporting: Trivy's own
  log (stderr - redirect it to a file, `tools/trivy-scan.sh` always does)
  shows it genuinely found and scanned the packages every time this has been
  seen (`pkg_num=112`, `[python-pkg] Detecting vulnerabilities...`), and
  re-reading the same file later from a separate process, nothing re-run,
  always shows it fully populated - sometimes after a couple of seconds,
  sometimes not inside a retry loop that kept re-reading, from the same
  process, for over a minute. That shape (a *separate* later read always
  works; retrying *within one process* is not reliable) looks like a
  read-after-write visibility race specific to this sandboxed environment
  (`--output` writes the file from inside the container, through the `:z`
  bind mount, and rootless Podman's storage - fuse-overlayfs here - does not
  guarantee that write is visible to every reader the instant `podman run`
  returns), but it was never nailed down conclusively past that, so do not
  take this as the final word if you hit it somewhere else. Re-running the
  whole scan is the wrong response either way: it is slow, and a real defect
  would look identical, so `tools/trivy-scan.sh` instead cross-checks the log
  - if it shows Trivy actually scanned real packages, a `null` result is
  reported as a warning to double-check by hand, not a failure, because
  calling it a failure has been wrong every time in testing. Reading the
  commands above by hand, do the same: before trusting a `null`, check the
  log for `pkg_num=` and re-read the json file from a fresh command a moment
  later rather than re-scanning.

## 3. A shell function, if you'll do this often

Always mounts `out/scans/`; pass `--format`/`--output` yourself so the
destination stays explicit rather than a name the function guesses:

```bash
trivy-podman() {
    mkdir -p out/scans
    podman run --rm \
        -v "/run/user/$(id -u)/podman/podman.sock:/run/podman/podman.sock:z" \
        -v trivy-cache:/root/.cache \
        -v "$(pwd)/out/scans:/out:z" \
        docker.io/aquasec/trivy:0.74.0 \
        image --image-src podman --podman-host /run/podman/podman.sock \
        "$@"
}
```

```bash
V=$(cat VERSION)
trivy-podman --format json --output "/out/continuum-pipeline-$V.json" \
    "localhost/continuum-pipeline:$V"
trivy-podman --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed \
    --format json --output "/out/continuum-import-$V.json" \
    "localhost/continuum-import:$V"
```

## Alternative: no persistent socket

If you'd rather not leave a socket enabled, start one just for the scan:

```bash
SOCKET=$(mktemp -u)
podman system service --time=60 "unix://$SOCKET" &
PID=$!

mkdir -p out/scans
V=$(cat VERSION)
podman run --rm \
  -v "$SOCKET:/run/podman/podman.sock:z" \
  -v trivy-cache:/root/.cache \
  -v "$(pwd)/out/scans:/out:z" \
  docker.io/aquasec/trivy:0.74.0 \
  image --image-src podman --podman-host /run/podman/podman.sock \
  --format json --output "/out/continuum-pipeline-$V.json" \
  "localhost/continuum-pipeline:$V"

kill "$PID"
```

## Useful flags

```bash
# fail on real, fixable severities only
trivy-podman --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed \
    --format json --output "/out/continuum-pipeline-$V.json" \
    "localhost/continuum-pipeline:$V"

# vulnerabilities + secrets + misconfig in one pass
trivy-podman --scanners vuln,secret,misconfig \
    --format json --output "/out/continuum-pipeline-$V.json" \
    "localhost/continuum-pipeline:$V"
```

## Offline scanning (matches this project's air-gap model)

Trivy fetches its vulnerability DB from a registry on first run — the same
class of runtime-network trap `SPEC.md` §8.2 lists for Typst, DuckDB and pip.
Pre-fetch it into the cache volume outside the air gap, then scan with
`--skip-db-update` inside:

```bash
# outside, once
podman run --rm -v trivy-cache:/root/.cache docker.io/aquasec/trivy:0.74.0 \
    image --download-db-only

# carry the trivy-cache volume in (podman volume export/import), then inside:
trivy-podman --skip-db-update --offline-scan \
    --format json --output "/out/continuum-pipeline-$V.json" \
    "localhost/continuum-pipeline:$V"
```

To move the volume across the gap:
`podman volume export trivy-cache -o trivy-cache.tar` outside,
`podman volume import trivy-cache trivy-cache.tar` inside.

## Do not use `scripts/lib.sh`'s `run_in_container` for this

It always mounts the repo read-only at `/work` and defaults to
`--network=none`, which is right for the pipeline's own tools but has nothing
to do with scanning an image. Run `podman run` for Trivy directly as above,
not through `./check.sh` or a similar entry point.
