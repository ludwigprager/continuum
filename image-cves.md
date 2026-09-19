# The images' CVEs, and what is actually needed to run each step

A companion to [`trivy.md`](trivy.md), which is *how* to scan. This is what
the scan says, where it comes from, and which of it can be removed without
taking a pipeline step with it.

The question behind it: **the pipeline image reports CVEs — which binaries
and packages does each step actually need, and what can go?**

The short answer is that almost nothing in the report belongs to this
project. Every finding is in a Debian package, none of them has a fix
available, and the packages this repository installs on top of the base image
contribute **zero** CVEs between them. The lever that moves the number is the
base image, not the dependency list.

---

## 1. The numbers

All measured on 2026-09-19 with Trivy 0.74.0 (`--scanners vuln`) against the
locally built images, using the method in `trivy.md`. The two `:test`
images were built for this analysis and are not part of the repository.

| image | findings | distinct CVEs | CRITICAL | HIGH |
|---|---:|---:|---:|---:|
| `continuum-pipeline:0.5.0` (today) | 304 | 120 | 5 | 16 |
| `continuum-import:0.5.0` (today) | 304 | 120 | 5 | 16 |
| `python:3.12-slim-bookworm` (the base, for reference) | 276 | 132 | 5 | 19 |
| `continuum-trim:test` — unused packages purged | 276 | 107 | 4 | 14 |
| `continuum-trixie:test` — same Dockerfile on Debian 13 | **187** | **65** | **0** | **8** |

Two things to read off that table before anything else.

**Both project images report exactly the same CVEs.** The import image is
`python:3.12-slim-bookworm` + `locales` + PyYAML; the pipeline image is the
same base + `locales` + `shellcheck` + two font packages + Typst + eleven
pinned wheels. 503 MB against 155 MB, and an identical security report. That
is not a coincidence, it is the finding: the exposure is the base image.

> The `out/scans/continuum-import-0.5.0.json` currently on disk (2026-09-16)
> reports `"Vulnerabilities": null` for both targets — the read-after-write
> race `trivy.md` documents, not a clean image. Its log shows Trivy really did
> scan `pkg_num=107`. Re-scanned for this analysis, it reports 304. Treat a
> `null` as "not read yet", exactly as `trivy.md` says.

**Nothing is fixable.** All 304 findings have no `FixedVersion` — 232
`affected`, 53 `fix_deferred`, 14 `will_not_fix`. `--ignore-unfixed` reduces
the report to nothing. There is no patch to apply, no `apt-get upgrade` that
helps, and the existing decision in both Dockerfiles not to reach for a
blanket upgrade costs nothing here.

### 1.1 Findings are not CVEs

304 findings are 120 distinct CVEs, because Trivy reports one finding per
*binary* package and Debian ships one source package as many:

| source package | binary packages in the image | findings | distinct CVEs |
|---|---:|---:|---:|
| glibc (`libc6`, `libc-bin`, `libc-l10n`, `locales`) | 4 | 80 | 20 |
| util-linux (`util-linux`, `-extra`, `mount`, `bsdutils`, `libuuid1`, `libsmartcols1`, `libmount1`, `libblkid1`) | 8 | 88 | 11 |
| ncurses (`libncursesw6`, `libtinfo6`, `ncurses-bin`, `ncurses-base`) | 4 | 12 | 3 |
| krb5 (`libkrb5-3`, `libk5crypto3`, `libkrb5support0`, `libgssapi-krb5-2`) | 4 | 16 | 4 |

Verified by hashing each package's sorted CVE-ID list: all four glibc
packages carry byte-identical lists, and so do all eight util-linux ones.
**Two source packages account for 168 of the 304 findings and 31 of the 120
CVEs.**

This matters for anything aimed at the number rather than at the risk.
Removing `locales` and `libc-l10n` deletes 40 findings and **zero** CVEs —
the same glibc bugs stay in `libc6`, which is not going anywhere. Same for
the `openssl` command-line tool: 7 findings, 0 CVEs not already in `libssl3`,
which Python's `_ssl` needs.

### 1.2 What the Dockerfiles already buy

Two mitigations are in place and measurably working:

- **`pip uninstall pip setuptools wheel`** removes the whole `lang-pkgs`
  class. The bookworm base reports 6 pip findings; both project images report
  0. On Debian 13 the base additionally reports `setuptools` (1 HIGH) and
  `msgpack` — the trixie test image reports 0, for the same reason.
- **`libpcre2-8-0=10.42-1+deb12u1`** removes 6 CVEs the base still has.

