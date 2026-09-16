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

## 2. Run Trivy in a container, pointed at the socket

Pin the Trivy image version rather than `:latest` — this project pins every
renderer for reproducibility (`SPEC.md` §9), and a scanner that silently
changes version between runs is the same trap. Check
[the current release](https://github.com/aquasecurity/trivy/releases) and
update the tag below when you bump it deliberately.

```bash
TRIVY_IMAGE=docker.io/aquasec/trivy:0.74.0
V=$(cat VERSION)

podman run --rm \
  -v "/run/user/$(id -u)/podman/podman.sock:/run/podman/podman.sock:z" \
  -v trivy-cache:/root/.cache \
  "$TRIVY_IMAGE" \
  image --image-src podman --podman-host /run/podman/podman.sock \
  "localhost/continuum-pipeline:$V"

podman run --rm \
  -v "/run/user/$(id -u)/podman/podman.sock:/run/podman/podman.sock:z" \
  -v trivy-cache:/root/.cache \
  "$TRIVY_IMAGE" \
  image --image-src podman --podman-host /run/podman/podman.sock \
  "localhost/continuum-import:$V"
```

The `:z` on the socket mount relabels it for SELinux hosts, the same reason
every bind mount in `scripts/lib.sh` carries one — harmless where SELinux
isn't enforcing, required where it is. `trivy-cache` is a named volume so the
vulnerability DB survives between scans instead of redownloading every run.

Trivy reads the image straight out of Podman's store over the socket; nothing
needs to be exported to a tarball and nothing needs mounting from
`/var/lib/containers/storage`.

**Two details that matter, both from hitting them:**

- **Name the image with its `localhost/` prefix.** Podman stores a locally
  built, unpushed image as `localhost/continuum-pipeline:0.5.0` (check with
  `podman images`), but Trivy's client normalizes a bare
  `continuum-pipeline:0.5.0` to `docker.io/library/...` before asking the
  socket for it — a mismatch, not a missing image. Without the prefix, Trivy
  fails to find it on `podman`, falls through to `docker` and `containerd`
  (neither present here), and ends by actually trying to pull the name from
  Docker Hub, which is where the `UNAUTHORIZED` comes from.
- **`--image-src podman`** skips the `docker`/`containerd`/`remote` probing
  entirely (Trivy's default tries all four in order) — worth it since here
  only `podman` will ever succeed, and the failed docker-socket and
  containerd-socket attempts add nothing but noise to the output.

## 3. A shell function, if you'll do this often

```bash
trivy-podman() {
    podman run --rm \
        -v "/run/user/$(id -u)/podman/podman.sock:/run/podman/podman.sock:z" \
        -v trivy-cache:/root/.cache \
        docker.io/aquasec/trivy:0.74.0 \
        image --image-src podman --podman-host /run/podman/podman.sock \
        "$@"
}
```

```bash
V=$(cat VERSION)
trivy-podman "localhost/continuum-pipeline:$V"
trivy-podman --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed "localhost/continuum-import:$V"
```

## Alternative: no persistent socket

If you'd rather not leave a socket enabled, start one just for the scan:

```bash
SOCKET=$(mktemp -u)
podman system service --time=60 "unix://$SOCKET" &
PID=$!

podman run --rm \
  -v "$SOCKET:/run/podman/podman.sock:z" \
  -v trivy-cache:/root/.cache \
  docker.io/aquasec/trivy:0.74.0 \
  image --image-src podman --podman-host /run/podman/podman.sock \
  "localhost/continuum-pipeline:$(cat VERSION)"

kill "$PID"
```

## Useful flags

```bash
# fail on real, fixable severities only
trivy-podman --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed "localhost/continuum-pipeline:$V"

# vulnerabilities + secrets + misconfig in one pass
trivy-podman --scanners vuln,secret,misconfig "localhost/continuum-pipeline:$V"

# machine-readable, for CI
trivy-podman --format json --output pipeline-scan.json "localhost/continuum-pipeline:$V"
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
trivy-podman --skip-db-update --offline-scan "localhost/continuum-pipeline:$V"
```

To move the volume across the gap:
`podman volume export trivy-cache -o trivy-cache.tar` outside,
`podman volume import trivy-cache trivy-cache.tar` inside.

## Do not use `scripts/lib.sh`'s `run_in_container` for this

It always mounts the repo read-only at `/work` and defaults to
`--network=none`, which is right for the pipeline's own tools but has nothing
to do with scanning an image. Run `podman run` for Trivy directly as above,
not through `./check.sh` or a similar entry point.
