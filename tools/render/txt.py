#!/usr/bin/env python3
"""
txt.py - the plain-text report. SPEC 6.4, milestone M4.

    tools/render/txt.py --model report_model.json --out DIR [--lang de|en]

Cheap, and it diffs cleanly day over day, which turns out to be the fastest
way to answer "what changed since yesterday" - `diff` on two of these is the
day-over-day comparison the pipeline itself deliberately does not keep
(SPEC 2, 12.5).

That is the whole reason for the fixed column widths. A layout that sizes its
columns to the data reflows every row the day one label gets longer, and the
diff is then noise. The widths live here; the data never moves them.

Like every renderer it lays out and computes nothing (SPEC 11): every number,
label and definition is read from report_model.json as it stands. It contains
no field name, no code and no enum value (SPEC 6.1.1) - the tables are
whatever the model happens to hold today.

Exit codes: 0 ok, 2 tool/usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path
from typing import Any, Sequence

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError:  # pragma: no cover
    sys.exit("jinja2 is required (it is in the pipeline image)")

# A renderer has no verdict on the data, so it never exits 1.
EXIT_OK, EXIT_TOOL = 0, 2

DEFAULT_TEMPLATE = Path("templates/report.txt.j2")
OUTPUT_NAME = "report.txt"

WIDTH = 100  # the page. Wide enough for the cross-tab, narrow enough to paste.

# Chrome, not taxonomy: the words around the numbers. The codes and their
# labels come from the model, and the ones that have no label yet stay bare
# codes rather than invented words (SPEC 12.1).
CHROME = {
    "de": {
        "as_of": "Stand", "generated": "erzeugt", "git_sha": "Git-Commit",
        "schema_version": "Schema-Version", "pipeline": "Pipeline-Version",
        "image": "Image", "projects": "Projekte", "unknown": "unbekannt",
        "headline": "Kennzahlen", "coverage": "Abdeckung",
        "projects_total": "Projekte gesamt", "fields_tracked": "Felder erfasst",
        "field_coverage": "Feldabdeckung", "verified_coverage": "davon verifiziert",
        "team": "Team", "verified": "verifiziert", "tables": "Tabellen",
        "code": "Code", "label": "Bezeichnung", "count": "Anzahl",
        "charts": "Diagramme", "definitions": "Zählregeln", "none": "keine",
    },
    "en": {
        "as_of": "as of", "generated": "generated", "git_sha": "git commit",
        "schema_version": "schema version", "pipeline": "pipeline version",
        "image": "image", "projects": "projects", "unknown": "unknown",
        "headline": "headline", "coverage": "coverage",
        "projects_total": "projects total", "fields_tracked": "fields tracked",
        "field_coverage": "field coverage", "verified_coverage": "of which verified",
        "team": "team", "verified": "verified", "tables": "tables",
        "code": "code", "label": "label", "count": "count",
        "charts": "charts", "definitions": "counting rules", "none": "none",
    },
}


def truncate_cell(value: Any, width: int) -> str:
    """Fit a value into a fixed column, cutting rather than reflowing.

    A cut cell is a local change in the diff; a reflowed row is not. The cut
    is marked so a reader knows the value continues - the full value is on the
    Excel, which is where slicing happens anyway.
    """
    text = "" if value is None else str(value)
    if len(text) <= width:
        return text
    return text[:max(1, width - 1)] + "…"


def col(value: Any, width: int) -> str:
    """Left-aligned fixed-width cell, with one space of gutter."""
    return truncate_cell(value, width - 1).ljust(width)


def num(value: Any, width: int) -> str:
    """Right-aligned fixed-width cell. `None` is n/a, never 0 (SPEC 5.1)."""
    text = "n/a" if value is None else str(value)
    return truncate_cell(text, width - 1).rjust(width - 1) + " "


def wrap(text: Any, width: int) -> list[str]:
    return textwrap.wrap(str(text or ""), width) or [""]


def environment(template_dir: Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,   # a typo in the template is an error, not
        trim_blocks=True,            # a silently missing line
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,            # plain text: escaping would corrupt it
    )
    env.filters["truncate_cell"] = truncate_cell
    env.filters["wrap"] = wrap
    return env


def render(model: dict, out_dir: Path, lang: str, template: Path) -> Path:
    env = environment(template.parent)
    chrome = CHROME[lang]

    text = env.get_template(template.name).render(
        model=model,
        p=model.get("provenance") or {},
        cov=model.get("coverage") or {},
        # `tables` is a mapping in the model; the template needs a stable
        # order, and sorted by key is the one the Excel uses too.
        tables=sorted((model.get("tables") or {}).items()),
        report_title=(model.get("report") or {}).get(f"title_{lang}")
        or (model.get("report") or {}).get("title_de", ""),
        lang=lang,
        c=chrome,
        w=WIDTH,
        label_field=f"label_{lang}",
        title_field=f"title_{lang}",
        note_field=f"note_{lang}",
        text_field=f"text_{lang}",
        rule=lambda character: character * WIDTH,
        cell=truncate_cell,
        col=col,
        num=num,
    )

    # Trailing whitespace is invisible here and loud in a diff, and a fixed
    # width layout produces a lot of it. Stripped once, at the end, so the
    # template can stay readable.
    text = "\n".join(line.rstrip() for line in text.splitlines()) + "\n"

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / OUTPUT_NAME
    out_path.write_text(text, encoding="utf-8")
    return out_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render report_model.json as plain text.")
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--lang", default="de", choices=("de", "en"))
    parser.add_argument("--template", default=DEFAULT_TEMPLATE, type=Path)
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
        out_path = render(model, args.out, args.lang, args.template)
    except Exception as exc:  # pragma: no cover - surfaced as a tool error
        print(f"txt: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_TOOL

    lines = out_path.read_text(encoding="utf-8").count("\n")
    print(f"txt: {out_path}")
    print(f"  {lines} lines, {WIDTH} columns")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