The whole difference between the base image and the pipeline image is those
two wins against the 40 duplicate glibc findings that `locales` drags in:
`270 − 6 + 20 + 20 = 304`. Nothing else the project installs registers at
all.

---

## 2. What each step needs

### 2.1 Entry point → image → command

`scripts/lib.sh` is the only thing that knows about containers (SPEC 6.5);
every entry point below goes through its `run_in_container`.

| entry point | image | what runs in the container |
|---|---|---|
| `./merge/merge.sh` | import | `python3 merge/merge_csv.py` |
| `./import/import.sh` | import | `python3 import/import_csv.py {profile,convert,derive-schema}` |
| `./check.sh` | pipeline | `python3 tools/validate.py` |
| `./snapshot.sh` | pipeline | `python3 tools/snapshot.py` |
| `./report.sh` | pipeline | `python3 tools/build_model.py`, then `tools/render/{charts,xlsx,txt,pdf,pptx}.py` |
| `./serve.sh` | pipeline | `python3 -m http.server` |
| `./edit.sh` | pipeline | `python3 tools/editor/app.py` |
| `./verify.sh` | pipeline | `shellcheck`, `python3 -m pytest`, then a full `./report.sh` |
| `./shell.sh` | pipeline | `bash` |

Every one of them is `python3` and nothing else, except `shellcheck` and
`bash`. There is exactly one external binary on the daily path: `typst`,
which `tools/render/pdf.py` shells out to (`subprocess.run`) and nothing else
in the tree does.

### 2.2 Tool → third-party Python package

Measured by import, with the standard library filtered out:

| tool | needs |
|---|---|
| `merge/merge_csv.py` | *(stdlib only — deliberate, SPEC §10)* |
| `import/import_csv.py` | `PyYAML` |
| `tools/validate.py` | `ruamel.yaml`, `jsonschema` |
| `tools/snapshot.py` | `ruamel.yaml`, `duckdb` |
| `tools/build_model.py` | `ruamel.yaml`, `duckdb` |
| `tools/render/charts.py` | `matplotlib` (brings numpy, pillow, fontTools, kiwisolver, pyparsing) |
| `tools/render/xlsx.py` | `openpyxl` (+ `pillow`, for the dashboard images) |
| `tools/render/txt.py` | `jinja2` |
| `tools/render/pdf.py` | *(none — it drives the `typst` binary)* |
| `tools/render/pptx.py` | `python-pptx` (brings lxml, XlsxWriter) |
| `tools/editor/*` | `ruamel.yaml`, `jinja2`, plus `tools/validate.py` |
| `tools/make_{workbook,deck}_template.py` | `openpyxl` / `python-pptx` |
| test suite | `pytest` |

Every wheel in `requirements.pipeline.txt` is reachable from a step, and
`requirements.import.txt` is one line. **There is nothing to drop here** —
and it would not help if there were: `lang-pkgs` reports 0 findings.

### 2.3 What the OS actually has to supply

Loaded every tool in the tree in one process and read `/proc/self/maps`. The
complete set of system shared objects the pipeline touches:

```
ld-linux-x86-64.so.2  libc.so.6  libm.so.6  libdl.so.2  libpthread.so.0
librt.so.1  libgcc_s.so.1  libstdc++.so.6        # C/C++ runtime
libcrypto.so.3  libssl.so.3                      # _hashlib, _ssl
libz.so.1  libbz2.so.1.0  liblzma.so.5           # zlib/bz2/lzma modules
libffi.so.8                                      # _ctypes
libuuid.so.1                                     # _uuid
```

And that is all. Three things account for why the list is so short:

- **`typst` is statically linked** (`typst-x86_64-unknown-linux-musl` — the
  Dockerfile already picks the musl build). It needs nothing from Debian.
- **The wheels vendor their own C libraries.** matplotlib, pillow, numpy and
  lxml ship `libfreetype-*.so`, `libpng16-*.so`, `libjpeg-*.so`,
  `libharfbuzz-*.so`, `libopenblas*.so` and friends under `*.libs/`, with
  hashed filenames, and do not link the Debian ones. The image's
  `libfreetype`/`libpng` would be unused even if they were installed.
- **`shellcheck` needs only `libc`, `libm`, `libffi`, `libgmp`** — all
  present for other reasons.

