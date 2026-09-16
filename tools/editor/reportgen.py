"""
reportgen.py - runs the report pipeline in-process, for the editor's
"generate report" button.

report.sh drives snapshot.py, build_model.py and the five renderers through
run_in_container - one container per step, per SPEC 6.5. That machinery
starts each container from the *host*, using the engine binary, which
tools/editor/app.py does not have: it already runs inside the pipeline
container edit.sh started (docker/Dockerfile.pipeline, the same image
report.sh uses - see edit.sh and scripts/lib.sh), with no engine socket
mounted in and no license to gain one (CLAUDE.md: all container knowledge
lives in scripts/lib.sh). Nesting a container from inside a container is not
this tool's problem to solve.

So this calls the same tools report.sh calls - snapshot.main(), then
build_model.main(), then each renderer's main() - as plain function calls in
this process instead. Every one of them already returns an int exit code
rather than calling sys.exit() itself (see the `if __name__ == "__main__":
sys.exit(main())` at the bottom of each), which is what makes this safe:
nothing here duplicates their logic, it only sequences it.

Renderers are loaded by file path with a unique module name, the way
tests/conftest.py loads them, rather than `import`ed by name: SPEC 10 (M5)
records that tools/render/pptx.py collides by name with the python-pptx
package it imports, and pptx.py's own top-of-file fix for that (temporarily
dropping its own directory from sys.path) only works if nothing has already
wedged tools/render/ onto sys.path some other way - which this avoids by
never adding that directory to sys.path at all.

One thing is deliberately left out: report.sh resolves git_sha, git_tag and
the image digest on the *host* (snapshot.sh's comment explains why - the
image has no git binary) and passes them in. This process has no host access
either, so it leaves those to snapshot.py's own fallback, which reads
.git/HEAD directly (repo_root() finds /work/.git, mounted read-write same as
everywhere else) - real, just without an image digest. A report generated
this way is a convenience preview, not a replacement for the daily
./report.sh run; nothing about that is hidden from the person clicking the
button (see build() below).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
RENDERERS = ("charts", "xlsx", "txt", "pdf", "pptx")


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"continuum_editor_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@dataclass
class Step:
    name: str
    ok: bool
    output: str


@dataclass
class Result:
    date: str
    ok: bool
    report_dir: str  # relative to the repo root, e.g. "out/reports/2026-09-16"
    steps: list[Step] = field(default_factory=list)


def generate(projects_dir: Path, schema_dir: Path, date: str, lang: str = "de") -> Result:
    """Run snapshot -> build_model -> charts/xlsx/txt/pdf/pptx for `date`,
    writing into out/reports/<date>/ exactly like ./report.sh does. Stops at
    the first failing step; every step already run is still reported."""

    tables_dir = REPO_ROOT / "out" / "tables"
    report_dir = REPO_ROOT / "out" / "reports" / date
    report_dir.mkdir(parents=True, exist_ok=True)
    model_path = report_dir / "report_model.json"

    result = Result(date=date, ok=True, report_dir=str(report_dir.relative_to(REPO_ROOT)))

    def run(name: str, func, argv: list[str]) -> bool:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                code = func(argv)
            except SystemExit as exc:  # argparse would call this on bad argv
                code = exc.code if isinstance(exc.code, int) else 1
            except Exception as exc:  # pragma: no cover - defence in depth
                buf.write(f"{type(exc).__name__}: {exc}\n")
                code = 1
        ok = code == 0
        result.steps.append(Step(name=name, ok=ok, output=buf.getvalue()))
        if not ok:
            result.ok = False
        return ok

    snapshot = _load("snapshot", REPO_ROOT / "tools" / "snapshot.py")
    if not run("snapshot", snapshot.main, [
        "--projects", str(projects_dir),
        "--schema", str(schema_dir),
        "--out", str(tables_dir),
        "--as-of", date,
    ]):
        return result

    build_model = _load("build_model", REPO_ROOT / "tools" / "build_model.py")
    if not run("build_model", build_model.main, [
        "--snapshot", str(tables_dir),
        "--spec", str(REPO_ROOT / "reports" / "daily.yaml"),
        "--definitions", str(REPO_ROOT / "reports" / "definitions.yaml"),
        "--schema", str(schema_dir),
        "--out", str(model_path),
        "--as-of", date,
    ]):
        return result

    # charts.py first: the other renderers embed its PNGs (SPEC 6.4).
    for name in RENDERERS:
        module = _load(name, REPO_ROOT / "tools" / "render" / f"{name}.py")
        if not run(name, module.main, [
            "--model", str(model_path),
            "--out", str(report_dir),
            "--lang", lang,
        ]):
            return result

    return result
