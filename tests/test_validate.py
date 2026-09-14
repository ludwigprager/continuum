"""Tests for validate.py.

Data-driven on purpose: a new failure mode is a directory under
tests/fixtures/invalid/ plus an expect.txt, never an edit to this file.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
VALIDATE = REPO / "tools" / "validate.py"
SCHEMA = REPO / "schema"
VALID = REPO / "tests" / "fixtures" / "projects"
INVALID_ROOT = REPO / "tests" / "fixtures" / "invalid"

EXIT_OK, EXIT_INVALID, EXIT_TOOL = 0, 1, 2


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VALIDATE), *args],
        capture_output=True, text=True, cwd=str(cwd or REPO))


def run_json(*args: str) -> tuple[int, dict]:
    proc = run(*args, "--format", "json")
    return proc.returncode, json.loads(proc.stdout)


def codes(payload: dict) -> list[str]:
    return [f["code"] for f in payload["findings"]]


def invalid_cases() -> list[str]:
    return sorted(d.name for d in INVALID_ROOT.iterdir()
                  if d.is_dir() and (d / "expect.txt").exists())


# --------------------------------------------------------------------------
# M1 acceptance
# --------------------------------------------------------------------------

def test_valid_fixtures_pass():
    proc = run("--projects", str(VALID), "--schema", str(SCHEMA))
    assert proc.returncode == EXIT_OK, proc.stdout + proc.stderr


def test_valid_fixtures_have_no_errors_only_warnings():
    _, payload = run_json("--projects", str(VALID), "--schema", str(SCHEMA))
    assert payload["summary"]["errors"] == 0
    assert payload["summary"]["warnings"] > 0, \
        "the fixtures are supposed to exercise the plausibility layer"


@pytest.mark.parametrize("case", invalid_cases())
def test_invalid_fixture_fails_with_expected_findings(case):
    directory = INVALID_ROOT / case
    expected = [line.strip() for line in
                (directory / "expect.txt").read_text().splitlines() if line.strip()]

    rc, payload = run_json("--projects", str(directory), "--schema", str(SCHEMA))
    assert rc == EXIT_INVALID, f"{case} should exit 1, got {rc}"

    reported = codes(payload)
    for code in set(expected):
        assert reported.count(code) >= expected.count(code), \
            f"{case}: expected {expected.count(code)}x {code}, got {reported}"


@pytest.mark.parametrize("case", invalid_cases())
def test_every_error_names_a_file_and_a_line(case):
    """file:line:fix is the contract (HANDOFF 6.1), not a nicety."""
    _, payload = run_json("--projects", str(INVALID_ROOT / case), "--schema", str(SCHEMA))
    for finding in payload["findings"]:
        if finding["level"] != "error":
            continue
        assert finding["file"], f"{case}: {finding['code']} has no file"
        assert finding["line"], f"{case}: {finding['code']} has no line"
        assert finding["fix"], f"{case}: {finding['code']} has no fix hint"


def test_error_message_format_matches_the_spec():
    """The exact shape from HANDOFF 6.1, not jsonschema's default wording."""
    proc = run("--projects", str(INVALID_ROOT / "unknown-enum"), "--schema", str(SCHEMA))
    out = proc.stdout
    assert "unknown-enum/project.yaml:9:21" in out
    assert "classification_b: 'CB-7' is not a known code" in out
    assert "valid: CB-1, CB-2, CB-3, CB-4" in out
    assert "schema/taxonomy.yaml" in out
    assert "ValidationError" not in out, "raw jsonschema wording leaked into the output"


def test_cycle_is_reported_with_the_ring_not_just_its_existence():
    proc = run("--projects", str(INVALID_ROOT / "cycle"), "--schema", str(SCHEMA))
    assert "alpha-service -> beta-service -> gamma-service -> alpha-service" in proc.stdout


# --------------------------------------------------------------------------
# Exit-code contract (HANDOFF 5.3)
# --------------------------------------------------------------------------

def test_missing_projects_dir_is_a_tool_error_not_a_data_error():
    assert run("--projects", "/nonexistent", "--schema", str(SCHEMA)).returncode == EXIT_TOOL