The fonts are the one thing that is needed but never linked: `fonts-dejavu`
and `fonts-liberation` are read as *files*, by matplotlib's baked cache and
by Typst, and both assert at startup that the family they asked for is the
one they got (SPEC 8.2). They contribute no CVEs. `locales` is likewise a
data dependency — `de_DE.UTF-8` for German number and date formatting
(SPEC 8.1) — and contributes no CVEs of its own either.

---

## 3. Classifying the image against that

### 3.1 Needed

Carrying real CVEs, and not removable, because a step loads them:

| package | findings | CVEs | why it stays |
|---|---:|---:|---|
| `libc6` + `libc-bin` | 40 | 20 | everything |
| `libssl3` | 7 | 7 | `_ssl`, `_hashlib` (sha256 in every manifest) |
| `zlib1g` | 3 | 3 (1 CRITICAL) | `zlib` module; xlsx and pptx are zip files |
| `libbz2-1.0`, `liblzma5` | 2 | 2 | `bz2` / `lzma` modules |
| `libstdc++6`, `libgcc-s1` | 2 | 2 | duckdb, the compiled wheels |
| `libattr1`, `libacl1` | 3 | 3 | pulled in under coreutils/util-linux |

### 3.2 Needed as Debian, not as pipeline

Essential or `required` Debian packages. Removing them breaks `dpkg`, the
shell, or the image's ability to be built on at all — and they are where the
worst findings live:

| package | findings | CVEs | note |
|---|---:|---:|---|
| `perl-base` | 18 | 18, **3 of the 5 CRITICALs** | `Essential: yes`. Nothing in this project runs Perl. |
| util-linux family | 88 | 11 | `Essential: yes` on `bsdutils`, `mount`, `util-linux` |
| `coreutils`, `bash`, `tar`, `gzip`, `diffutils`, `ncurses-*`, `login`, `passwd`, `sysvinit-utils` | ~25 | ~12 | `Essential`/`required` |
| `apt`, `libapt-pkg6.0`, `gpgv`, `libgnutls30`, `libp11-kit0`, `libtasn1-6`, `libsystemd0`, `libudev1` | ~30 | ~14 | the package manager and its transitive closure |

`perl-base` is the sharp one: three CRITICAL CVEs in a language this
repository never invokes, kept only because Debian marks it essential.

### 3.3 Unused and removable

Present, unused by any step, and nothing installed depends on them
(`apt-cache --installed rdepends` returns empty or a closed cluster):

| package(s) | findings | CVEs unique to them | what breaks |
|---|---:|---:|---|
| `libsqlite3-0` | 9 | 9, **1 CRITICAL** | `import sqlite3`. Nothing does — duckdb statically links its own engine. |
| `libgssapi-krb5-2`, `libkrb5-3`, `libk5crypto3`, `libkrb5support0`, `libnsl2`, `libtirpc3` | 16 | 4 (all LOW) | nothing; a closed cluster rooted at `libnsl2` |
| `libreadline8`, `libgdbm6`, `libncursesw6` | 3 | 0 | `readline`/`dbm`/`curses` modules — line editing in an interactive `python3`, which `./shell.sh` does not depend on |

**Verified, not assumed.** `continuum-trim:test` purges exactly those,
and `./verify.sh` against it: shellcheck passes, the schema self-test passes,
**172 of 173 tests pass**, the full `--network=none` pipeline produces all
eight artifacts byte-identically across two runs, the report server serves,
and the exit-code contract holds under both podman and docker.

> The one failure, `tests/test_editor.py::test_reportgen_stops_at_the_first_failing_step`,
> is **pre-existing and unrelated**: it reproduces identically on the
> untrimmed `continuum-pipeline:0.5.0`. The test writes to
> `/work/out/reports/reportgen-unit-test`, but `verify.sh` runs pytest with
> `/work` mounted read-only, so it fails with `OSError: [Errno 30]
> Read-only file system`. Worth fixing on its own; it is not a CVE question.

Measured effect: **304 → 276 findings, 120 → 107 CVEs, CRITICAL 5 → 4,
HIGH 16 → 14.**

### 3.4 Cosmetic only

Removable, and lowers the headline number without lowering the risk at all:

| package(s) | findings removed | CVEs removed |
|---|---:|---:|
| `locales`, `libc-l10n` | 40 | **0** (all shared with `libc6`) |
| `openssl` (the CLI) | 7 | **0** (all shared with `libssl3`) |

And `locales` cannot go anyway without losing `de_DE.UTF-8`, which SPEC 8.1
requires. Listed here only so that nobody mistakes a 47-finding drop for a
security improvement.

### 3.5 Not a CVE question, but worth knowing

