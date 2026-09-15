"""Tests for tools/render/pdf.py and templates/report.typ (M5).

The load-bearing ones:

  * test_the_pdf_is_byte_identical_across_runs - the model is reproducible
    (SPEC 5.2) and so is everything downstream of it. Typst stamps the current
    time into a PDF unless it is told not to, so this is the test that keeps
    --creation-timestamp wired to the model's own generated_at.
  * test_a_substituted_font_is_an_error - SPEC 8.2. Typst warns about an
    unknown family, substitutes, and exits 0. The report then looks different
    inside the air gap than it did outside and nothing says so.
  * test_the_template_imports_no_typst_package - the other half of SPEC 8.2
    for this renderer: `@preview/...` reaches for the package registry, which
    inside the air gap is a failed daily report.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_pipeline import FIXTURES, REPO, make_model, make_snapshot, run

PDF = REPO / "tools" / "render" / "pdf.py"
CHARTS = REPO / "tools" / "render" / "charts.py"
TEMPLATE = REPO / "templates" / "report.typ"


def report_dir(tmp_path: Path, name: str = "report",
               projects: Path = FIXTURES) -> tuple[Path, dict]:
    """A report directory shaped like the one ./report.sh builds.

    The model sits next to the chart PNGs it names, because that is what the
    template reads: `json("report_model.json")` resolves relative to the
    source file, and the images resolve relative to the same root.
    """
    out = tmp_path / name
    out.mkdir(parents=True, exist_ok=True)
    snapshot = make_snapshot(tmp_path / f"{name}-tables", projects)
    model_path, model = make_model(tmp_path / f"{name}-tables", snapshot)
    (out / "report_model.json").write_bytes(model_path.read_bytes())
    run(CHARTS, "--model", str(out / "report_model.json"), "--out", str(out))
    return out, model


def render(out: Path, *extra: str) -> Path:
    run(PDF, "--model", str(out / "report_model.json"), "--out", str(out),
        *extra)
    return out / "report.pdf"


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    out, model = report_dir(tmp_path_factory.mktemp("pdf"))
    return render(out), model, out


# --------------------------------------------------------------------------
# It is a PDF, and it came from the model
# --------------------------------------------------------------------------

def test_the_pdf_is_written_and_is_a_pdf(rendered):
    path, _, _ = rendered
    assert path.is_file()
    assert path.read_bytes()[:5] == b"%PDF-"
    assert path.stat().st_size > 10_000, "suspiciously small for a full report"


def test_the_charts_are_embedded_not_redrawn(tmp_path):
    """SPEC 2: charts are rendered once as PNG and embedded everywhere.

    So a report directory without them is not a PDF with three blank frames,
    it is a failed render that says which file is missing - the renderer has
    nothing to draw with and must not invent something.
    """
    out, model = report_dir(tmp_path)
    assert model["charts"], "the fixtures should exercise at least one chart"
    missing = out / model["charts"][0]["path"]
    missing.unlink()

    proc = subprocess.run(
        [sys.executable, str(PDF), "--model", str(out / "report_model.json"),
         "--out", str(out)], capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert missing.name in proc.stdout + proc.stderr


# --------------------------------------------------------------------------
# Reproducibility (SPEC 5.2)
# --------------------------------------------------------------------------

def test_the_pdf_is_byte_identical_across_runs(tmp_path):
    out, _ = report_dir(tmp_path)
    first = render(out).read_bytes()
    (out / "report.pdf").unlink()
    second = render(out).read_bytes()
    assert first == second


def test_the_timestamp_comes_from_the_model_not_the_clock(tmp_path):
    """Two models that differ only in generated_at must differ in the PDF.

    That is the proof the creation date is pinned to the model rather than
    left to Typst, which would otherwise read the wall clock and make every
    run of an unchanged model a different file.
    """
    out, model = report_dir(tmp_path)
    first = render(out).read_bytes()

    model["generated_at"] = "2020-01-02T03:04:05+01:00"
    (out / "report_model.json").write_text(json.dumps(model), encoding="utf-8")
    second = render(out).read_bytes()
    assert first != second


def test_lang_switches_the_labels(tmp_path):
    """Primary audience is German, both languages must render (SPEC 1)."""
    out, _ = report_dir(tmp_path)
    german = render(out).read_bytes()
    (out / "report.pdf").unlink()
    english = render(out, "--lang", "en").read_bytes()
    assert german != english


# --------------------------------------------------------------------------
# The report directory stays the documented set of files (SPEC 4)
# --------------------------------------------------------------------------

def test_the_copied_template_is_cleaned_up(rendered):
    """The template is copied next to the model to be compiled (SPEC 6.4) and
    removed again: the report directory is the deliverable, and a stray .typ
    in it is a file somebody has to explain."""
    _, _, out = rendered
    assert not (out / "report.typ").exists()


def test_keep_source_leaves_it_for_debugging(tmp_path):
    out, _ = report_dir(tmp_path)
    render(out, "--keep-source")
    assert (out / "report.typ").is_file()


# --------------------------------------------------------------------------
# The air-gap traps SPEC 8.2 names for this renderer
# --------------------------------------------------------------------------

def test_the_template_imports_no_typst_package():
    """A package import fetches from the registry on first use. Inside the air
    gap that is a failed daily report, and the failure happens months after
    the line was written.

    Comments are stripped first: the template says in words why it must never
    import one, and that sentence has to be allowed to name the thing.
    """
    code = "\n".join(line for line in TEMPLATE.read_text(encoding="utf-8")
                     .splitlines() if not line.strip().startswith("//"))
    assert "@preview" not in code


def test_a_substituted_font_is_an_error(tmp_path):
    """SPEC 8.2: a missing font does not error in Typst, it substitutes and
    exits 0. The renderer has to turn that back into a failure, or the report
    silently looks different inside the air gap than it did outside."""
    out, _ = report_dir(tmp_path)
    template = tmp_path / "bogus.typ"
    template.write_text('#set text(font: "No Such Font At All")\nhello\n',
                        encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(PDF), "--model", str(out / "report_model.json"),
         "--out", str(out), "--template", str(template)],
        capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "font" in (proc.stdout + proc.stderr).lower()
    assert not (out / "report.pdf").exists()


# --------------------------------------------------------------------------
# Usage
# --------------------------------------------------------------------------

def test_a_missing_model_is_a_tool_error(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(PDF), "--model", str(tmp_path / "nope.json"),
         "--out", str(tmp_path)], capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 2
    assert "not found" in proc.stdout + proc.stderr


def test_the_model_must_be_the_name_the_template_reads(tmp_path):
    """The template says `json("report_model.json")`. A model under another
    name would compile against whatever else happened to be in the directory,
    so it is refused by name rather than guessed at."""
    out, _ = report_dir(tmp_path)
    other = out / "something_else.json"
    other.write_bytes((out / "report_model.json").read_bytes())
    proc = subprocess.run(
        [sys.executable, str(PDF), "--model", str(other), "--out", str(out)],
        capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 2
    assert "report_model.json" in proc.stdout + proc.stderr