def test_empty_projects_dir_is_a_tool_error(tmp_path):
    # Exiting 0 on an empty directory is how a miswired CI job passes forever.
    assert run("--projects", str(tmp_path), "--schema", str(SCHEMA)).returncode == EXIT_TOOL


def test_warnings_alone_do_not_fail_but_do_under_strict():
    assert run("--projects", str(VALID), "--schema", str(SCHEMA)).returncode == EXIT_OK
    assert run("--projects", str(VALID), "--schema", str(SCHEMA),
               "--strict").returncode == EXIT_INVALID


# --------------------------------------------------------------------------
# Contracts the rest of the pipeline depends on
# --------------------------------------------------------------------------

def test_underscore_files_are_skipped():
    """_import_manifest.yaml is provenance, not a project (HANDOFF 3)."""
    _, payload = run_json("--projects", str(VALID), "--schema", str(SCHEMA))
    files = {p["file"] for p in payload["coverage"]["by_project"]}
    assert not any("_import_manifest" in f for f in files)
    assert payload["summary"]["files"] == 12


def test_output_is_deterministic():
    """M2 needs byte-identical output; the habit starts here."""
    first = run("--projects", str(VALID), "--schema", str(SCHEMA), "--format", "json")
    second = run("--projects", str(VALID), "--schema", str(SCHEMA), "--format", "json")
    assert first.stdout == second.stdout


def test_unknown_and_null_and_missing_all_count_as_not_answered(tmp_path):
    """The three ways of saying nothing must be indistinguishable in coverage.

    Asserted by construction rather than against a fixture: the same file
    written three ways must produce the same number.
    """
    schema_args = ("--schema", str(SCHEMA))
    spellings = {
        "unknown": "classification:\n  classification_a: unknown\n",
        "null":    "classification:\n  classification_a: null\n",
        "missing": "",
    }
    results = {}
    for name, block in spellings.items():
        directory = tmp_path / name
        directory.mkdir()
        (directory / "p.yaml").write_text(
            f"schema_version: 1\nid: p\nname: Same project\n{block}")
        rc, payload = run_json("--projects", str(directory), *schema_args)
        assert rc == EXIT_OK, payload
        results[name] = payload["coverage"]["field_coverage_pct"]
    assert len(set(results.values())) == 1, results

    # And a file that answers nothing at all is 0%, not a validation failure.
    _, payload = run_json("--projects", str(VALID), *schema_args)
    by_id = {p["id"]: p for p in payload["coverage"]["by_project"]}
    assert by_id["minimal-record"]["coverage_pct"] == 0.0, \
        "id and schema_version alone answer nothing"
    # Both of these fill in `name` and one more field and nothing else: every
    # other key is present but set to null / unknown, and must not count.
    assert by_id["lagerverwaltung"]["fields_filled"] == 2, "explicit nulls must not count"
    assert by_id["legacy-crm"]["fields_filled"] == 2, "`unknown` must not count"


def test_raw_and_verified_coverage_are_separate_numbers():
    """HANDOFF 11: imported data is not verified data."""
    _, payload = run_json("--projects", str(VALID), "--schema", str(SCHEMA))
    coverage = payload["coverage"]
    assert coverage["field_coverage_pct"] > coverage["verified_coverage_pct"]
    assert coverage["verified_coverage_pct"] == pytest.approx(8.3, abs=0.1)


def test_both_placement_shapes_validate():
    """HANDOFF 12.4 is open; neither shape may fail while it is."""
    _, payload = run_json("--projects", str(VALID), "--schema", str(SCHEMA))
    assert payload["summary"]["errors"] == 0
    ids = {p["id"] for p in payload["coverage"]["by_project"]}
    assert {"reporting-hub", "payment-gateway"} <= ids


def test_json_output_has_a_stable_top_level_shape():
    _, payload = run_json("--projects", str(VALID), "--schema", str(SCHEMA))
    assert set(payload) == {"version", "summary", "coverage", "findings"}
    assert set(payload["coverage"]) == {
        "projects_total", "fields_tracked", "field_coverage_pct",
        "verified_coverage_pct", "by_team", "by_project"}


# --------------------------------------------------------------------------
# The schema self-test
# --------------------------------------------------------------------------

def test_schema_self_test_passes():
    assert run("--schema", str(SCHEMA), "--check-schema").returncode == EXIT_OK