`shellcheck` (19 MB) and `pytest` are development tools on a *production*
image. They carry no CVEs, so they do not show up above, but they are only
ever used by `./verify.sh`. See §4, option D.

The 503 MB, for reference: 345 MB `/usr/local` (284 MB of it Python — duckdb
58 MB, numpy 70 MB, matplotlib 34 MB, fontTools 29 MB), 54 MB `typst`, 19 MB
`shellcheck`, 12 MB fonts.

---

## 4. Options

### A. Do nothing, and say so

Defensible today, and it needs to be *stated* rather than left implicit:

- **Zero findings are fixable.** There is no patched version of anything.
- **The runtime has no network** (SPEC 1, 8.2), and `run_in_container`
  defaults to `--network=none`. The pipeline reads local YAML and writes
  local files. `libsqlite3`, krb5 and Perl are not reachable by anything.
- **The two listening services** — `./serve.sh` (read-only over
  `out/reports`) and `./edit.sh` (read-write over `projects/`) — are the only
  attack surface that exists, and both are Python `http.server`, not any of
  the packages carrying CVEs. Their real exposure question is SPEC §12.9's
  no-authentication tradeoff, which this analysis does not touch.

What this option does not survive is a scanner gate in someone else's CI
with `--severity CRITICAL --exit-code 1`. `--ignore-unfixed` is the honest
flag to reach for, and it currently zeroes the report.

### B. Trim the unused packages

Measured in §3.3: −28 findings, −13 CVEs, one CRITICAL gone. One `RUN
apt-get purge` in `Dockerfile.pipeline`, verified working. Cheap, small,
honest — and it removes the only CRITICAL that is in the image for no reason
at all (`libsqlite3-0`).

Do the same in `Dockerfile.import`, where the whole renderer stack is absent
and so is any user of sqlite or krb5.

### C. Move the base to Debian 13 (trixie) — the one that matters

`continuum-trixie:test` is `Dockerfile.pipeline` with three edits:

```diff
-FROM python:3.12-slim-bookworm AS typst
+FROM python:3.13-slim-trixie AS typst
-FROM python:3.12-slim-bookworm
+FROM python:3.13-slim-trixie
-        libpcre2-8-0=10.42-1+deb12u1; \
+        libpcre2-8-0=10.46-1~deb13u2; \
```

**304 → 187 findings, 120 → 65 CVEs, CRITICAL 5 → 0, HIGH 16 → 8.** Half the
CVEs and every CRITICAL, for a three-line change.

The friction is small and was checked:

- **Every pin in `requirements.pipeline.txt` resolves on Python 3.13** —
  `pip install --dry-run` against all eleven, exit 0. cp313 wheels exist for
  duckdb 1.5.5, matplotlib 3.11.2, pillow 12.3.0, python-pptx 1.0.2, lxml and
  numpy.
- **Every apt package is available**: `locales` 2.41-12+deb13u4, `tzdata`
  2026c, `shellcheck` 0.10.0-1, `fonts-dejavu` 2.37-8, `fonts-liberation`
  2.1.5-3.
- **The pcre2 pin must change.** `10.42-1+deb12u1` does not exist on trixie
  and the build fails loudly, which is the right failure. Trixie ships
  `10.46-1~deb13u2`, which is past the 6 CVEs the pin was closing.
- **Typst is unaffected** — a static musl binary, pinned by version and
  sha256, base-independent.
- `libkrb5*`, `libnsl2`, `libtirpc3`, `libreadline8` and `libgdbm6` are gone
  from trixie's slim image already, so most of option B is free there.
  `libsqlite3-0` remains and is still worth purging (4 findings, unused).

What is left afterwards is 8 HIGH CVEs, and they are all in the packages
§3.2 says cannot go: `bsdutils`/util-linux (4), `libsystemd0`, `libacl1`,
`libncursesw6`, `perl-base`.

**`./verify.sh` against `continuum-trixie:test` passes**, with the same
172/173 as §3.3 and the same single pre-existing read-only-mount failure. All
eight artifacts are byte-identical across two runs, the server serves, and the
exit-code contract holds under both engines. Python 3.13 changed nothing about
what the pipeline does.

The real cost is this project's actual currency: **reproducibility across the
switch**. Building the fixture report on both images and comparing:

| artifact | differs? | why |
|---|---|---|
| `report_model.json` | only `provenance.image_digest` | by design (SPEC 5.2) — otherwise byte-identical |
| `report.txt` | only the `Image` line | the same digest, printed |
| `by_*.png` | **yes, pixels differ** | the font files changed |
| `report.pdf`, `report.xlsx`, `deck.pptx` | **yes** | they embed those PNGs |

