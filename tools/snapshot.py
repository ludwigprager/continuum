#!/usr/bin/env python3
"""
snapshot.py - the derived analytical layer. SPEC 6.2.

    projects/**/*.yaml  ->  out/tables/*.jsonl  (read directly by DuckDB)

Why jsonl: DuckDB cannot read YAML, and this keeps pandas out of the image.

There is no Parquet, no dated snapshot history and nothing carried over from
the last run. The tables are a pure function of the working tree: delete
out/ and the next run rebuilds it identically. Git is the history.
See SPEC 2.

Five tables, each one row per (project, child) pair. That is what makes
"how many projects have DB2" a COUNT(DISTINCT project_id) rather than a row
count that double-counts a project with two DB2 instances.

    projects      one row per project
    datastores    one row per (project, datastore)
    environments  one row per (project, environment)
    blockers      one row per (project, blocker)
    dependencies  one row per (project, depends_on target)

Two flattening rules carry real weight, and both are stated in
reports/definitions.yaml because they change the numbers:

  * A missing or null coded value becomes the string `unknown`, never NULL and
    never absent. `unknown` is an explicit row in every distribution.
  * A project with no datastores (or no environments) still gets one row, with
    is_placeholder=true. Without it the engine distribution would silently
    have a smaller denominator than the project count.

Exit codes: 0 ok, 1 invalid data, 2 tool/usage error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from ruamel.yaml import YAML
except ImportError:  # pragma: no cover
    sys.exit("ruamel.yaml is required (it is in the pipeline image)")

try:
    import duckdb
except ImportError:  # pragma: no cover
    sys.exit("duckdb is required (it is in the pipeline image)")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate import (  # noqa: E402  - one definition of these, not two
    UNKNOWN, Schema, coverage_for, dig, is_known, load_schema_dir,
    normalise_dates, project_files,
)

EXIT_OK, EXIT_INVALID, EXIT_TOOL = 0, 1, 2

TABLES = ("projects", "datastores", "environments", "blockers", "dependencies")

# Explicit column types, not inference.
#
# Inference would read types from whatever data happens to be present: a
# column that is null in every row today becomes a different type tomorrow,
# the table shape shifts underneath the model, and an empty table loses its
# columns entirely. Declaring the schema makes the tables the same shape
# whether it holds 10 projects or 10,000, and whether or not anyone has filled
# a field in yet.
#
# The `projects` table is NOT listed here. Its columns are derived from the
# x-column annotations in the project schema, so adding a field to the report
# is a schema edit and nothing else. See project_columns().
SCHEMAS: dict[str, dict[str, str]] = {
    "datastores": {
        "project_id": "VARCHAR", "idx": "BIGINT", "engine": "VARCHAR",
        "version": "VARCHAR", "role": "VARCHAR", "is_placeholder": "BOOLEAN",
    },
    "environments": {
        "project_id": "VARCHAR", "idx": "BIGINT", "name": "VARCHAR",
        "datacenter": "VARCHAR", "os_family": "VARCHAR",
        "os_version": "VARCHAR", "os_eol": "VARCHAR", "is_placeholder": "BOOLEAN",
    },
    "blockers": {
        "project_id": "VARCHAR", "idx": "BIGINT", "blocker_id": "VARCHAR",
        "type": "VARCHAR", "severity": "VARCHAR", "status": "VARCHAR",
    },
    "dependencies": {
        "project_id": "VARCHAR", "idx": "BIGINT",
        "from_project": "VARCHAR", "to_project": "VARCHAR",
    },
}

# Which field of the project schema each child-table column was flattened from.
#
# flatten() below already had to name these paths to read the values. Naming
# them once more here, instead of a second time in a renderer, is what lets
# build_model.py ask the schema whether a column is coded and which taxonomy
# group it belongs to. No taxonomy name and no enum value appears in any of
# this - only the path, which the flattening already knew (SPEC 6.1.1).
#
# A column with no entry is a fact about the row rather than a field of the
# project (project_id, idx, is_placeholder) and is never labelled.
SOURCES: dict[str, dict[str, str]] = {
    "datastores": {
        "engine": "datastores[].engine",
        "version": "datastores[].version",
        "role": "datastores[].role",
    },
    "environments": {
        "name": "placement.environments[].name",
        "datacenter": "placement.environments[].datacenter",
        "os_family": "placement.environments[].os.family",
        "os_version": "placement.environments[].os.version",
        "os_eol": "placement.environments[].os.eol",
    },
    "blockers": {
        "blocker_id": "migration.blockers[].id",
        "type": "migration.blockers[].type",
        "severity": "migration.blockers[].severity",
        "status": "migration.blockers[].status",
    },
    "dependencies": {
        "from_project": "id",
        "to_project": "integration.depends_on[]",
    },
}


# Columns of the projects table that are not fields of a project: they are
# facts about the file or about the validation run.
COMPUTED_COLUMNS: dict[str, str] = {
    "project_id": "VARCHAR",
    "file": "VARCHAR",
    "fields_filled": "BIGINT",
    "fields_tracked": "BIGINT",
}

# x-kind -> (column type, how to read the value)
KIND_COLUMN = {
    "count": "BIGINT",
    "text": "VARCHAR",
    "code": "VARCHAR",
    "version": "VARCHAR",
    "date": "VARCHAR",
    "id": "VARCHAR",
}


def project_columns(schema: "Schema") -> list[tuple[str, str, str, str]]:
    """Every column of the projects table, derived from the schema.

    Returns (column_name, column_type, dotted_path, kind) for each field
    annotated `x-column`. One annotation is the whole job: the column, its
    type and how the value is read all follow from it, so a field can never
    be half-added - declared in one place and forgotten in another.
    """
    columns: list[tuple[str, str, str, str]] = []
    for pattern, annots in schema.walk_schema():
        marker = annots.get("x-column")
        if not marker:
            continue
        if "[]" in pattern:
            # A repeated field belongs on its own table at its own grain, not
            # squashed into a column of the projects table.
            raise ValueError(
                f"x-column on {pattern!r}: repeated fields cannot be project "
                f"columns. Put it on the datastores/environments table instead.")
        name = marker if isinstance(marker, str) else pattern.split(".")[-1]
        kind = annots.get("x-kind", "text")
        columns.append((name, KIND_COLUMN.get(kind, "VARCHAR"), pattern, kind))
    columns.sort()
    return columns


def table_schemas(schema: "Schema") -> dict[str, dict[str, str]]:
    """The full column set of every table, projects included.

    snapshot.py writes the tables and build_model.py reads them; both need the
    same answer, so both ask here rather than keeping their own copy.
    """
    schemas = {name: dict(cols) for name, cols in SCHEMAS.items()}
    projects = dict(COMPUTED_COLUMNS)
    projects.update({name: typ for name, typ, _, _ in project_columns(schema)})
    schemas["projects"] = projects
    return schemas


def column_specs(schema: "Schema") -> dict[str, list[dict]]:
    """Every column of every table, with the schema annotations behind it.

    The tables carry codes; the report has to show labels. Which taxonomy
    group a column's codes come from is a property of the schema, so it is
    read from the schema here and handed to build_model.py, which bakes the
    labels into the model once. Nothing downstream ever sees a taxonomy name
    (SPEC 5.2, 6.1.1).
    """
    annotated = {pattern: annots for pattern, annots in schema.walk_schema()}
    project_paths = {name: pattern for name, _, pattern, _ in project_columns(schema)}

    specs: dict[str, list[dict]] = {}
    for table, columns in table_schemas(schema).items():
        sources = dict(SOURCES.get(table) or {})
        if table == "projects":
            sources.update(project_paths)
        specs[table] = []
        for name, typ in columns.items():
            annots = annotated.get(sources.get(name, ""), {})
            specs[table].append({
                "key": name,
                "type": typ,
                "kind": annots.get("x-kind"),
                "taxonomy": annots.get("x-taxonomy"),
                "reference": annots.get("x-ref"),
                "source": sources.get(name),
            })
    return specs


def project_row(doc: dict, columns: list[tuple[str, str, str, str]]) -> dict:
    row: dict[str, Any] = {}
    for name, _, path, kind in columns:
        value = dig(doc, path)
        if isinstance(value, list):
            # A list of codes flattens to a sorted, comma-joined string so it
            # can be filtered in Excel. The list itself lives on its own table
            # when the grain matters.
            row[name] = ", ".join(sorted(code(v) for v in value)) or UNKNOWN
        elif kind == "count":
            row[name] = number(value)
        elif kind == "code":
            row[name] = code(value)
        else:
            row[name] = text(value)
    return row


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def pipeline_version() -> str:
    version_file = repo_root() / "VERSION"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip()
    return "unknown"


# --------------------------------------------------------------------------
# Git provenance, without the git binary
# --------------------------------------------------------------------------
# The pipeline image has no git and the repo is mounted read-only, so this
# reads .git directly. The wrapper script passes --git-sha when it can; this
# is the fallback so running the tool straight inside the container still
# produces real provenance rather than "unknown".

def read_git(root: Path) -> tuple[str, str | None]:
    git_dir = root / ".git"
    if git_dir.is_file():  # worktree: ".git" is a file pointing elsewhere
        try:
            git_dir = Path(git_dir.read_text(encoding="utf-8").split(":", 1)[1].strip())
        except (OSError, IndexError):
            return ("unknown", None)
    if not git_dir.is_dir():
        return ("unknown", None)

    def resolve(ref: str) -> str | None:
        direct = git_dir / ref
        if direct.exists():
            return direct.read_text(encoding="utf-8").strip() or None
        packed = git_dir / "packed-refs"
        if packed.exists():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.startswith("#") or "^" in line:
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[1].strip() == ref:
                    return parts[0].strip()
        return None

    sha = "unknown"
    head_file = git_dir / "HEAD"
    if head_file.exists():
        head = head_file.read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            sha = resolve(head.split(":", 1)[1].strip()) or "unknown"
        elif head:
            sha = head

    tag = None
    if sha != "unknown":
        tags_dir = git_dir / "refs" / "tags"
        if tags_dir.is_dir():
            for candidate in sorted(tags_dir.rglob("*")):
                if candidate.is_file() and \
                        candidate.read_text(encoding="utf-8").strip() == sha:
                    tag = str(candidate.relative_to(tags_dir))
                    break
    return (sha, tag)


# --------------------------------------------------------------------------
# Flattening
# --------------------------------------------------------------------------

def code(value: Any) -> str:
    """A coded value, or the string `unknown`. Never NULL, never absent."""
    return str(value).strip() if is_known(value) else UNKNOWN


def number(value: Any) -> int | None:
    """A number, or NULL. `unknown` is not 0 and must not become 0."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def text(value: Any) -> str | None:
    return str(value) if is_known(value) else None