def test_schema_self_test_warns_about_unlabelled_codes_and_provisional_files():
    _, payload = run_json("--schema", str(SCHEMA), "--check-schema")
    reported = {f["code"] for f in payload["findings"]}
    assert "schema.unlabelled-codes" in reported
    assert "schema.provisional-reference" in reported


def test_schema_self_test_catches_a_typo_in_an_annotation(tmp_path):
    """The worst failure mode of the annotation design is an annotation that
    silently validates nothing. It must be caught, not ignored."""
    schema_dir = tmp_path / "schema"
    shutil.copytree(SCHEMA, schema_dir)
    doc = json.loads((schema_dir / "project.schema.json").read_text())
    doc["properties"]["classification"]["properties"]["security_class"]["x-taxonmy"] = "oops"
    (schema_dir / "project.schema.json").write_text(json.dumps(doc))

    rc, payload = run_json("--schema", str(schema_dir), "--check-schema")
    assert rc == EXIT_INVALID
    assert "schema.unknown-annotation" in {f["code"] for f in payload["findings"]}


def test_schema_self_test_catches_a_taxonomy_group_that_does_not_exist(tmp_path):
    schema_dir = tmp_path / "schema"
    shutil.copytree(SCHEMA, schema_dir)
    doc = json.loads((schema_dir / "project.schema.json").read_text())
    doc["properties"]["classification"]["properties"]["security_class"]["x-taxonomy"] = "nope"
    (schema_dir / "project.schema.json").write_text(json.dumps(doc))

    rc, payload = run_json("--schema", str(schema_dir), "--check-schema")
    assert rc == EXIT_INVALID
    assert "schema.unknown-taxonomy-group" in {f["code"] for f in payload["findings"]}


# --------------------------------------------------------------------------
# M1 acceptance 4: adding a field must not touch validate.py
# --------------------------------------------------------------------------

def test_a_new_coded_field_needs_only_schema_and_taxonomy_edits(tmp_path):
    """The whole point of the annotation design. If this test needs a change
    to validate.py to pass, the design has failed."""
    before = VALIDATE.read_bytes()

    schema_dir = tmp_path / "schema"
    shutil.copytree(SCHEMA, schema_dir)
    projects = tmp_path / "projects"
    projects.mkdir()

    # 1. a new taxonomy group
    taxonomy = (schema_dir / "taxonomy.yaml").read_text()
    taxonomy += """
  backup_class:
    order: [gold, silver, none, unknown]
    codes:
      gold:    {label_de: Gold, label_en: Gold, colour: null}
      silver:  {label_de: Silber, label_en: Silver, colour: null}
      none:    {label_de: Keine, label_en: None, colour: null}
      unknown: {label_de: Unbekannt, label_en: Unknown, colour: null}
"""
    (schema_dir / "taxonomy.yaml").write_text(taxonomy)

    # 2. a field pointing at it
    doc = json.loads((schema_dir / "project.schema.json").read_text())
    doc["properties"]["operations"]["properties"]["backup_class"] = {
        "$ref": "#/$defs/code", "x-taxonomy": "backup_class", "x-tracked": True}
    (schema_dir / "project.schema.json").write_text(json.dumps(doc, indent=2))

    # 3. that is all. No code change.
    assert VALIDATE.read_bytes() == before

    assert run("--schema", str(schema_dir), "--check-schema").returncode == EXIT_OK

    (projects / "good.yaml").write_text(
        "schema_version: 1\nid: good\noperations:\n  backup_class: gold\n")
    rc, payload = run_json("--projects", str(projects), "--schema", str(schema_dir))
    assert rc == EXIT_OK, payload

    # the new field counts toward coverage...
    assert payload["coverage"]["fields_tracked"] == 18

    # ...and a bad code in it is rejected, with the new group named.
    (projects / "good.yaml").write_text(
        "schema_version: 1\nid: good\noperations:\n  backup_class: platinum\n")
    rc, payload = run_json("--projects", str(projects), "--schema", str(schema_dir))
    assert rc == EXIT_INVALID
    finding = next(f for f in payload["findings"] if f["code"] == "ref.unknown-code")
    assert "platinum" in finding["message"]
    assert "gold, none, silver, unknown" in finding["fix"]
