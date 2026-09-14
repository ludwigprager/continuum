"""Tests for snapshot.py and build_model.py (M2).

The load-bearing one is test_model_is_byte_identical_across_runs: SPEC 5.2
requires the model to be reproducible, and every other design choice in
build_model.py (sorted lists, fixed float format, no clock reading outside
generated_at) exists to make it true.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO / "tools" / "snapshot.py"
BUILD_MODEL = REPO / "tools" / "build_model.py"
SCHEMA = REPO / "schema"
SPEC = REPO / "reports" / "daily.yaml"
DEFINITIONS = REPO / "reports" / "definitions.yaml"
FIXTURES = REPO / "tests" / "fixtures" / "projects"

PINNED = "2026-09-14T04:00:00+02:00"


def run(script: Path, *args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def make_snapshot(tmp_path: Path, projects: Path = FIXTURES,
                  name: str = "2026-09-14") -> Path:
    out = tmp_path / "tables"
    run(SNAPSHOT, "--projects", str(projects), "--schema", str(SCHEMA),
        "--out", str(out), "--as-of", name)
    return out


def make_model(tmp_path: Path, snapshot: Path, name: str = "model.json",
               *extra: str) -> tuple[Path, dict]:
    out = tmp_path / name
    run(BUILD_MODEL, "--snapshot", str(snapshot), "--spec", str(SPEC),
        "--definitions", str(DEFINITIONS), "--schema", str(SCHEMA),
        "--out", str(out), "--generated-at", PINNED, *extra)
    return out, json.loads(out.read_text())


def duck():
    import duckdb
    con = duckdb.connect()
    con.execute("SET autoinstall_known_extensions=false")
    con.execute("SET autoload_known_extensions=false")
    return con


# --------------------------------------------------------------------------
# M2 acceptance
# --------------------------------------------------------------------------

def test_model_is_byte_identical_across_runs(tmp_path):
    """SPEC 10, M2: running it twice on identical input produces
    byte-identical output. Asserted here, as the milestone requires."""
    # The same paths both times, as in production: the model records which
    # snapshot produced it, so building from two different directories would
    # differ for an uninteresting reason.
    snapshot = make_snapshot(tmp_path)
    first, _ = make_model(tmp_path, snapshot, "first.json")
    make_snapshot(tmp_path)
    second, _ = make_model(tmp_path, snapshot, "second.json")
    assert first.read_bytes() == second.read_bytes()


def test_tables_are_byte_identical_across_runs(tmp_path):
    """If the tables drift, the day-over-day delta is comparing noise."""
    first = make_snapshot(tmp_path / "a")
    second = make_snapshot(tmp_path / "b")
    for table in ("projects", "datastores", "environments", "blockers", "dependencies"):
        assert (first / f"{table}.jsonl").read_bytes() == \
               (second / f"{table}.jsonl").read_bytes(), table


def test_only_generated_at_moves_between_unpinned_runs(tmp_path):
    """The clock must not leak into the model anywhere else."""
    snapshot = make_snapshot(tmp_path)
    first = tmp_path / "unpinned-1.json"
    second = tmp_path / "unpinned-2.json"
    for out in (first, second):
        run(BUILD_MODEL, "--snapshot", str(snapshot), "--spec", str(SPEC),
            "--definitions", str(DEFINITIONS), "--schema", str(SCHEMA),
            "--out", str(out))
    a, b = json.loads(first.read_text()), json.loads(second.read_text())
    a.pop("generated_at")
    b.pop("generated_at")
    assert a == b


# --------------------------------------------------------------------------
# Grain: the thing that makes two slides disagree (SPEC 7)
# --------------------------------------------------------------------------

def test_a_project_with_two_engines_counts_once_per_engine_not_once_overall(tmp_path):
    snapshot = make_snapshot(tmp_path)
    con = duck()
    rows = con.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT project_id) "
        f"FROM read_json_auto('{snapshot / 'datastores.jsonl'}') "
        f"WHERE project_id = 'payment-gateway'").fetchone()
    assert rows == (2, 1), "payment-gateway has db2 and redis"

    _, model = make_model(tmp_path, snapshot)
    by_engine = {r["engine"]: r["value"] for r in model["tables"]["by_engine"]["rows"]}
    assert by_engine["db2"] >= 1 and by_engine["redis"] >= 1
    # Counted as projects, so the project appears in both rows and the column
    # sums to more than the project total. That is the documented behaviour.
    assert sum(by_engine.values()) > model["coverage"]["projects_total"]
    assert model["tables"]["by_engine"]["counts"] == "projects"


def test_status_distribution_sums_to_the_project_count(tmp_path):
    """One row per project, never double counted."""
    snapshot = make_snapshot(tmp_path)
    _, model = make_model(tmp_path, snapshot)
    rows = model["tables"]["by_status"]["rows"]
    assert sum(r["value"] for r in rows) == model["coverage"]["projects_total"]


def test_cross_tab_totals_are_environments_not_projects(tmp_path):
    snapshot = make_snapshot(tmp_path)
    _, model = make_model(tmp_path, snapshot)
    table = model["tables"]["site_x_os"]
    assert table["counts"] == "rows"
    grand = sum(r["total"] for r in table["rows"])
    con = duck()
    environments = con.execute(
        f"SELECT COUNT(*) FROM read_json_auto('{snapshot / 'environments.jsonl'}')"
    ).fetchone()[0]
    assert grand == environments


# --------------------------------------------------------------------------
# unknown is never silently dropped (SPEC 11)
# --------------------------------------------------------------------------

def test_every_distribution_has_an_unknown_row(tmp_path):
    snapshot = make_snapshot(tmp_path)
    _, model = make_model(tmp_path, snapshot)
    for key, table in model["tables"].items():
        if not table.get("available") or table.get("grain") is None:
            continue
        if "rows" not in table or not table["rows"]:
            continue
        column = table["columns"][0]["key"]
        codes = {r[column] for r in table["rows"]}
        assert "unknown" in codes, f"{key} has no unknown row"


def test_a_project_with_no_datastores_still_appears_under_unknown(tmp_path):
    """Otherwise the engine distribution has a smaller denominator than the
    project count, and two slides disagree."""
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "bare.yaml").write_text(
        "schema_version: 1\nid: bare\nmy_mandatory_field: TODO\n")
    snapshot = make_snapshot(tmp_path, projects)
    con = duck()
    rows = con.execute(
        f"SELECT engine, is_placeholder FROM "
        f"read_json_auto('{snapshot / 'datastores.jsonl'}')").fetchall()
    assert rows == [("unknown", True)]

    _, model = make_model(tmp_path, snapshot)
    by_engine = {r["engine"]: r["value"] for r in model["tables"]["by_engine"]["rows"]}
    assert by_engine["unknown"] == 1


def test_unknown_is_not_zero(tmp_path):
    """A missing number stays NULL. It must never arrive as 0, which is a
    real answer and would be counted as one."""
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "bare.yaml").write_text(
        "schema_version: 1\nid: bare\nmy_mandatory_field: TODO\n")
    snapshot = make_snapshot(tmp_path, projects)
    con = duck()
    cores, storage = con.execute(
        f"SELECT cpu_cores, storage_gb FROM "
        f"read_json_auto('{snapshot / 'projects.jsonl'}')").fetchone()
    assert cores is None and storage is None


# --------------------------------------------------------------------------
# The moving target (SPEC 1, 6.3)
# --------------------------------------------------------------------------

def test_a_field_named_in_the_spec_but_absent_from_the_data_renders_na(tmp_path):
    snapshot = make_snapshot(tmp_path)
    _, model = make_model(tmp_path, snapshot)
    table = model["tables"]["by_backup_class"]
    assert table["available"] is False
    assert table["rows"] == []
    assert "backup_class" in table["note_en"]


def test_both_placement_shapes_become_environment_rows(tmp_path):
    """SPEC 12.4 is open; snapshot.py normalises so nothing downstream
    has to know which shape a file used."""
    snapshot = make_snapshot(tmp_path)
    con = duck()
    flat = con.execute(
        f"SELECT datacenter, name FROM read_json_auto('{snapshot / 'environments.jsonl'}') "
        f"WHERE project_id = 'reporting-hub'").fetchall()
    assert flat == [("fra-02", None)], "the v1 datacenter scalar becomes one unnamed env"
    listed = con.execute(
        f"SELECT COUNT(*) FROM read_json_auto('{snapshot / 'environments.jsonl'}') "
        f"WHERE project_id = 'fakturierung'").fetchone()[0]
    assert listed == 3, "the environments list keeps all three"


def test_an_empty_table_still_has_its_columns_when_queried(tmp_path):
    """The first day nobody records a blocker, every query over the blockers
    table must still work.

    Unlike Parquet, a jsonl file does not carry its own schema - an empty one
    is an empty file. The column set therefore comes from the declaration that
    snapshot.py writes with and build_model.py reads with, shared via
    table_schemas() so the two cannot disagree.
    """
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "bare.yaml").write_text(
        "schema_version: 1\nid: bare\nmy_mandatory_field: TODO\n")
    snapshot = make_snapshot(tmp_path, projects)
    assert (snapshot / "blockers.jsonl").read_text() == "", "no blockers recorded"

    # The model counts blockers by status; that must not fail on an empty table.
    _, model = make_model(tmp_path, snapshot)
    table = model["tables"]["by_blocker_status"]
    assert table["available"] is True
    assert all(r["value"] == 0 for r in table["rows"])
    assert "unknown" in {r["status"] for r in table["rows"]}


# --------------------------------------------------------------------------
# Statelessness
# --------------------------------------------------------------------------

def test_nothing_is_carried_over_between_runs(tmp_path):
    """The tables are a pure function of the working tree.

    No previous generation, no deltas, no accumulating directories: deleting
    out/ and rebuilding must produce exactly the same thing.
    """
    snapshot = make_snapshot(tmp_path)
    first = (snapshot / "projects.jsonl").read_bytes()
    shutil.rmtree(snapshot)
    snapshot = make_snapshot(tmp_path)
    assert (snapshot / "projects.jsonl").read_bytes() == first
    assert not (tmp_path / "previous").exists()

    _, model = make_model(tmp_path, snapshot)
    assert "previous_tables" not in model["provenance"]
    for item in model["headline"]:
        assert "delta_vs_previous" not in item


# --------------------------------------------------------------------------
# Model shape (SPEC 5.2) and the definitions contract (SPEC 7)
# --------------------------------------------------------------------------

def test_model_has_the_documented_top_level_shape(tmp_path):
    snapshot = make_snapshot(tmp_path)
    _, model = make_model(tmp_path, snapshot)
    assert {"generated_at", "as_of_date", "provenance", "coverage", "headline",
            "tables", "charts", "flat", "definitions"} <= set(model)
    assert set(model["flat"]) == {"projects", "datastores", "environments",
                                  "blockers", "dependencies"}
    for key in ("git_sha", "schema_version", "pipeline_version",
                "image_digest", "project_count"):
        assert key in model["provenance"], key


def test_every_table_names_a_definition_that_exists(tmp_path):
    """SPEC 7: reference its key from every table in the model."""
    snapshot = make_snapshot(tmp_path)
    _, model = make_model(tmp_path, snapshot)
    known = {d["key"] for d in model["definitions"]}
    assert known, "definitions must be emitted into the model"
    for key, table in model["tables"].items():
        assert table["definition"] in known, f"{key} names an unknown definition"
    for item in model["headline"]:
        assert item["definition"] in known


def test_definitions_carry_both_languages(tmp_path):
    snapshot = make_snapshot(tmp_path)
    _, model = make_model(tmp_path, snapshot)
    for definition in model["definitions"]:
        assert definition["text_de"].strip(), definition["key"]
        assert definition["text_en"].strip(), definition["key"]


def test_labels_are_baked_in_so_renderers_never_read_the_taxonomy(tmp_path):
    snapshot = make_snapshot(tmp_path)
    _, model = make_model(tmp_path, snapshot)
    rows = model["tables"]["by_status"]["rows"]
    migrated = next(r for r in rows if r["migration_status"] == "migrated")
    assert migrated["migration_status_label_de"] == "Migriert"
    assert migrated["migration_status_label_en"] == "Migrated"


def test_flat_projects_carry_the_convenience_columns(tmp_path):
    """SPEC 6.4: managers filter on these without thinking about grain."""
    snapshot = make_snapshot(tmp_path)
    _, model = make_model(tmp_path, snapshot)
    row = next(r for r in model["flat"]["projects"] if r["project_id"] == "payment-gateway")
    assert row["datastore_engines"] == "db2, redis"
    assert row["has_db2"] == "full"
    assert row["sites"] == "muc-01"
    assert row["os_families"] == "windows"


def test_raw_and_verified_coverage_stay_separate(tmp_path):
    snapshot = make_snapshot(tmp_path)
    _, model = make_model(tmp_path, snapshot)
    coverage = model["coverage"]
    assert coverage["field_coverage_pct"] > coverage["verified_coverage_pct"]
    assert coverage["by_team"], "per-team coverage feeds the deck"


def test_snapshot_records_provenance(tmp_path):
    snapshot = make_snapshot(tmp_path)
    manifest = json.loads((snapshot / "manifest.json").read_text())
    for key in ("git_sha", "schema_version", "pipeline_version",
                "project_count", "row_counts", "duckdb_version"):
        assert key in manifest, key
    assert manifest["project_count"] == 12


def test_underscore_files_are_skipped_by_the_snapshot_too(tmp_path):
    snapshot = make_snapshot(tmp_path)
    con = duck()
    ids = {r[0] for r in con.execute(
        f"SELECT project_id FROM read_json_auto('{snapshot / 'projects.jsonl'}')").fetchall()}
    assert not any("manifest" in i for i in ids)
    assert len(ids) == 12
