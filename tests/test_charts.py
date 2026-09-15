"""Tests for tools/render/charts.py (M4).

The load-bearing ones:

  * test_the_png_is_byte_identical_across_runs - the model is reproducible
    (SPEC 5.2) and the PDF and the deck will embed these, so an image that
    moves makes every downstream artifact move with it.
  * test_a_table_with_no_data_still_gets_its_png - the Excel dashboard, the
    deck and the PDF all reference a chart by the path the model gave them.
    A missing file is a hole in the deck; a panel saying the field is not
    collected yet is an answer.
  * test_unknown_is_drawn_in_the_colour_the_taxonomy_gave_it - SPEC 11 says
    unknown is never silently dropped, and SPEC 7 says the honest chart shows
    the unknown bar. It is also the check that colours reach the renderer from
    the model rather than from taxonomy.yaml (SPEC 5.2).
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from test_pipeline import FIXTURES, REPO, make_model, make_snapshot, run

CHARTS = REPO / "tools" / "render" / "charts.py"

# The grey taxonomy.yaml gives `unknown`. Read from the file, never typed in:
# a test that hard-codes it passes after somebody recolours the taxonomy and
# the charts stop matching the rest of the report.
TAXONOMY = REPO / "schema" / "taxonomy.yaml"


def unknown_colour() -> str:
    from ruamel.yaml import YAML
    groups = (YAML(typ="safe").load(TAXONOMY.read_text(encoding="utf-8"))
              or {}).get("groups") or {}
    colours = {(group.get("codes") or {}).get("unknown", {}).get("colour")
               for group in groups.values()}
    colours.discard(None)
    assert len(colours) == 1, f"taxonomy.yaml disagrees about unknown: {colours}"
    return colours.pop()


def render(tmp_path: Path, *extra: str, projects: Path = FIXTURES,
           model_path: Path | None = None) -> tuple[Path, dict]:
    """model -> PNGs, the way report.sh does it."""
    if model_path is None:
        snapshot = make_snapshot(tmp_path, projects)
        model_path, _ = make_model(tmp_path, snapshot)
    model = json.loads(model_path.read_text())
    out = tmp_path / "charts"
    run(CHARTS, "--model", str(model_path), "--out", str(out), *extra)
    return out, model


@pytest.fixture(scope="module")
def charts(tmp_path_factory):
    return render(tmp_path_factory.mktemp("charts"))


# --------------------------------------------------------------------------
# What the model asked for is what lands on disk
# --------------------------------------------------------------------------

def png_size(path: Path) -> tuple[int, int]:
    """Width and height out of the IHDR chunk, without a PNG library."""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", header[16:24])


def test_every_chart_in_the_model_is_written_at_the_path_the_model_gave(charts):
    out, model = charts
    assert model["charts"], "the fixtures should exercise at least one chart"
    for chart in model["charts"]:
        path = out / chart["path"]
        assert path.is_file(), f"{chart['key']} was not rendered"
        assert png_size(path) == (chart["width_px"], chart["height_px"])


def test_filenames_come_from_the_chart_key(charts):
    """Deterministic filenames (SPEC 6.4): the embedders reference them."""
    out, model = charts
    for chart in model["charts"]:
        assert chart["path"] == f"{chart['key']}.png"
        assert (out / f"{chart['key']}.png").is_file()


def test_nothing_else_is_written(charts):
    out, model = charts
    assert sorted(p.name for p in out.iterdir()) == \
        sorted(chart["path"] for chart in model["charts"])


# --------------------------------------------------------------------------
# Reproducibility (SPEC 5.2)
# --------------------------------------------------------------------------

def test_the_png_is_byte_identical_across_runs(tmp_path):
    snapshot = make_snapshot(tmp_path)
    model_path, model = make_model(tmp_path, snapshot)
    rendered = []
    for name in ("a", "b"):
        out = tmp_path / name
        run(CHARTS, "--model", str(model_path), "--out", str(out))
        rendered.append({chart["path"]: (out / chart["path"]).read_bytes()
                         for chart in model["charts"]})
    assert rendered[0] == rendered[1]


def test_the_png_does_not_carry_the_renderer_version(charts):
    """matplotlib stamps its version into a `Software` chunk by default.

    That would change every image on an upgrade the pins already govern, and
    the whole point of pinning is that the bytes only move when we move them.
    """
    out, model = charts
    for chart in model["charts"]:
        assert b"Software" not in (out / chart["path"]).read_bytes(), chart["key"]


# --------------------------------------------------------------------------
# unknown, and where the colours come from
# --------------------------------------------------------------------------

def test_unknown_is_drawn_in_the_colour_the_taxonomy_gave_it(charts):
    """The honest chart shows the unknown bar (SPEC 7), in the same grey
    everywhere, and the renderer got that grey from the model - it never
    opens taxonomy.yaml (SPEC 5.2)."""
    out, model = charts
    grey = tuple(int(unknown_colour().lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))

    from PIL import Image
    for chart in model["charts"]:
        table = model["tables"][chart["key"]]
        column = table["columns"][0]["key"]
        row = next(r for r in table["rows"] if r[column] == "unknown")
        if not row.get("value"):
            continue  # nothing to draw; the bar is zero wide
        assert row[f"{column}_colour"] == unknown_colour(), \
            f"{chart['key']}: the model did not carry the unknown colour"
        pixels = set(Image.open(out / chart["path"]).convert("RGB").getdata())
        assert grey in pixels, f"{chart['key']} has no unknown bar in taxonomy grey"


def test_a_code_the_taxonomy_has_not_coloured_gets_a_palette_colour(charts):
    """Most codes have `colour: null` (SPEC 12.1 is still open). The renderer
    picks from its own palette for those rather than refusing to draw."""
    out, model = charts
    from PIL import Image
    from charts import SERIES  # noqa: E402 - added to sys.path by conftest

    table = model["tables"][model["charts"][0]["key"]]
    column = table["columns"][0]["key"]
    uncoloured = [r for r in table["rows"]
                  if r.get(f"{column}_colour") is None and r.get("value")]
    assert uncoloured, "the fixtures should have at least one uncoloured code"
    series = tuple(int(SERIES.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    pixels = set(Image.open(out / model["charts"][0]["path"])
                 .convert("RGB").getdata())
    assert series in pixels


# --------------------------------------------------------------------------
# The moving target (SPEC 6.3)
# --------------------------------------------------------------------------

def test_a_table_with_no_data_still_gets_its_png(tmp_path):
    """A field named in the report spec that nobody has collected yet.

    n/a, not a crash and not a zero - and not a missing file either: the
    dashboard, the deck and the PDF reference it by path.
    """
    snapshot = make_snapshot(tmp_path)
    model_path, model = make_model(tmp_path, snapshot)
    unavailable = [key for key, table in model["tables"].items()
                   if not table.get("available")]
    assert unavailable, "the fixtures should name a field nobody has collected"

    key = unavailable[0]
    model["charts"] = [{"key": key, "path": f"{key}.png",
                        "width_px": 1600, "height_px": 900,
                        "title_de": model["tables"][key]["title_de"],
                        "title_en": model["tables"][key]["title_en"]}]
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(model), encoding="utf-8")

    out, _ = render(tmp_path, model_path=edited)
    assert png_size(out / f"{key}.png") == (1600, 900)


def test_a_cross_tab_renders_as_a_stacked_bar(tmp_path):
    """The other shape in the model. Every code gets a segment, including
    unknown, and the legend means identity is never colour alone."""
    snapshot = make_snapshot(tmp_path)
    model_path, model = make_model(tmp_path, snapshot)
    key = next(k for k, t in model["tables"].items()
               if t.get("kind") == "cross_tab")
    model["charts"] = [{"key": key, "path": f"{key}.png", "width_px": 1200,
                        "height_px": 700, "title_de": "x", "title_en": "x"}]
    edited = tmp_path / "cross.json"
    edited.write_text(json.dumps(model), encoding="utf-8")
    out, _ = render(tmp_path, model_path=edited)
    assert png_size(out / f"{key}.png") == (1200, 700)


def test_a_chart_naming_no_table_warns_and_keeps_going(tmp_path):
    """build_model.py refuses to emit this, so it means a hand-edited model.
    Say so; do not guess what was meant, and do not take the run down."""
    snapshot = make_snapshot(tmp_path)
    model_path, model = make_model(tmp_path, snapshot)
    model["charts"].append({"key": "nonexistent", "path": "nonexistent.png",
                            "width_px": 800, "height_px": 400,
                            "title_de": "x", "title_en": "x"})
    edited = tmp_path / "bad.json"
    edited.write_text(json.dumps(model), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(CHARTS), "--model", str(edited),
         "--out", str(tmp_path / "out")],
        capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 0, proc.stderr
    assert "nonexistent" in proc.stderr
    assert not (tmp_path / "out" / "nonexistent.png").exists()


# --------------------------------------------------------------------------
# The air-gap trap SPEC 8.2 names for this renderer
# --------------------------------------------------------------------------

def test_a_missing_font_is_an_error_not_a_silent_substitution():
    """SPEC 8.2: a missing font does not error in matplotlib, it substitutes,
    and the report then looks different inside the air gap than out."""
    from charts import check_font

    with pytest.raises(RuntimeError) as excinfo:
        check_font(("No Such Font At All",))
    assert "Dockerfile" in str(excinfo.value)


def test_the_font_the_charts_ask_for_is_installed():
    """The assertion the renderer makes at startup, made once more here so a
    broken image fails the test suite rather than the daily run."""
    from charts import FONT_STACK, check_font

    assert check_font() in FONT_STACK


def test_lang_switches_the_labels(tmp_path):
    """Primary audience is German, both languages must render (SPEC 1)."""
    snapshot = make_snapshot(tmp_path)
    model_path, model = make_model(tmp_path, snapshot)
    rendered = {}
    for lang in ("de", "en"):
        out = tmp_path / lang
        run(CHARTS, "--model", str(model_path), "--out", str(out), "--lang", lang)
        rendered[lang] = (out / model["charts"][0]["path"]).read_bytes()
    assert rendered["de"] != rendered["en"], "the labels should differ"
