#!/usr/bin/env python3
"""
pdf.py - the paginated report. SPEC 6.4, milestone M5.

    tools/render/pdf.py --model report_model.json --out DIR [--lang de|en]

Typst, not LaTeX (image size and speed) and not WeasyPrint (the output is a
paginated management report, not a web page). SPEC 6.4 names WeasyPrint as the
fallback if Typst turns out to be a problem; it did not, so this is Typst and
switching would be a decision to announce, not a detail to change quietly.

How it works, and why it is this shape: SPEC 6.4 says write report_model.json
next to the template and read it with `#let data = json("report_model.json")`.
The model already sits in its own report directory next to the chart PNGs it
names, so the template is copied *there* and compiled with that directory as
the Typst root. Nothing outside it is readable to the compiler, which is a
useful thing to be able to say about a tool that renders data from several
hundred teams.

This renderer computes nothing (SPEC 11) and names no field, code or enum
value (SPEC 6.1.1) - all of that is in templates/report.typ, which in turn only
reads what build_model.py put in the model.

Two traps, both from SPEC 8.2, both closed here rather than hoped about:

  * **Fonts.** Typst warns about an unknown family and substitutes silently,
    exiting 0. The report then looks different inside the air gap than it did
    outside and nothing says so. Any such warning is turned into a failure
    here, so it does not need a list of font names in this file to catch it -
    whatever the template asks for is what gets checked.
  * **Packages.** `#import "@preview/..."` makes the compiler reach for the
    package registry. templates/report.typ imports nothing, TYPST_PACKAGE_PATH
    points into the image, and tests/test_pdf.py asserts the template stays
    that way.

Determinism (SPEC 5.2): `typst compile` stamps the PDF with the current time
unless it is told otherwise, so the creation timestamp is always pinned to the
model's own `generated_at`. Same model in, same bytes out.

Exit codes: 0 ok, 2 tool/usage error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

# A renderer has no verdict on the data, so it never exits 1.
EXIT_OK, EXIT_TOOL = 0, 2

DEFAULT_TEMPLATE = Path("templates/report.typ")
OUTPUT_NAME = "report.pdf"
MODEL_NAME = "report_model.json"   # what the template reads, by that name
SOURCE_NAME = "report.typ"         # the copy that sits next to it

TYPST = os.environ.get("TYPST_BIN", "typst")

# Typst says this and carries on (exit 0). It must not be a warning here.
FONT_WARNING = "unknown font family"


def epoch(generated_at: Any) -> int:
    """The model's `generated_at` as a UNIX timestamp, for --creation-timestamp.

    Typst reads the clock for the PDF's creation date, which would make two
    runs of the same model differ. The model's own timestamp is the honest
    answer and is already pinned wherever reproducibility is being tested. A
    model without a parseable one falls back to the zip epoch the workbook
    uses, so the two artifacts at least agree on what "no timestamp" means.
    """
    try:
        parsed = dt.datetime.fromisoformat(str(generated_at))
    except (TypeError, ValueError):
        return int(dt.datetime(1980, 1, 1, tzinfo=dt.timezone.utc).timestamp())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp())


def typst_version() -> str:
    """The compiler that produced this PDF, for the run log.

    Pinned in docker/Dockerfile.pipeline: Typst shifts layout between releases
    and that would move the bytes of a PDF nobody changed (SPEC 9).
    """
    try:
        proc = subprocess.run([TYPST, "--version"], capture_output=True,
                              text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            f"{TYPST} is not runnable: {exc}. It is installed in the pipeline "
            f"image (docker/Dockerfile.pipeline); run this through "
            f"./report.sh or ./shell.sh rather than on the host.") from exc
    return proc.stdout.strip()


def compile_pdf(source: Path, root: Path, out_path: Path, lang: str,
                created: int) -> list[str]:
    """Run `typst compile`, and refuse to accept a silently substituted font."""
    command = [
        TYPST, "compile",
        "--root", str(root),
        "--input", f"lang={lang}",
        # SPEC 5.2. Without this the PDF carries the wall clock and two runs of
        # the same model differ; tests/test_pdf.py checks that they do not.
        "--creation-timestamp", str(created),
        str(source), str(out_path),
    ]
    proc = subprocess.run(command, capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(f"typst compile failed:\n{output.strip()}")

    if FONT_WARNING in output:
        # Typst has already written the PDF by this point - it considers the
        # substitution a warning. Remove it: a report directory carrying a
        # file this run refused to stand behind is worse than an empty one.
        out_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"typst substituted a font rather than failing (SPEC 8.2), so the "
            f"PDF would not look the same inside the air gap:\n"
            f"{output.strip()}\n"
            f"Install the family in docker/Dockerfile.pipeline (SPEC 8.1) or "
            f"change the font stack in the template.")

    return [line for line in output.splitlines() if line.strip()]


def render(model: dict, model_path: Path, out_dir: Path, lang: str,
           template: Path, keep_source: bool = False) -> tuple[Path, list[str]]:
    """Copy the template next to the model, compile, and clean up after.

    The copy is what SPEC 6.4 asks for: `json("report_model.json")` resolves
    relative to the source file, so the two have to be in the same directory,
    and that directory is the one that already holds the PNGs the template
    embeds. It is removed again afterwards - the report directory is the
    deliverable and its contents are documented in SPEC 4; a stray .typ in it
    is a file somebody has to explain. `--keep-source` leaves it for debugging.
    """
    root = model_path.parent.resolve()
    if model_path.name != MODEL_NAME:
        raise RuntimeError(
            f"the template reads {MODEL_NAME!r} by that name, but the model is "
            f"{model_path.name!r}. Rename it or point --model at the one the "
            f"pipeline wrote.")

    source = root / SOURCE_NAME
    # A .typ that was already there is left where it was found: either somebody
    # put it there, or a previous --keep-source run did. This run only removes
    # what this run created.
    existing = source.exists()
    shutil.copyfile(template, source)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = (out_dir / OUTPUT_NAME).resolve()
    try:
        messages = compile_pdf(source, root, out_path, lang,
                               epoch(model.get("generated_at")))
    finally:
        if not keep_source and not existing:
            source.unlink(missing_ok=True)

    missing = [chart.get("path") for chart in model.get("charts") or []
               if not (root / str(chart.get("path", ""))).is_file()]
    if missing:
        # charts.py runs first in ./report.sh (SPEC 6.4). If it has not, the
        # compile above already failed on the missing file - this only makes
        # the reason legible.
        messages.append(f"charts not rendered next to the model: "
                        f"{', '.join(str(p) for p in missing)}")
    return out_path, messages


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render report_model.json to PDF with Typst.")
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--lang", default="de", choices=("de", "en"))
    parser.add_argument("--template", default=DEFAULT_TEMPLATE, type=Path,
                        help="Typst template (default: templates/report.typ)")
    parser.add_argument("--keep-source", action="store_true",
                        help="leave the copied .typ next to the model")
    args = parser.parse_args(argv)

    if not args.model.is_file():
        print(f"--model: {args.model} not found", file=sys.stderr)
        return EXIT_TOOL
    if not args.template.is_file():
        print(f"--template: {args.template} not found", file=sys.stderr)
        return EXIT_TOOL
    try:
        model = json.loads(args.model.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"--model: {args.model} is not valid JSON: {exc}", file=sys.stderr)
        return EXIT_TOOL

    try:
        version = typst_version()
        out_path, messages = render(model, args.model, args.out, args.lang,
                                    args.template, args.keep_source)
    except Exception as exc:  # pragma: no cover - surfaced as a tool error
        print(f"pdf: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_TOOL

    for message in messages:
        print(f"warning: {message}", file=sys.stderr)
    print(f"pdf: {out_path}")
    print(f"  {out_path.stat().st_size} bytes, {version}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