def flatten(doc: dict, source_file: str, coverage: tuple[int, int],
            columns: list[tuple[str, str, str, str]]) -> dict[str, list[dict]]:
    pid = str(doc.get("id") or "")
    rows: dict[str, list[dict]] = {name: [] for name in TABLES}

    rows["projects"].append({
        "project_id": pid,
        "file": source_file,
        # Coverage is computed against the schema rather than the flattened
        # tables, and carried as two columns so every aggregate of it is SQL.
        "fields_filled": coverage[0],
        "fields_tracked": coverage[1],
        **project_row(doc, columns),
    })

    datastores = dig(doc, "datastores") or []
    if not isinstance(datastores, list) or not datastores:
        rows["datastores"].append({
            "project_id": pid, "idx": 0, "engine": UNKNOWN,
            "version": None, "role": UNKNOWN, "is_placeholder": True})
    else:
        for i, entry in enumerate(datastores):
            entry = entry if isinstance(entry, dict) else {}
            rows["datastores"].append({
                "project_id": pid, "idx": i,
                "engine": code(entry.get("engine")),
                "version": text(entry.get("version")),
                "role": code(entry.get("role")),
                "is_placeholder": False})

    # SPEC 12.4 is open: placement may be a single `datacenter` scalar
    # (v1 imported data) or an `environments` list (target schema). Both
    # normalise to environment rows here so every downstream count is written
    # once. A flat scalar becomes one environment with no name, because the
    # legacy sheet has nothing to derive a name from.
    environments = dig(doc, "placement.environments")
    flat_site = dig(doc, "placement.datacenter")
    if isinstance(environments, list) and environments:
        for i, entry in enumerate(environments):
            entry = entry if isinstance(entry, dict) else {}
            rows["environments"].append({
                "project_id": pid, "idx": i,
                "name": text(entry.get("name")),
                "datacenter": code(entry.get("datacenter")),
                "os_family": code(dig(entry, "os.family")),
                "os_version": text(dig(entry, "os.version")),
                "os_eol": text(dig(entry, "os.eol")),
                "is_placeholder": False})
    elif is_known(flat_site):
        rows["environments"].append({
            "project_id": pid, "idx": 0, "name": None,
            "datacenter": code(flat_site),
            "os_family": UNKNOWN, "os_version": None, "os_eol": None,
            "is_placeholder": False})
    else:
        rows["environments"].append({
            "project_id": pid, "idx": 0, "name": None,
            "datacenter": UNKNOWN, "os_family": UNKNOWN,
            "os_version": None, "os_eol": None, "is_placeholder": True})

    for i, entry in enumerate(dig(doc, "migration.blockers") or []):
        entry = entry if isinstance(entry, dict) else {}
        rows["blockers"].append({
            "project_id": pid, "idx": i,
            "blocker_id": text(entry.get("id")),
            "type": code(entry.get("type")),
            "severity": code(entry.get("severity")),
            "status": code(entry.get("status"))})

    for i, target in enumerate(dig(doc, "integration.depends_on") or []):
        if is_known(target):
            rows["dependencies"].append({
                "project_id": pid, "idx": i,
                "from_project": pid, "to_project": str(target)})

    return rows


