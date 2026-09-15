#!/usr/bin/env python3
"""
build_model.py - DuckDB SQL over the jsonl tables -> report_model.json.
SPEC 6.3.

report_model.json is the only thing renderers read. It contains every number,
label and table that will appear in any output, already aggregated. Renderers
lay out; they do not compute (SPEC 11).

Driven by reports/daily.yaml, so adding a newly-collected field to the deck is
a config change. A field named in the spec but absent from the data renders
n/a; it must not crash the run.

Counting rules come from reports/definitions.yaml and are applied in exactly
one place - `query_definition` below. Every table names the definition it was
counted under, and the definitions are emitted into the model so they can be
printed next to the numbers.

Determinism (SPEC 5.2): the model must be byte-identical for the same
input. Every list is sorted, every float has a fixed format, and the only
clock reading in the whole program is `generated_at`, which can be pinned with
--generated-at so two runs can be compared byte for byte.

Exit codes: 0 ok, 1 invalid data, 2 tool/usage error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

try:
    import duckdb
except ImportError:  # pragma: no cover
    sys.exit("duckdb is required (it is in the pipeline image)")

try:
    from ruamel.yaml import YAML
except ImportError:  # pragma: no cover
    sys.exit("ruamel.yaml is required (it is in the pipeline image)")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot import (  # noqa: E402
    TABLES, column_specs, connect, pipeline_version, table_schemas,
)
from validate import Schema, load_schema_dir  # noqa: E402

EXIT_OK, EXIT_INVALID, EXIT_TOOL = 0, 1, 2
UNKNOWN = "unknown"


def load_yaml(path: Path) -> dict:
    return YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}


def pct(part: float, total: float) -> float:
    """One fixed float format everywhere. A drifting one breaks byte-identity."""
    return round(100.0 * part / total, 1) if total else 0.0


# --------------------------------------------------------------------------
# Labels. Looked up once, here, and baked into the model.
# Renderers never touch the taxonomy (SPEC 5.2).
# --------------------------------------------------------------------------

class Labels:
    def __init__(self, schema_dir: Path):
        taxonomy = load_yaml(schema_dir / "taxonomy.yaml")
        self.groups = taxonomy.get("groups") or {}
        self.references: dict[str, dict] = {}
        for candidate in sorted(schema_dir.glob("*.yaml")):
            if candidate.name != "taxonomy.yaml":
                self.references[candidate.stem] = load_yaml(candidate).get("entries") or {}

        # The colour taxonomy.yaml gives `unknown`, if its groups agree on one.
        #
        # A column whose codes come from a reference list (sites, teams) has no
        # taxonomy group and therefore no colours at all, so without this the
        # unknown bar would be grey on one chart and the series colour on the
        # next. The value is not written here: it is read from the groups, so
        # recolouring unknown stays a one-line edit to taxonomy.yaml. Groups
        # that disagree get no fallback rather than an arbitrary winner.
        declared = set()
        for group in self.groups.values():
            entry = (group.get("codes") or {}).get(UNKNOWN)
            if isinstance(entry, dict) and entry.get("colour"):
                declared.add(str(entry["colour"]))
        self.unknown_colour: str | None = \
            declared.pop() if len(declared) == 1 else None

    def order(self, taxonomy: str | None, reference: str | None) -> list[str]:
        if taxonomy and taxonomy in self.groups:
            return [str(c) for c in (self.groups[taxonomy].get("order") or [])]
        if reference and reference in self.references:
            # `unknown` belongs at the end of an axis, not alphabetically in
            # the middle of it.
            codes = sorted(c for c in self.references[reference] if c != UNKNOWN)
            return codes + ([UNKNOWN] if UNKNOWN in self.references[reference] else [])
        return []

    def has_labels(self, taxonomy: str | None, reference: str | None) -> bool:
        """Is there a label source for this column at all?

        `x-ref: projects` points at the project files rather than at a
        reference list, so a dependency target has no label but its own id.
        A column like that gets one column in the Excel, not two.
        """
        if taxonomy and taxonomy in self.groups:
            return True
        return bool(reference and self.references.get(reference))

    def entry(self, code: str, taxonomy: str | None,
              reference: str | None) -> Any:
        if taxonomy and taxonomy in self.groups:
            return (self.groups[taxonomy].get("codes") or {}).get(code)
        if reference and reference in self.references:
            return self.references[reference].get(code)
        return None

    def label(self, code: str, taxonomy: str | None, reference: str | None,
              lang: str) -> str:
        entry = self.entry(code, taxonomy, reference)
        if isinstance(entry, dict):
            value = entry.get(f"label_{lang}")
            if value:
                return str(value)
        # No label yet (SPEC 12.1). Showing the bare code is honest;
        # inventing a label is not. --check-schema warns about every one.
        return code

    def colour(self, code: str, taxonomy: str | None,
               reference: str | None) -> str | None:
        """The colour taxonomy.yaml gives this code, or None.

        Baked into the model for the same reason the labels are: charts.py
        must not open taxonomy.yaml (SPEC 5.2). Most codes have no colour yet
        and the renderer picks from its own palette; `unknown` has one, so the
        unknown bar is the same grey in every chart of every report.
        """
        entry = self.entry(code, taxonomy, reference)
        if isinstance(entry, dict):
            value = entry.get("colour")
            if value:
                return str(value)
        return self.unknown_colour if code == UNKNOWN else None


# --------------------------------------------------------------------------
# Querying. Every count goes through here, once.
# --------------------------------------------------------------------------

class Snapshot:
    def __init__(self, con: duckdb.DuckDBPyConnection, directory: Path,
                 schemas: dict[str, dict[str, str]]):
        self.con = con
        self.directory = directory

        self.columns: dict[str, set[str]] = {}
        for name in TABLES:
            path = (directory / f"{name}.jsonl").resolve()
            view = f"t_{name}"
            # Explicit columns, never inference: an all-null column must not
            # change type between runs, and an empty table must keep its shape.
            spec = ", ".join(f"'{col}': '{typ}'"
                             for col, typ in schemas[name].items())
            con.execute(
                f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM "
                f"read_json('{path}', format='newline_delimited', "
                f"columns={{{spec}}})")
            self.columns[name] = {
                row[0] for row in
                con.execute(f"DESCRIBE {view}").fetchall()}

    def view(self, table: str) -> str:
        return f"t_{table}"

    def has(self, table: str, column: str) -> bool:
        return column in self.columns.get(table, set())


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def q(name: str) -> str:
    """Quote an identifier that came from a config file.

    Without this, a field called `jira-ticket-nr` passes validation and then
    parses as `jira - ticket - nr` in the SELECT. Quoting makes any legal
    field name work; the pattern check catches the ones that would need
    escaping instead of silently mangling them.
    """
    if not IDENTIFIER.match(name):
        if '"' in name or "\\" in name:
            raise ValueError(f"unusable column name: {name!r}")
    return '"' + name + '"'


def count_expression(definition: dict) -> str:
    return ("COUNT(DISTINCT project_id)" if definition.get("count") == "projects"
            else "COUNT(*)")


def where_clause(definition: dict, snapshot: Snapshot, table: str) -> str:
    if definition.get("exclude_placeholders") and snapshot.has(table, "is_placeholder"):
        return " WHERE NOT is_placeholder"
    return ""


def query_definition(snapshot: Snapshot, definition: dict,
                     column: str | None = None,
                     equals: str | None = None) -> Any:
    """The single place a counting rule turns into SQL."""
    table = definition["grain"]
    measure = count_expression(definition)
    clause = where_clause(definition, snapshot, table)

    if column is None:
        return snapshot.con.execute(
            f"SELECT {measure} FROM {snapshot.view(table)}{clause}").fetchone()[0]

    if not snapshot.has(table, column):
        return None
    if equals is not None:
        connector = " AND" if clause else " WHERE"
        return snapshot.con.execute(
            f"SELECT {measure} FROM {snapshot.view(table)}{clause}"
            f"{connector} {q(column)} = ?", [equals]).fetchone()[0]
    return dict(snapshot.con.execute(
        f"SELECT {q(column)}, {measure} FROM {snapshot.view(table)}{clause} "
        f"GROUP BY 1").fetchall())


def distribution(snapshot: Snapshot, spec: dict, definition: dict,
                 labels: Labels) -> dict:
    column = spec["column"]
    table = definition["grain"]
    result: dict[str, Any] = {
        "title_de": spec.get("title_de", column),
        "title_en": spec.get("title_en", column),
        # The shape, named rather than inferred: a renderer that guesses from
        # the column count draws the wrong chart the day a table grows one.
        "kind": "distribution",
        "definition": spec["definition"],
        "grain": table,
        "counts": definition.get("count", "rows"),
    }

    if not snapshot.has(table, column):
        # The moving target: a field named in the spec that nobody has
        # collected yet. n/a, not a crash and not a zero (SPEC 6.3).
        result.update({
            "available": False,
            "columns": [], "rows": [],
            "note_de": f"Feld '{column}' ist in den Daten nicht vorhanden.",
            "note_en": f"Field '{column}' is not present in the data.",
        })
        return result

    counted = query_definition(snapshot, definition, column) or {}
    taxonomy, reference = spec.get("taxonomy"), spec.get("reference")

    ordered = labels.order(taxonomy, reference)
    # Every code from the taxonomy appears, including ones with a count of 0,
    # so the axis is stable day to day. Anything observed but not in the
    # taxonomy is appended rather than dropped - validate.py would have failed
    # it, so reaching here means the reference data changed under us.
    extra = sorted(str(k) for k in counted if str(k) not in ordered)
    codes = ordered + extra
    if UNKNOWN not in codes:
        codes.append(UNKNOWN)

    result.update({
        "available": True,
        "columns": [
            {"key": column, "label_de": spec.get("title_de", column),
             "label_en": spec.get("title_en", column)},
            {"key": "value", "label_de": "Anzahl", "label_en": "Count"},
        ],
        "rows": [
            {
                column: c,
                f"{column}_label_de": labels.label(c, taxonomy, reference, "de"),
                f"{column}_label_en": labels.label(c, taxonomy, reference, "en"),
                f"{column}_colour": labels.colour(c, taxonomy, reference),
                "value": int(counted.get(c, 0)),
            }
            for c in codes
        ],
    })
    return result


def cross_tab(snapshot: Snapshot, spec: dict, definition: dict,
              labels: Labels) -> dict:
    row_col, col_col = spec["rows"], spec["columns"]
    table = definition["grain"]
    result: dict[str, Any] = {
        "title_de": spec.get("title_de", ""),
        "title_en": spec.get("title_en", ""),
        "kind": "cross_tab",
        "definition": spec["definition"],
        "grain": table,
        "counts": definition.get("count", "rows"),
    }
    if not (snapshot.has(table, row_col) and snapshot.has(table, col_col)):
        missing = [c for c in (row_col, col_col) if not snapshot.has(table, c)]
        result.update({
            "available": False, "columns": [], "rows": [],
            "note_de": f"Felder nicht vorhanden: {', '.join(missing)}.",
            "note_en": f"Fields not present in the data: {', '.join(missing)}.",
        })
        return result

    measure = count_expression(definition)
    clause = where_clause(definition, snapshot, table)
    pairs = snapshot.con.execute(
        f"SELECT {q(row_col)}, {q(col_col)}, {measure} "
        f"FROM {snapshot.view(table)}{clause} GROUP BY 1, 2").fetchall()

    counted = {(str(r[0]), str(r[1])): int(r[2]) for r in pairs}
    row_tax, row_ref = spec.get("rows_taxonomy"), spec.get("rows_reference")
    col_tax, col_ref = spec.get("columns_taxonomy"), spec.get("columns_reference")

    def axis(observed: set[str], taxonomy, reference) -> list[str]:
        ordered = labels.order(taxonomy, reference)
        codes = ordered + sorted(o for o in observed if o not in ordered)
        if UNKNOWN not in codes:
            codes.append(UNKNOWN)
        return codes

    row_codes = axis({k[0] for k in counted}, row_tax, row_ref)
    col_codes = axis({k[1] for k in counted}, col_tax, col_ref)

    columns = [{"key": row_col,
                "label_de": spec.get("title_de", row_col),
                "label_en": spec.get("title_en", row_col)}]
    for c in col_codes:
        columns.append({"key": c,
                        "label_de": labels.label(c, col_tax, col_ref, "de"),
                        "label_en": labels.label(c, col_tax, col_ref, "en"),
                        "colour": labels.colour(c, col_tax, col_ref)})
    columns.append({"key": "total", "label_de": "Summe", "label_en": "Total"})

    rows = []
    for r in row_codes:
        row: dict[str, Any] = {
            row_col: r,
            f"{row_col}_label_de": labels.label(r, row_tax, row_ref, "de"),
            f"{row_col}_label_en": labels.label(r, row_tax, row_ref, "en"),
            f"{row_col}_colour": labels.colour(r, row_tax, row_ref),
        }
        total = 0
        for c in col_codes:
            value = counted.get((r, c), 0)
            row[c] = value
            total += value
        row["total"] = total
        rows.append(row)

    result.update({"available": True, "columns": columns, "rows": rows})
    return result


# --------------------------------------------------------------------------
# Coverage. Aggregated from the two columns snapshot.py carried through.
# --------------------------------------------------------------------------

def build_coverage(snapshot: Snapshot, fields_tracked: int) -> dict:
    con, view = snapshot.con, snapshot.view("projects")
    total, filled, tracked, verified = con.execute(
        f"SELECT COUNT(*), COALESCE(SUM(fields_filled), 0), "
        f"COALESCE(SUM(fields_tracked), 0), "
        f"COUNT(*) FILTER (WHERE confidence = 'verified') FROM {view}").fetchone()

    by_team = con.execute(
        f"SELECT team_id, COUNT(*), COALESCE(SUM(fields_filled), 0), "
        f"COALESCE(SUM(fields_tracked), 0), "
        f"COUNT(*) FILTER (WHERE confidence = 'verified') "
        f"FROM {view} GROUP BY 1 ORDER BY 1").fetchall()

    return {
        "projects_total": int(total),
        "fields_tracked": fields_tracked,
        # Raw coverage and verified coverage are two separate numbers
        # everywhere they appear (SPEC 11). An import gives you 90% raw
        # coverage that means almost nothing.
        "field_coverage_pct": pct(filled, tracked),
        "verified_coverage_pct": pct(verified, total),
        "by_team": [
            {"team_id": str(t[0]), "projects": int(t[1]),
             "coverage_pct": pct(t[2], t[3]), "verified_pct": pct(t[4], t[1])}
            for t in by_team
        ],
    }


# --------------------------------------------------------------------------
# flat: what goes on the Excel data sheets
# --------------------------------------------------------------------------

FLAT_ORDER = {
    "projects": "project_id",
    "datastores": "project_id, idx",
    "environments": "project_id, idx",
    "blockers": "project_id, idx",
    "dependencies": "from_project, to_project",
}

# SPEC 5.2 names the dependency columns `from` and `to`. The table cannot:
# `from` is a SQL keyword. Renamed on the way out, metadata included.
FLAT_RENAME = {"dependencies": {"from_project": "from", "to_project": "to"}}
FLAT_DROP = {"dependencies": ("project_id", "idx")}

# The convenience columns computed by the SQL below. Declared here because
# they exist nowhere else - they are not fields of a project, so the schema
# has nothing to say about them (SPEC 6.4).
CONVENIENCE_COLUMNS: list[dict] = [
    {"key": "datastore_engines", "kind": "text"},
    {"key": "has_db2", "kind": "text"},
    {"key": "sites", "kind": "text"},
    {"key": "os_families", "kind": "text"},
    {"key": "open_blockers", "kind": "count"},
]


def flat_column_specs(schema_dir_specs: dict[str, list[dict]],
                      labels: Labels) -> dict[str, list[dict]]:
    """What each flat table's columns are, and which of them carry codes.

    The renderers lay this out and compute nothing (SPEC 11): a column marked
    `coded` has a `<key>_label_de` / `<key>_label_en` on every row, already
    looked up, so the Excel can show the code and the label side by side
    without ever opening taxonomy.yaml.
    """
    # A column the schema says nothing about is one of snapshot.py's own
    # (project_id, idx, is_placeholder). Its kind follows its declared SQL
    # type, so the renderer still knows whether it is a number.
    kind_of_type = {"BIGINT": "count", "BOOLEAN": "flag"}

    columns: dict[str, list[dict]] = {}
    for table in TABLES:
        renames = FLAT_RENAME.get(table, {})
        dropped = FLAT_DROP.get(table, ())
        specs = []
        for spec in schema_dir_specs[table]:
            if spec["key"] in dropped:
                continue
            key = renames.get(spec["key"], spec["key"])
            coded = spec["kind"] == "code" and \
                labels.has_labels(spec["taxonomy"], spec["reference"])
            specs.append({
                "key": key,
                "kind": spec["kind"] or kind_of_type.get(spec["type"], "text"),
                "coded": coded,
                "label_de": key,
                "label_en": key,
            })
        if table == "projects":
            specs.extend({**c, "coded": False, "label_de": c["key"],
                          "label_en": c["key"]} for c in CONVENIENCE_COLUMNS)
        columns[table] = specs
    return columns


def build_flat(snapshot: Snapshot, specs: dict[str, list[dict]],
               labels: Labels, schema_specs: dict[str, list[dict]]
               ) -> dict[str, list[dict]]:
    con = snapshot.con
    flat: dict[str, list[dict]] = {}

    # Convenience columns for the projects sheet (SPEC 6.4). Managers filter
    # on these without thinking about grain; the multi-grain sheets are there
    # for when they need to be correct. Computed here, never in a renderer.
    con.execute(f"""
        CREATE OR REPLACE VIEW _projects_flat AS
        SELECT p.*,
               COALESCE(d.engines, 'unknown')      AS datastore_engines,
               CASE WHEN d.has_db2 THEN 'full' ELSE 'none' END AS has_db2,
               COALESCE(e.sites, 'unknown')        AS sites,
               COALESCE(e.os_families, 'unknown')  AS os_families,
               COALESCE(b.open_blockers, 0)        AS open_blockers
        FROM {snapshot.view('projects')} p
        LEFT JOIN (
            SELECT project_id,
                   string_agg(DISTINCT engine, ', ' ORDER BY engine) AS engines,
                   bool_or(engine = 'db2') AS has_db2
            FROM {snapshot.view('datastores')} GROUP BY 1) d USING (project_id)
        LEFT JOIN (
            SELECT project_id,
                   string_agg(DISTINCT datacenter, ', ' ORDER BY datacenter) AS sites,
                   string_agg(DISTINCT os_family, ', ' ORDER BY os_family) AS os_families
            FROM {snapshot.view('environments')} GROUP BY 1) e USING (project_id)
        LEFT JOIN (
            SELECT project_id, COUNT(*) FILTER (WHERE status = 'open') AS open_blockers
            FROM {snapshot.view('blockers')} GROUP BY 1) b USING (project_id)
    """)

    for table in TABLES:
        view = "_projects_flat" if table == "projects" else snapshot.view(table)
        order = ", ".join(q(c.strip()) for c in FLAT_ORDER[table].split(","))
        cursor = con.execute(f"SELECT * FROM {view} ORDER BY {order}")
        names = [d[0] for d in cursor.description]
        rows = [dict(zip(names, record)) for record in cursor.fetchall()]

        renames = FLAT_RENAME.get(table, {})
        dropped = FLAT_DROP.get(table, ())
        coded = {s["key"] for s in specs[table] if s["coded"]}
        sources = {s["key"]: s for s in schema_specs[table]}

        shaped = []
        for row in rows:
            out: dict[str, Any] = {}
            for name, value in row.items():
                if name in dropped:
                    continue
                key = renames.get(name, name)
                out[key] = value
                if key in coded:
                    source = sources[name]
                    for lang in ("de", "en"):
                        out[f"{key}_label_{lang}"] = labels.label(
                            str(value), source["taxonomy"], source["reference"], lang)
            shaped.append(out)
        flat[table] = shaped
    return flat


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build report_model.json from the jsonl tables.")
    parser.add_argument("--snapshot", default=Path("out/tables"), type=Path,
                        help="the jsonl tables written by snapshot.py")
    parser.add_argument("--spec", default=Path("reports/daily.yaml"), type=Path)
    parser.add_argument("--definitions", default=Path("reports/definitions.yaml"), type=Path)
    parser.add_argument("--schema", default=Path("schema"), type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--as-of", default=None,
                        help="report date (default: the snapshot directory name)")
    parser.add_argument("--generated-at", default=None,
                        help="ISO timestamp. Pin it to compare two runs byte for byte.")
    args = parser.parse_args(argv)

    if not args.snapshot.is_dir():
        print(f"--snapshot: {args.snapshot} is not a directory", file=sys.stderr)
        return EXIT_TOOL
    for required in (args.spec, args.definitions):
        if not required.exists():
            print(f"{required} not found", file=sys.stderr)
            return EXIT_TOOL

    spec = load_yaml(args.spec)
    definitions = (load_yaml(args.definitions) or {}).get("definitions") or {}
    labels = Labels(args.schema)

    manifest_file = args.snapshot / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8")) \
        if manifest_file.exists() else {}

    as_of = args.as_of or manifest.get("as_of_date") or args.snapshot.name
    generated_at = args.generated_at or \
        dt.datetime.now().astimezone().replace(microsecond=0).isoformat()

    try:
        root, _, _, _ = load_schema_dir(args.schema)
    except Exception as exc:
        print(f"--schema: {exc}", file=sys.stderr)
        return EXIT_TOOL
    schema = Schema(root)
    schemas = table_schemas(schema)
    schema_specs = column_specs(schema)
    flat_columns = flat_column_specs(schema_specs, labels)

    con = connect()
    snapshot = Snapshot(con, args.snapshot, schemas)

    def resolve(key: str) -> dict | None:
        definition = definitions.get(key)
        if definition is None:
            print(f"{args.spec}: unknown definition {key!r}. "
                  f"known: {', '.join(sorted(definitions))}", file=sys.stderr)
        return definition

    # ---- headline ----
    headline = []
    for item in spec.get("headline") or []:
        definition = resolve(item["definition"])
        if definition is None:
            return EXIT_TOOL
        value = query_definition(snapshot, definition,
                                 item.get("column"), item.get("equals"))
        headline.append({
            "key": item["key"],
            "label_de": item.get("label_de", item["key"]),
            "label_en": item.get("label_en", item["key"]),
            "definition": item["definition"],
            "value": None if value is None else int(value),
        })

    # ---- tables ----
    tables: dict[str, Any] = {}
    for key in sorted(spec.get("tables") or {}):
        table_spec = (spec["tables"])[key]
        definition = resolve(table_spec["definition"])
        if definition is None:
            return EXIT_TOOL
        kind = table_spec.get("kind", "distribution")
        if kind == "cross_tab":
            tables[key] = cross_tab(snapshot, table_spec, definition, labels)
        elif kind == "distribution":
            tables[key] = distribution(snapshot, table_spec, definition, labels)
        else:
            print(f"{args.spec}: table {key!r} has unknown kind {kind!r} "
                  f"(known: distribution, cross_tab)", file=sys.stderr)
            return EXIT_TOOL

    # ---- charts: declared here, rendered by charts.py in M4 ----
    charts = []
    for key in spec.get("charts") or []:
        if key not in tables:
            print(f"{args.spec}: charts names {key!r}, which is not a table",
                  file=sys.stderr)
            return EXIT_TOOL
        charts.append({
            "key": key, "path": f"{key}.png",
            "width_px": 1600, "height_px": 900,
            "title_de": tables[key]["title_de"], "title_en": tables[key]["title_en"],
        })
    # `charts` names tables; the shape of each is already in tables[key].
    # charts.py reads both and computes nothing (SPEC 11).

    model = {
        "generated_at": generated_at,
        "as_of_date": as_of,
        "provenance": {
            "git_sha": manifest.get("git_sha", "unknown"),
            "git_tag": manifest.get("git_tag"),
            "schema_version": manifest.get("schema_version", 1),
            "pipeline_version": manifest.get("pipeline_version", pipeline_version()),
            "image_digest": manifest.get("image_digest"),
            "duckdb_version": manifest.get("duckdb_version"),
            "project_count": manifest.get("project_count", 0),
            "tables": str(args.snapshot),
        },
        "report": {
            "title_de": (spec.get("report") or {}).get("title_de", ""),
            "title_en": (spec.get("report") or {}).get("title_en", ""),
        },
        "coverage": build_coverage(snapshot, manifest.get("fields_tracked", 0)),
        "headline": headline,
        "tables": tables,
        "charts": charts,
        # What the Excel data sheets are made of: the columns, with the
        # coded ones marked, and the rows with every label already
        # looked up. A renderer lays this out and computes nothing.
        "flat_columns": flat_columns,
        "flat": build_flat(snapshot, flat_columns, labels, schema_specs),
        "definitions": [
            {"key": key,
             "grain": definitions[key].get("grain"),
             "counts": definitions[key].get("count"),
             "text_de": definitions[key].get("text_de", "").strip(),
             "text_en": definitions[key].get("text_en", "").strip()}
            for key in sorted(definitions)
        ],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(model, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"model: {args.out}")
    print(f"  projects {model['coverage']['projects_total']}, "
          f"coverage {model['coverage']['field_coverage_pct']}%, "
          f"verified {model['coverage']['verified_coverage_pct']}%")
    print(f"  tables {len(tables)}, headline {len(headline)}, charts {len(charts)}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
