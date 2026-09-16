# Scanning the project images with Trivy

This project builds two images (`SPEC.md` §8.1):

| image | Dockerfile | built by |
|---|---|---|
| `continuum-pipeline:<VERSION>` | `docker/Dockerfile.pipeline` | `./check.sh`, `./snapshot.sh`, `./report.sh`, `./verify.sh`, `./shell.sh`, `./serve.sh` |
| `continuum-import:<VERSION>` | `docker/Dockerfile.import` | `./merge/merge.sh`, `./import/import.sh` |

`<VERSION>` is the contents of `VERSION` at the repo root (currently `0.5.0`).
Neither image is pulled from a registry — `scripts/lib.sh` builds them locally
on first use (`ensure_image`) — so scanning them means scanning what you just
built, not something fetched.

This howto uses the native `trivy` binary, not the containerized image. Trivy
talks to whichever engine looks like Docker; podman is the default engine
here, so most commands go through a saved archive rather than a live socket.

## Install Trivy (apt)

Debian/Ubuntu, via Aqua Security's apt repo:

```bash
sudo apt-get install -y wget gnupg
wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | \
    sudo gpg --dearmor -o /usr/share/keyrings/trivy.gpg
echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb generic main" | \
    sudo tee /etc/apt/sources.list.d/trivy.list
sudo apt-get update
sudo apt-get install -y trivy
trivy --version
```

(If this repo's own Ubuntu release ships a recent enough `trivy` package
directly, `sudo apt-get install -y trivy` without adding the repo also works —
check `apt-cache policy trivy` first. The repo above is the reliable path when
it doesn't.)

## Build the images first

```bash
cd /path/to/continuum
V=$(cat VERSION)
podman build -f docker/Dockerfile.pipeline -t continuum-pipeline:$V .
podman build -f docker/Dockerfile.import  -t continuum-import:$V  .
```

(Or just run `./check.sh` / `./import/import.sh` once — either builds the
image it needs.)

## Scan by name, via the podman socket

Trivy's Docker-API client works against podman's socket too:

```bash
systemctl --user start podman.socket
export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock

trivy image continuum-pipeline:$V
trivy image continuum-import:$V
```

## Scan by archive (no socket needed)

More reliable, and it scans exactly what you'd carry into the air gap if you
were bundling for M6:

```bash
podman save --format oci-archive -o /tmp/pipeline.tar continuum-pipeline:$V
trivy image --input /tmp/pipeline.tar

podman save --format oci-archive -o /tmp/import.tar continuum-import:$V
trivy image --input /tmp/import.tar
```

## Useful flags

```bash
# fail on real, fixable severities only
trivy image --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed continuum-pipeline:$V

# vulnerabilities + secrets + misconfig in one pass
trivy image --scanners vuln,secret,misconfig continuum-pipeline:$V

# machine-readable, for CI
trivy image --format json --output pipeline-scan.json continuum-pipeline:$V
```

## Offline scanning (matches this project's air-gap model)

Trivy fetches its vulnerability DB from GitHub on first run — the same class
of runtime-network trap `SPEC.md` §8.2 lists for Typst, DuckDB and pip.
Pre-fetch it outside, carry the cache in, scan with no network inside:

```bash
# outside the air gap, once
trivy image --download-db-only --cache-dir ./trivy-cache

# carry ./trivy-cache in alongside the image archives

# inside, fully offline
trivy image --cache-dir ./trivy-cache --skip-db-update --offline-scan \
    --input /tmp/pipeline.tar
```

## Do not use `scripts/lib.sh`'s `run_in_container` for this

It always mounts the repo read-only at `/work` and defaults to
`--network=none` (`scripts/lib.sh`), which is right for the pipeline's own
tools but has nothing to do with scanning an image. Run `trivy` directly as
above, not through `./check.sh` or a similar entry point.
