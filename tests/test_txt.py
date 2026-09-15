"""Tests for tools/render/txt.py (M4).

The load-bearing ones:

  * test_one_changed_field_changes_only_the_lines_it_touches - the whole
    reason this renderer exists (SPEC 6.4). There is no day-over-day
    comparison in the pipeline (SPEC 12.5); `diff` on two of these is it, and
    a layout that reflows makes the diff useless.
  * test_every_number_on_the_page_is_the_models_number - the renderer lays
    out and computes nothing (SPEC 11).
  * test_unknown_is_on_the_page_never_dropped - SPEC 11 again, in the output
    people paste into mails.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_pipeline import FIXTURES, REPO, make_model, make_snapshot, run

TXT = REPO / "tools" / "render" / "txt.py"
TEMPLATE = REPO / "templates" / "report.txt.j2"


def render(tmp_path: Path, *extra: str, projects: Path = FIXTURES,
           name: str = "txt") -> tuple[str, dict]:
    """model -> report.txt, the way report.sh does it."""
    snapshot = make_snapshot(tmp_path / name, projects)
    model_path, model = make_model(tmp_path / name, snapshot)
    out = tmp_path / name / "report"
    run(TXT, "--model", str(model_path), "--out", str(out), *extra)
    return (out / "report.txt").read_text(encoding="utf-8"), model


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    return render(tmp_path_factory.mktemp("txt"))


# --------------------------------------------------------------------------
# Fixed column widths: what makes the diff readable
# --------------------------------------------------------------------------

def test_no_line_is_wider_than_the_page(report):
    from txt import WIDTH

    text, _ = report
    too_wide = [line for line in text.splitlines() if len(line) > WIDTH]
    assert not too_wide, too_wide[:3]


def test_no_line_carries_trailing_whitespace(report):
    """Invisible on screen, loud in a diff, and a fixed-width layout produces
    a lot of it."""
    text, _ = report
    assert not [line for line in text.splitlines() if line != line.rstrip()]


def test_the_columns_do_not_move_when_the_data_does(tmp_path):
    """A layout that sizes columns to the data reflows every row the day one
    label gets longer, and the day-over-day diff is then noise."""
    projects = tmp_path / "projects"
    shutil.copytree(FIXTURES, projects)
    text, _ = render(tmp_path, name="before")

    # A much longer name in one project. Nothing about the layout may move.
    target = projects / "payment-gateway.yaml"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "name: Payment Gateway",
            "name: " + "Zahlungsverkehr Gateway Plattform " * 4),
        encoding="utf-8")
    widened, _ = render(tmp_path, projects=projects, name="after")

    def positions(text: str) -> list[tuple[int, str]]:
        """Where each table's Code/Label/Anzahl header sits."""
        return [(index, line) for index, line in enumerate(text.splitlines())
                if line.startswith("Code")]

    assert positions(text) == positions(widened)


def test_one_changed_field_changes_only_the_lines_it_touches(tmp_path):
    """The fastest way to answer "what changed since yesterday" (SPEC 6.4).

    One project moves from `assessed` to `migrated`. Two rows of one
    distribution and one headline number change; nothing else may.
    """
    projects = tmp_path / "projects"
    shutil.copytree(FIXTURES, projects)
    before, model = render(tmp_path, projects=projects, name="before")

    column = model["tables"]["by_status"]["columns"][0]["key"]
    moved = next(r for r in model["flat"]["projects"]
                 if r.get(column) == "assessed")
    path = projects / f"{moved['project_id']}.yaml"
    path.write_text(path.read_text(encoding="utf-8")
                    .replace("status: assessed", "status: migrated"),
                    encoding="utf-8")

    after, _ = render(tmp_path, projects=projects, name="after")
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines())
               if a != b]
    assert changed, "the edit must show up at all"
    assert len(changed) <= 4, changed
    assert len(before.splitlines()) == len(after.splitlines()), \
        "the report must not change shape over a value change"


# --------------------------------------------------------------------------
# The renderer computes nothing (SPEC 11)
# --------------------------------------------------------------------------

def test_every_number_on_the_page_is_the_models_number(report):
    text, model = report
    for key, table in sorted((model["tables"]).items()):
        if not table.get("available") or table.get("kind") != "distribution":
            continue
        block = section(text, key)
        column = table["columns"][0]["key"]
        for row in table["rows"]:
            line = next((l for l in block if l.startswith(row[column] + " ")
                         or l == row[column]), None)
            assert line is not None, f"{key}: {row[column]} is missing"
            assert line.split()[-1] == str(row["value"]), f"{key}: {line}"


def test_the_headline_numbers_are_the_models(report):
    text, model = report
    for item in model["headline"]:
        line = next(l for l in text.splitlines()
                    if l.startswith(item["label_de"]))
        assert str(item["value"]) in line
        # Every number names the rule it was counted under (SPEC 7).
        assert item["definition"] in line