SORT_KEYS = {
    "projects": ("project_id",),
    "datastores": ("project_id", "idx"),
    "environments": ("project_id", "idx"),
    "blockers": ("project_id", "idx"),
    "dependencies": ("project_id", "idx"),
}


def write_jsonl(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def connect() -> duckdb.DuckDBPyConnection:
    """DuckDB with every path to the network closed (SPEC 8.2).

    `json` is statically linked into the Python wheel. The optional extensions
    (httpfs, excel, spatial) download on first use, so autoinstall and autoload
    are switched off rather than merely avoided.
    """
    con = duckdb.connect()
    con.execute("SET autoinstall_known_extensions=false")
    con.execute("SET autoload_known_extensions=false")
    # Note: NOT enable_external_access=false. That switch disables all file
    # system access, including reading the local jsonl.
    # Blocking extension download is what is actually needed here.
    return con


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flatten projects/ into the jsonl tables the report reads.")
    parser.add_argument("--projects", default="projects", type=Path)
    parser.add_argument("--schema", default="schema", type=Path)
    parser.add_argument("--out", default=Path("out/tables"), type=Path,
                        help="where the jsonl tables go (default: out/tables)")
    parser.add_argument("--as-of", default=None,
                        help="the date these tables describe, recorded in "
                             "manifest.json as provenance")
    parser.add_argument("--schema-version", type=int, default=1)
    parser.add_argument("--git-sha", default=None)
    parser.add_argument("--git-tag", default=None)
    parser.add_argument("--image-digest", default=None)
    args = parser.parse_args(argv)

    if not args.projects.is_dir():
        print(f"--projects: {args.projects} is not a directory", file=sys.stderr)
        return EXIT_TOOL

    files = project_files(args.projects)
    if not files:
        print(f"--projects: no project files under {args.projects}", file=sys.stderr)
        return EXIT_TOOL

    try:
        root, _, _, _ = load_schema_dir(args.schema)
    except Exception as exc:
        print(f"--schema: {exc}", file=sys.stderr)
        return EXIT_TOOL
    schema = Schema(root)
    tracked = {pattern for pattern, annots in schema.walk_schema()
               if annots.get("x-tracked")}
    try:
        columns = project_columns(schema)
    except ValueError as exc:
        print(f"--schema: {exc}", file=sys.stderr)
        return EXIT_TOOL
    SCHEMAS.update(table_schemas(schema))

    loader = YAML(typ="safe")
    tables: dict[str, list[dict]] = {name: [] for name in TABLES}
    for path in sorted(files):
        try:
            doc = loader.load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"{path}: cannot parse: {exc}", file=sys.stderr)
            print("run ./check.sh first - snapshot assumes the data is valid",
                  file=sys.stderr)
            return EXIT_INVALID
        if not isinstance(doc, dict) or not doc.get("id"):
            print(f"{path}: not a project file (no id)", file=sys.stderr)
            return EXIT_INVALID
        doc = normalise_dates(doc)
        rel = str(path.relative_to(args.projects.parent)) \
            if args.projects.parent in path.parents else str(path)
        for name, rows in flatten(doc, rel, coverage_for(doc, schema, tracked),
                                  columns).items():
            tables[name].extend(rows)

    for name in TABLES:
        tables[name].sort(key=lambda r, n=name: tuple(str(r.get(k)) for k in SORT_KEYS[n]))

    args.out.mkdir(parents=True, exist_ok=True)
    for name in TABLES:
        write_jsonl(tables[name], args.out / f"{name}.jsonl")

    git_sha, git_tag = read_git(repo_root())
    manifest = {
        "as_of_date": args.as_of,
        "schema_version": args.schema_version,
        "pipeline_version": pipeline_version(),
        "git_sha": args.git_sha or git_sha,
        "git_tag": args.git_tag or git_tag,
        "image_digest": args.image_digest or os.environ.get("IMAGE_DIGEST"),
        "project_count": len(tables["projects"]),
        "fields_tracked": len(tracked),
        "file_count": len(files),
        "row_counts": {name: len(tables[name]) for name in TABLES},
        "duckdb_version": duckdb.__version__,
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8")

    print(f"tables: {args.out}")
    for name in TABLES:
        print(f"  {name:<14} {len(tables[name]):>5} rows")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
