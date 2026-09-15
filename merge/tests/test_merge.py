"""Tests for merge_csv.py - step 1 of the import (SPEC 3.1, 5.4).

The load-bearing ones are the two SPEC 5.4 calls the numbers depend on:
first-*non-empty* wins per cell, and every discarded value reaching
merge_conflicts.csv - which is the only record that it ever existed.

Fixtures: fixtures/input/ holds three sources that deliberately overlap,
contradict, leave gaps, spell a key in the wrong case, carry a row with no key
at all, and disagree about encoding and delimiter.
"""
from __future__ import annotations

import csv
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MERGE = HERE.parent / "merge_csv.py"
FIXTURES = HERE / "fixtures" / "input"
INVALID = HERE / "fixtures" / "no_key_column"

KEY = "Projekt-Nr"


def run(source_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """--sources is always explicit here: its default is the directory
    merge_csv.py lives in, and a test must not write into the repo."""
    return subprocess.run(
        [sys.executable, str(MERGE), "--key", KEY, "--sources", str(source_dir), *args],
        capture_output=True, text=True)


def sources(root: Path) -> Path:
    """The fixtures, copied under root/input so nothing writes into the repo."""
    work = root / "input"
    shutil.copytree(FIXTURES, work)
    return work


@pytest.fixture
def merged(tmp_path: Path) -> Path:
    """Returns the directory the RESULTS are in, which is the parent of the
    source directory: the merge writes one level up so that what it reads
    holds nothing but sources."""
    result = run(sources(tmp_path))
    assert result.returncode == 0, result.stderr
    return tmp_path


def read_merged(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def by_key(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {r[KEY]: r for r in rows if r[KEY]}


# --------------------------------------------------------------------------
# The merge rules (SPEC 5.4)
# --------------------------------------------------------------------------

def test_first_non_empty_wins_per_cell(merged: Path):
    """01 wins where it has a value; where it is blank, 02 fills the gap.

    Both halves matter. The first is first-wins; the second is that an empty
    cell is not a contradiction, so a blank never beats a real value.
    """
    rows = by_key(read_merged(merged / "merged.csv"))
    assert rows["P-1001"]["CPU (Kerne)"] == "16"       # 01 wins over 02's 32
    assert rows["P-1002"]["CPU (Kerne)"] == "8"        # 01 blank, filled from 02
    assert rows["P-1003"]["Datenbank"] == "Oracle"     # 01 blank, filled from 03


def test_merge_is_per_cell_not_per_row(merged: Path):
    """No file wins a whole project: one row draws from all three."""
    row = by_key(read_merged(merged / "merged.csv"))["P-1003"]
    assert row["Team"] == "Team Gamma"                 # only 01 has it
    assert row["Datenbank"] == "Oracle"                # only 03 has it
    assert row["Wartungsfenster"] == "Sa 02:00-04:00"  # only 03 has it


def test_every_discarded_value_is_recorded(merged: Path):
    """SPEC 5.4.1: the conflicts file is the only record of what was thrown
    away, so a value that loses and is not listed is gone without trace."""
    with (merged / "merge_conflicts.csv").open(encoding="utf-8-sig", newline="") as fh:
        conflicts = list(csv.DictReader(fh, delimiter=";"))

    discarded = {(c["key"], c["column"], c["discarded_value"]) for c in conflicts}
    assert discarded == {
        ("P-1001", "CPU (Kerne)", "32"),
        ("P-1001", "Datenbank", "Redis"),
        ("P-1001", "Standort RZ", "muc-01"),
    }
    for conflict in conflicts:
        assert conflict["winning_file"] == "01-sap-export.csv"
        assert conflict["discarded_file"] == "02-cmdb-dump.csv"


def test_filling_a_blank_is_not_a_conflict(merged: Path):
    """P-1002's CPU came from 02 because 01 was blank. Nobody disagreed, so
    nothing was discarded and nothing may be reported."""
    text = (merged / "merge_conflicts.csv").read_text(encoding="utf-8-sig")
    assert "P-1002" not in text


def test_values_are_not_normalised(merged: Path):
    """MUC-01 vs muc-01 is a reported disagreement, not a silent match.
    Deciding they mean the same thing is value_map.yaml's job in step 2."""
    text = (merged / "merge_conflicts.csv").read_text(encoding="utf-8-sig")
    assert "muc-01" in text


# --------------------------------------------------------------------------
# The join key (SPEC 3.1.1)
# --------------------------------------------------------------------------

def test_keys_merge_across_case_and_are_reported(tmp_path: Path):
    """p-1001 and P-1001 are one project - and the assumption is visible."""
    work = sources(tmp_path)
    result = run(work)

    rows = read_merged(tmp_path / "merged.csv")
    assert [r[KEY] for r in rows].count("P-1001") == 1
    assert "p-1001" not in [r[KEY] for r in rows]
    assert "casefolding" in result.stderr
    assert "'P-1001' <- 'p-1001'" in result.stderr


def test_blank_keys_never_merge_with_each_other(merged: Path):
    """Two rows with no key stay two rows. Collapsing them would invent one
    project holding the merged remains of everything nobody keyed."""
    rows = read_merged(merged / "merged.csv")
    keyless = [r for r in rows if not r[KEY]]
    assert len(keyless) == 2
    assert {r["Anwendungsname"] for r in keyless} == {"Nicht zugeordnet", "Ohne Nummer"}
    # and they did not bleed into one another
    assert {r["Team"] for r in keyless} == {"Team Delta", ""}


def test_blank_keys_are_reported(tmp_path: Path):
    work = sources(tmp_path)
    assert "2 row(s) had no" in run(work).stderr


def test_missing_key_column_is_an_error_naming_the_file(tmp_path: Path):
    """A source without the key column is named, not quietly skipped."""
    work = tmp_path / "input"
    shutil.copytree(INVALID, work)
    result = run(work)
    assert result.returncode == 1            # invalid data, not a usage error
    assert "02-keyless.csv" in result.stderr
    assert not (tmp_path / "merged.csv").exists()   # nothing written at all


def test_key_is_required(tmp_path: Path):
    """No default and no auto-detection: a wrong guess produces a plausible
    file with the wrong number of rows, which is the failure this must not have."""
    result = subprocess.run(
        [sys.executable, str(MERGE), "--sources", str(FIXTURES)],
        capture_output=True, text=True)
    assert result.returncode == 2
    assert "--key" in result.stderr


# --------------------------------------------------------------------------
# Sources, columns, dialect
# --------------------------------------------------------------------------

def test_its_own_output_is_not_a_source(merged: Path):
    """merged.csv must not be read back on the next run. It sorts among the
    others and would win every contest by being read first - so the fixture
    directory ships one, holding a project that must never appear."""
    rows = read_merged(merged / "merged.csv")
    assert "P-9999" not in [r[KEY] for r in rows]
    assert len(rows) == 7


def test_column_union_in_first_seen_order(merged: Path):
    """The highest-ranked source keeps its layout; later-only columns are
    appended rather than interleaved."""
    with (merged / "merged.csv").open(encoding="utf-8-sig", newline="") as fh:
        header = next(csv.reader(fh, delimiter=";"))
    assert header[:6] == [KEY, "Anwendungsname", "Team", "Standort RZ",
                          "CPU (Kerne)", "Datenbank"]
    assert header[6:] == ["Storage (GB)", "Standort  RZ", "Wartungsfenster"]


def test_near_identical_headers_warn_and_stay_separate(tmp_path: Path):
    """'Standort RZ' and 'Standort  RZ' are two columns that will never merge.
    That is allowed to happen; it is not allowed to happen silently."""
    work = sources(tmp_path)
    result = run(work)
    assert "near-identical column names" in result.stderr
    assert "Standort  RZ" in result.stderr


def test_per_file_encoding_and_delimiter(merged: Path):
    """03 is cp1252 and ';'-delimited, 02 is ','-delimited. Both are read
    correctly, and the umlaut survives into the merged file."""
    rows = by_key(read_merged(merged / "merged.csv"))
    assert rows["P-1005"]["Anwendungsname"] == "Tarifrechner Prüfung"
    assert rows["P-1004"]["Storage (GB)"] == "100"


def test_written_for_a_german_excel_reader(merged: Path):
    """BOM, ';' and CRLF (SPEC 3.2). Without the BOM every umlaut renders
    wrong and the reviewer distrusts the file for the wrong reason."""
    raw = (merged / "merged.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.split(b"\r\n")[0].count(b";") >= 8
    assert b"\n" not in raw.replace(b"\r\n", b"")


# --------------------------------------------------------------------------
# The handoff to step 2 is manual, and the tool's last word is the instruction
# --------------------------------------------------------------------------

def test_nothing_reaches_the_destination(tmp_path: Path):
    """The copy into import/ is the inspection gate. If the merge made it
    itself there would be no gate, only a pause.

    The results do land outside the source directory - one level up, beside
    merge.sh - so what must be true is narrower than "writes nothing
    elsewhere": nothing appears at the destination it names.
    """
    work = sources(tmp_path)
    destination = tmp_path / "import" / "merged.csv"
    assert run(work, "--destination", str(destination)).returncode == 0

    assert (tmp_path / "merged.csv").exists()      # written, beside merge.sh
    assert not destination.exists()                # but not handed over
    assert not destination.parent.exists()


def test_it_prints_a_copy_command_that_can_be_pasted(tmp_path: Path):
    work = sources(tmp_path)
    lines = run(work).stdout.strip().splitlines()

    assert lines[-1].strip().startswith("cp ")
    assert lines[-1].strip().endswith("import/merged.csv")
    assert "merged.csv" in lines[-1]


def test_the_destination_is_configurable(tmp_path: Path):
    work = sources(tmp_path)
    out = run(work, "--destination", "elsewhere/data.csv").stdout
    assert "elsewhere/data.csv" in out
    assert not (tmp_path / "elsewhere").exists()   # named, not written


# --------------------------------------------------------------------------
# Reproducibility - the same discipline as the model, the workbook and the PDF
# --------------------------------------------------------------------------

def test_byte_identical_across_runs(tmp_path: Path):
    first, second = tmp_path / "a", tmp_path / "b"
    for root in (first, second):
        assert run(sources(root)).returncode == 0

    for name in ("merged.csv", "merge_conflicts.csv", "merge_manifest.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_manifest_records_the_provenance(merged: Path):
    """Step 2 cannot see the sources, so their identity has to survive here
    (SPEC 3.5). No timestamp: this file is part of what must be reproducible."""
    import json
    manifest = json.loads((merged / "merge_manifest.json").read_text(encoding="utf-8"))

    assert manifest["join_key"] == KEY
    assert [s["file"] for s in manifest["sources"]] == [
        "01-sap-export.csv", "02-cmdb-dump.csv", "03-team-umfrage.csv"]
    assert [s["rank"] for s in manifest["sources"]] == [1, 2, 3]
    assert all(len(s["sha256"]) == 64 for s in manifest["sources"])
    assert manifest["rows_in"] == 10 and manifest["rows_out"] == 7
    assert manifest["conflicts"] == 3 and manifest["blank_keys"] == 2

    # No clock reading: the import date is stamped by step 2, which is where
    # SPEC 3.5 puts it. (Checked on the keys, not on the serialised text - the
    # data is German and 'Datenbank' contains 'date'.)
    keys = set(manifest) | {k for s in manifest["sources"] for k in s}
    assert not {k for k in keys if any(word in k.lower() for word in
                                       ("date", "time", "stamp", "generated"))}


def test_precedence_is_sorted_filename_order(tmp_path: Path):
    """Rename the lowest-ranked source so it sorts first and it wins instead.
    This is the documented cost of deriving precedence from the filename."""
    work = sources(tmp_path)
    (work / "03-team-umfrage.csv").rename(work / "00-team-umfrage.csv")
    assert run(work).returncode == 0

    rows = by_key(read_merged(tmp_path / "merged.csv"))
    assert rows["P-1003"]["Datenbank"] == "Oracle"
    # 00 now outranks 01, so its spelling of the shared column wins
    assert rows["P-1003"]["Standort  RZ"] == "MUC-01"