def test_raw_and_verified_coverage_stay_two_numbers(report):
    """SPEC 11: never merged into one, everywhere they appear."""
    text, model = report
    coverage = model["coverage"]
    assert str(coverage["field_coverage_pct"]) in text
    assert str(coverage["verified_coverage_pct"]) in text
    for team in coverage["by_team"]:
        line = next(l for l in text.splitlines()
                    if l.startswith(team["team_id"]))
        assert str(team["coverage_pct"]) in line
        assert str(team["verified_pct"]) in line


def test_the_counting_rules_are_printed_next_to_the_numbers(report):
    """SPEC 7: a number and the rule it was counted under never travel
    separately."""
    text, model = report
    for definition in model["definitions"]:
        assert definition["key"] in text
        assert definition["text_de"].split(".")[0][:40] in text


def test_provenance_is_on_the_page(report):
    """The txt is the copy that gets pasted into a mail; it has to say which
    commit and which image produced it."""
    text, model = report
    provenance = model["provenance"]
    assert provenance["git_sha"] in text
    assert str(provenance["schema_version"]) in text
    assert str(provenance["pipeline_version"]) in text


def test_the_charts_are_listed_by_the_path_the_model_gave(report):
    text, model = report
    for chart in model["charts"]:
        assert chart["path"] in text


# --------------------------------------------------------------------------
# unknown, n/a, and the moving target
# --------------------------------------------------------------------------

def section(text: str, key: str) -> list[str]:
    """The lines of one table's block, by the table key printed under it."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(key + " "))
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("---") and i > start + 1), len(lines))
    return lines[start:end]


def test_unknown_is_on_the_page_never_dropped(report):
    text, model = report
    for key, table in (model["tables"]).items():
        if not table.get("available"):
            continue
        assert any(line.startswith("unknown") for line in section(text, key)), \
            f"{key} lost its unknown row"


def test_a_field_nobody_collected_yet_prints_its_note(report):
    """n/a, not a crash and not a zero, and not an omitted section either:
    an absent row and a zero row mean different things (SPEC 11)."""
    text, model = report
    key, table = next((k, t) for k, t in model["tables"].items()
                      if not t.get("available"))
    assert table["note_de"] in text
    assert not any(line.startswith("unknown") for line in section(text, key))


def test_a_headline_number_the_data_cannot_answer_is_na_not_zero(tmp_path):
    """SPEC 5.1: missing renders as n/a, never as zero. Zero is an answer."""
    snapshot = make_snapshot(tmp_path)
    model_path, model = make_model(tmp_path, snapshot)
    model["headline"][1]["value"] = None
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "na"
    run(TXT, "--model", str(edited), "--out", str(out))
    line = next(l for l in (out / "report.txt").read_text(encoding="utf-8")
                .splitlines() if l.startswith(model["headline"][1]["label_de"]))
    assert "n/a" in line and " 0 " not in line


# --------------------------------------------------------------------------
# Traps
# --------------------------------------------------------------------------

def test_lang_switches_the_labels(tmp_path):
    """Primary audience is German, but the labels must support both (SPEC 1)."""
    snapshot = make_snapshot(tmp_path)
    model_path, model = make_model(tmp_path, snapshot)
    pages = {}
    for lang in ("de", "en"):
        out = tmp_path / lang
        run(TXT, "--model", str(model_path), "--out", str(out), "--lang", lang)
        pages[lang] = (out / "report.txt").read_text(encoding="utf-8")
    row = model["tables"]["by_status"]["rows"][0]
    assert row["migration_status_label_de"] in pages["de"]
    assert row["migration_status_label_en"] in pages["en"]
    assert pages["de"] != pages["en"]


def test_a_typo_in_the_template_is_an_error_not_a_missing_line(tmp_path):
    """StrictUndefined: a template that silently drops a section is worse
    than one that fails, because nobody notices the number that left."""
    snapshot = make_snapshot(tmp_path)
    model_path, _ = make_model(tmp_path, snapshot)
    broken = tmp_path / "broken.txt.j2"
    broken.write_text("{{ model.no_such_key.at_all }}\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(TXT), "--model", str(model_path),
         "--out", str(tmp_path / "out"), "--template", str(broken)],
        capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "txt:" in proc.stderr


def test_the_template_is_committed():
    assert TEMPLATE.is_file(), "templates/report.txt.j2 is missing"


def test_the_text_is_byte_identical_across_runs(tmp_path):
    snapshot = make_snapshot(tmp_path)
    model_path, _ = make_model(tmp_path, snapshot)
    written = []
    for name in ("a", "b"):
        out = tmp_path / name
        run(TXT, "--model", str(model_path), "--out", str(out))
        written.append((out / "report.txt").read_bytes())
    assert written[0] == written[1]


def test_german_text_survives_as_utf8(report):
    """The source data is German and the page is read in a terminal."""
    text, _ = report
    assert re.search(r"[äöüÄÖÜß]", text), "the German labels should be intact"