So the **data layer is unaffected**: every number, label and table the model
computes is identical on Python 3.12 and 3.13, which is the part that would
have been expensive to be wrong about. What moves is rasterisation, and the
cause is not Python at all — Debian reships the fonts:

```
                                   bookworm                          trixie
LiberationSans-Regular.ttf  dceebf9db79d2acf4…              dea13973ce7da62f…
DejaVuSans.ttf              4cc160d1da14d459…               94d8e21951bb4eb0…
```

Different glyph outlines, different pixels, therefore different PNG bytes and
different PDF/XLSX/PPTX bytes. Nothing in SPEC requires an artifact to match
one built on a *different* image — `verify.sh` asserts that two runs of the
same image agree, which is the property that matters, and it still holds. But
the day this is switched is a day every rendered output changes, so it belongs
in a deliberate, VERSION-bumping commit of its own, not folded into another
change.

### D. Split the dev tooling out of the runtime image

Structural, and the natural extension of SPEC 8.1's existing pipeline/import
split. `shellcheck` and `pytest` exist for `./verify.sh` alone. A
`Dockerfile.dev` built `FROM` the pipeline image, adding those two, would
leave the image that runs daily in production with neither.

It buys **no CVEs today** — neither tool carries any — so it is an attack-surface
and size argument (19 MB, a Haskell runtime, a test framework), not a scanner
argument. It also costs a third image to pin, bundle and checksum for M6,
which SPEC 8.1 explicitly weighs against elsewhere (the `nginx` decision in
§8.3). **Probably not worth it**, and named here so the option is on the
record rather than rediscovered.

The more aggressive version — distroless or a `FROM scratch` runtime — would
finally shed `perl-base`, `apt`, `coreutils` and util-linux, which is where
everything unfixable in §3.2 lives. It is also incompatible with
`./shell.sh`, `./verify.sh`'s shellcheck step, and the `bash` that
`--interactive` assumes. Not recommended; recorded because it is the only
path that reaches the three CRITICAL Perl CVEs.

### Recommendation

**C, then B, in one commit, with a VERSION bump** — the base bump is where
the CVEs are, the trim is a few lines more and removes what the bump leaves
behind (`libsqlite3-0`). Both were built and run through `./verify.sh` for
this analysis and both pass. Accept that the rendered artifacts change bytes
once — the model does not — and re-scan to confirm.

If the bump has to wait, **B alone** is a five-line, verified change that
takes out a CRITICAL.

And regardless of either: **write down that nothing is fixable.** A scanner
report with 304 red lines and no available patch is a fact about Debian
stable's CVE bookkeeping, not about this pipeline, and the next person to run
Trivy against it deserves to find that already said.

---

## 5. Reproducing this

```bash
# the scans (see trivy.md for the socket setup)
tools/trivy-scan.sh

# what the tools actually load: import them all, then read the memory map
./shell.sh python3 -c '
import importlib.util, re, sys
for p in ["tools/validate.py", "tools/snapshot.py", "tools/build_model.py",
          "tools/render/charts.py", "tools/render/xlsx.py", "tools/render/txt.py",
          "tools/render/pdf.py", "tools/render/pptx.py", "tools/editor/app.py"]:
    name = p.replace("/", "_")[:-3]
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # dataclasses needs the module registered
    spec.loader.exec_module(mod)
libs = {re.search(r"(/\S+\.so[^\s]*)", l).group(1)
        for l in open("/proc/self/maps") if re.search(r"/\S+\.so", l)}
print("\n".join(sorted(l for l in libs
                       if "site-packages" not in l and "lib-dynload" not in l)))'

# which packages the Dockerfiles add over the base
podman run --rm python:3.12-slim-bookworm dpkg-query -W -f '${Package}\n' | sort > base.txt
podman run --rm continuum-pipeline:$(cat VERSION) dpkg-query -W -f '${Package}\n' | sort > ours.txt
comm -13 base.txt ours.txt

# whether anything still needs a candidate for removal
podman run --rm --user 0 continuum-pipeline:$(cat VERSION) \
    apt-cache --installed rdepends libsqlite3-0
```

Findings drift with Trivy's database, not with the image: the same
`continuum-pipeline:0.5.0` reported 299 findings / 118 CVEs on 2026-09-16 and
304 / 120 on 2026-09-19. Compare like with like — re-scan the baseline in the
same run as the candidate, which is what every pair in §1 does.
