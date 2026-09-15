#!/usr/bin/env python3
"""
pptx.py - the management deck. SPEC 6.4, milestone M5.

    tools/render/pptx.py --model report_model.json --out DIR [--lang de|en]

python-pptx, working from templates/deck.potx. The deck is never built from
scratch and never produced by converting the xlsx or the pdf through
LibreOffice headless (SPEC 11): slides are created from the template's own
layouts and the template's placeholders are filled by `idx`.

**The corporate template has not been supplied yet (SPEC 12.2).** Until it
does, `templates/deck.potx` is the plausible placeholder that
`tools/make_deck_template.py` writes. Everything this renderer knows about
the template is the LAYOUTS dict below - one dict, at the top of the file, so
swapping in the real template is the five-minute job SPEC 6.4 asks for. The
names in it are checked against the template on every run and a mismatch is a
warning naming both sides, because a layout index that quietly moved produces
a deck that is wrong rather than a deck that fails.

Like every renderer this one computes nothing (SPEC 11) and contains no field
name, no code and no enum value (SPEC 6.1.1). The slides are whatever the
model holds: its headline numbers, its coverage, one slide per chart it names
and one per table it carries, including the tables it marks unavailable -
those print their own note rather than vanishing, because an absent slide and
a zero say different things.

Two things that bite:

  * **python-pptx refuses a real `.potx`.** A PowerPoint template differs from
    a presentation by one OPC content type, and python-pptx checks it and
    raises `ValueError: ... is not a PowerPoint file`. The corporate template
    will be a genuine .potx, so `load_template` swaps that content type in a
    copy held in memory. The file on disk is untouched.
  * **Charts are drawn by charts.py and embedded here** (SPEC 2), at the size
    the model declares. The picture placeholder of a template crops an image
    to fill its frame, which would cut the axis labels off a wide chart, so
    the inserted picture is uncropped and refitted inside that frame.

Determinism (SPEC 5.2): the deck is byte-identical for identical input, the
way the model and the workbook are. Both leaks are pinned - the zip entry
timestamps and the document properties, which come from the model rather than
the clock.

Exit codes: 0 ok, 2 tool/usage error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any, Sequence

# python-pptx's package is called `pptx`, and SPEC 4 names this file the same
# thing. Run as `python3 tools/render/pptx.py`, sys.path[0] is *this*
# directory, so a bare `import pptx` finds this file, imports it a second time
# under the name `pptx`, and the import inside that copy then fails against a
# half-initialised module - reported as "python-pptx is not installed", which
# is a lie. Dropping this directory from the search path first is what makes
# the import below mean the library. tests/conftest.py keeps the renderer
# directory at the *end* of sys.path for the same reason.
# The path is put back afterwards: this module is also imported by the test
# suite, and a renderer that permanently edits sys.path takes `import charts`
# down with it three test files later.
_HERE = Path(__file__).resolve().parent
_SEARCH_PATH = list(sys.path)
sys.path[:] = [entry for entry in sys.path
               if Path(entry or ".").resolve() != _HERE]
try:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Emu, Pt
except ImportError:  # pragma: no cover
    sys.exit("python-pptx is required (it is in the pipeline image)")
finally:
    sys.path[:] = _SEARCH_PATH

# A renderer has no verdict on the data, so it never exits 1.
EXIT_OK, EXIT_TOOL = 0, 2

DEFAULT_TEMPLATE = Path("templates/deck.potx")
OUTPUT_NAME = "deck.pptx"

# --------------------------------------------------------------------------
# THE mapping. Everything this file knows about the template is here.
#
# `layout` is the index in the template's layout list, `name` what that layout
# is called in PowerPoint (checked on every run, warned about if it moved) and
# the rest are placeholder `idx` values inside it. Swapping in the corporate
# template (SPEC 12.2) means editing this dict and nothing else.
# --------------------------------------------------------------------------
LAYOUTS = {
    "title":   {"layout": 0, "name": "Title Slide",
                "title": 0, "subtitle": 1},
    "content": {"layout": 1, "name": "Title and Content",
                "title": 0, "body": 1},
    "section": {"layout": 2, "name": "Section Header",
                "title": 0, "body": 1},
    "table":   {"layout": 5, "name": "Title Only",
                "title": 0},
    "picture": {"layout": 8, "name": "Picture with Caption",
                "title": 0, "picture": 1, "caption": 2},
}

# Chrome, not taxonomy: the words around the numbers. The codes and their
# labels come from the model, and the ones with no label yet stay bare codes
# rather than invented words (SPEC 12.1).
CHROME = {
    "de": {
        "as_of": "Stand", "generated": "Erzeugt", "git_sha": "Git-Commit",
        "schema_version": "Schema-Version", "pipeline": "Pipeline-Version",
        "image": "Image", "projects": "Projekte", "headline": "Kennzahlen",
        "coverage": "Abdeckung", "projects_total": "Projekte gesamt",
        "fields_tracked": "Felder erfasst", "field_coverage": "Feldabdeckung",
        "verified_coverage": "davon verifiziert", "team": "Team",
        "verified": "verifiziert", "code": "Code", "label": "Bezeichnung",
        "count": "Anzahl", "charts": "Diagramme", "tables": "Tabellen",
        "definitions": "Anhang: Zählregeln", "definition": "Zählregel",
        "grain": "Grundgesamtheit", "description": "Beschreibung",
        "na": "Keine Daten", "value": "Wert", "continued": "(Fortsetzung)",
    },
    "en": {
        "as_of": "As of", "generated": "Generated", "git_sha": "Git commit",
        "schema_version": "Schema version", "pipeline": "Pipeline version",
        "image": "Image", "projects": "Projects", "headline": "Headline",
        "coverage": "Coverage", "projects_total": "Projects total",
        "fields_tracked": "Fields tracked", "field_coverage": "Field coverage",
        "verified_coverage": "of which verified", "team": "Team",
        "verified": "Verified", "code": "Code", "label": "Label",
        "count": "Count", "charts": "Charts", "tables": "Tables",
        "definitions": "Appendix: counting rules", "definition": "Counting rule",
        "grain": "Grain", "description": "Description",
        "na": "No data", "value": "Value", "continued": "(continued)",
    },
}

# The same ink as the charts and the PDF, so a deck pasted next to either does
# not look like a different document.
INK = RGBColor(0x0B, 0x0B, 0x0B)
INK_MUTED = RGBColor(0x52, 0x51, 0x4E)

# Sizes are set only where the template has no opinion worth keeping: table
# cells, which a template cannot pre-size, and the caption, which is a text box
# on layouts that have no caption placeholder. Titles and body text keep
# whatever the template says they are.
TABLE_SIZE = Pt(11)
CAPTION_SIZE = Pt(9)

# A table taller than this is split over as many slides as it needs. Nothing
# is dropped; the continuation slides carry the same title. The appendix uses
# a smaller cap because a counting rule is a sentence, not a word.
MAX_TABLE_ROWS = 10
MAX_DEFINITION_ROWS = 5

# Fractions of the slide, so the geometry follows whatever template is loaded
# instead of assuming a slide size.
BODY_LEFT, BODY_WIDTH = 0.06, 0.88
BODY_TOP, BODY_HEIGHT = 0.28, 0.56
CAPTION_TOP, CAPTION_HEIGHT = 0.86, 0.09

PRESENTATION_TYPE = ("application/vnd.openxmlformats-officedocument"
                     ".presentationml.presentation.main+xml")
TEMPLATE_TYPE = ("application/vnd.openxmlformats-officedocument"
                 ".presentationml.template.main+xml")

# A fixed timestamp for every zip entry, as in xlsx.py. Two runs of the same
# model then produce the same bytes (SPEC 5.2).
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
EPOCH = dt.datetime(1980, 1, 1)


def t(item: dict, field: str, lang: str, default: Any = "") -> Any:
    """`field` in the requested language, falling back to the other one."""
    for candidate in (f"{field}_{lang}", f"{field}_de", f"{field}_en", field):
        value = item.get(candidate)
        if value not in (None, ""):
            return value
    return default


def n(value: Any) -> str:
    """`None` is n/a, never 0 (SPEC 5.1)."""
    return "n/a" if value is None else str(value)


def stamp(generated_at: Any) -> dt.datetime:
    """The deck's created/modified date, taken from the model, not the clock."""
    try:
        parsed = dt.datetime.fromisoformat(str(generated_at))
    except (TypeError, ValueError):
        return EPOCH
    return parsed.replace(tzinfo=None)


# --------------------------------------------------------------------------
# Template
# --------------------------------------------------------------------------

def load_template(path: Path) -> tuple[Any, list[str]]:
    """Open the template, `.potx` or `.pptx`, and say what was wrong with it.

    A PowerPoint *template* carries a different OPC content type from a
    presentation, and python-pptx checks it and refuses the file outright.
    The corporate template (SPEC 12.2) will be a genuine .potx, so rather than
    asking whoever delivers it to rename it into something it is not, the
    content type is swapped in a copy held in memory. Nothing on disk moves.
    """
    warnings: list[str] = []
    if not path.is_file():
        warnings.append(
            f"no template at {path}: the deck is built from python-pptx's own "
            f"default, which is not the corporate layout. Regenerate the "
            f"placeholder with tools/make_deck_template.py (SPEC 6.4, 12.2).")
        return Presentation(), warnings

    payload = path.read_bytes()
    try:
        return Presentation(io.BytesIO(payload)), warnings
    except ValueError:
        pass

    # A real .potx. Rewrite the one content type it differs by.
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = [(info, archive.read(info.filename))
                   for info in archive.infolist()]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, data in entries:
            if info.filename == "[Content_Types].xml":
                data = data.replace(TEMPLATE_TYPE.encode(),
                                    PRESENTATION_TYPE.encode())
            archive.writestr(info, data)
    buffer.seek(0)
    return Presentation(buffer), warnings


def check_layouts(presentation: Any) -> list[str]:
    """Do the indices in LAYOUTS still point at the layouts they are named for?

    A template swap that moves a layout produces a deck laid out on the wrong
    master - which looks plausible and is wrong, the worst pair of properties
    a report can have. So it is checked, by name, on every run.
    """
    warnings: list[str] = []
    layouts = presentation.slide_layouts
    for role, mapping in LAYOUTS.items():
        index = mapping["layout"]
        if index >= len(layouts):
            warnings.append(
                f"LAYOUTS[{role!r}] wants layout {index}, the template has "
                f"{len(layouts)}. Fix the mapping at the top of "
                f"tools/render/pptx.py.")
            continue
        actual = layouts[index].name
        if actual != mapping["name"]:
            warnings.append(
                f"LAYOUTS[{role!r}] expects layout {index} to be "
                f"{mapping['name']!r}, the template calls it {actual!r}. The "
                f"deck is being laid out on it anyway.")
        available = {ph.placeholder_format.idx for ph in layouts[index].placeholders}
        wanted = {value for key, value in mapping.items()
                  if key not in ("layout", "name")}
        missing = sorted(wanted - available)
        if missing:
            warnings.append(
                f"LAYOUTS[{role!r}]: layout {index} ({actual!r}) has no "
                f"placeholder idx {missing}; those go into a text box "
                f"instead. Available: {sorted(available)}.")
    return warnings


def add_slide(presentation: Any, role: str) -> Any:
    mapping = LAYOUTS[role]
    index = min(mapping["layout"], len(presentation.slide_layouts) - 1)
    return presentation.slides.add_slide(presentation.slide_layouts[index])


def placeholder(slide: Any, role: str, name: str) -> Any | None:
    """The placeholder this role calls `name`, by idx, or None if it is gone."""
    idx = LAYOUTS[role].get(name)
    if idx is None:
        return None
    for shape in slide.placeholders:
        if shape.placeholder_format.idx == idx:
            return shape
    return None


def drop_empty_placeholders(slide: Any) -> None:
    """Remove the placeholders nothing was written into.

    An empty placeholder is not invisible in PowerPoint: it shows its prompt
    text ("Click to add title") in the editor and leaves a hole in the layout.
    """
    for shape in list(slide.placeholders):
        if not shape.has_text_frame:
            continue
        if not shape.text_frame.text.strip():
            shape._element.getparent().remove(shape._element)


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------

def write_text(shape: Any, lines: Sequence[tuple[str, dict]]) -> None:
    """Fill a text placeholder with (text, style) paragraphs.

    Style keys are the few things a deck needs: `size`, `bold`, `muted` and
    `level` for the outline depth the template's own list formatting uses.

    A style without a `size` leaves the size alone, and that is deliberate: a
    title is whatever size the template says a title is. Setting one here
    would override the corporate template the day it arrives and make every
    heading in the deck the same height as the body text.
    """
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    for index, (text, style) in enumerate(lines):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.level = style.get("level", 0)
        run = paragraph.add_run()
        run.text = text
        if style.get("size") is not None:
            run.font.size = style["size"]
        run.font.bold = style.get("bold", False)
        run.font.color.rgb = INK_MUTED if style.get("muted") else INK


def add_caption(slide: Any, presentation: Any, text: str,
                role: str = "table") -> None:
    """The counting rule under the slide, in the caption placeholder if the
    layout has one and in a text box if it does not.

    SPEC 7: a number and the rule it was counted under never travel
    separately. A corporate template will not have a placeholder for this on
    every layout, so the fallback has to exist.
    """
    if not text:
        return
    shape = placeholder(slide, role, "caption")
    if shape is None:
        shape = slide.shapes.add_textbox(
            Emu(int(presentation.slide_width * BODY_LEFT)),
            Emu(int(presentation.slide_height * CAPTION_TOP)),
            Emu(int(presentation.slide_width * BODY_WIDTH)),
            Emu(int(presentation.slide_height * CAPTION_HEIGHT)))
    write_text(shape, [(text, {"size": CAPTION_SIZE, "muted": True})])


def set_title(slide: Any, role: str, text: str) -> None:
    shape = placeholder(slide, role, "title")
    if shape is not None:
        write_text(shape, [(text, {"bold": True})])


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------

def add_table(slide: Any, presentation: Any, headers: Sequence[str],
              rows: Sequence[Sequence[str]],
              align_right: Sequence[bool] | None = None) -> Any:
    """A table shape, sized to the slide.

    This is the one place a shape is added rather than a placeholder filled.
    A PowerPoint template has no placeholder that holds a table - the "content"
    placeholder offers a table *button*, not a table - so there is nothing to
    fill. The slide still comes from the template's own layout (SPEC 6.4).
    """
    left = Emu(int(presentation.slide_width * BODY_LEFT))
    top = Emu(int(presentation.slide_height * BODY_TOP))
    width = Emu(int(presentation.slide_width * BODY_WIDTH))
    height = Emu(int(presentation.slide_height * BODY_HEIGHT))

    shape = slide.shapes.add_table(len(rows) + 1, len(headers),
                                   left, top, width, height)
    table = shape.table
    # add_table divides the height evenly but PowerPoint grows a row to fit
    # its text, so the heights are set explicitly and the row count per slide
    # is capped below. A table that outgrows the slide is not a layout
    # problem, it is a row nobody can read.
    for row in table.rows:
        row.height = Emu(int(height / (len(rows) + 1)))
    for column, header in enumerate(headers):
        cell = table.cell(0, column)
        cell.text = str(header)
        for paragraph in cell.text_frame.paragraphs:
            for run in paragraph.runs:
                run.font.size = TABLE_SIZE
                run.font.bold = True
    for index, record in enumerate(rows, start=1):
        for column, value in enumerate(record):
            cell = table.cell(index, column)
            cell.text = "" if value is None else str(value)
            for paragraph in cell.text_frame.paragraphs:
                if align_right and column < len(align_right) and align_right[column]:
                    paragraph.alignment = PP_ALIGN.RIGHT
                for run in paragraph.runs:
                    run.font.size = TABLE_SIZE
    return table


def table_slides(presentation: Any, title: str, caption: str,
                 headers: Sequence[str], rows: Sequence[Sequence[str]],
                 lang: str, align_right: Sequence[bool] | None = None,
                 max_rows: int = MAX_TABLE_ROWS) -> int:
    """One table, over as many slides as its rows need.

    Nothing is dropped to make a table fit: a distribution that runs past the
    bottom of the slide continues on the next one, under the same title. A row
    silently missing from a deck is how a meeting ends up about the numbers.
    """
    chunks = [rows[start:start + max_rows]
              for start in range(0, max(len(rows), 1), max_rows)] or [[]]
    for index, chunk in enumerate(chunks):
        slide = add_slide(presentation, "table")
        heading = title if index == 0 else f"{title} {CHROME[lang]['continued']}"
        set_title(slide, "table", heading)
        if chunk:
            add_table(slide, presentation, headers, chunk, align_right)
        add_caption(slide, presentation, caption)
        drop_empty_placeholders(slide)
    return len(chunks)


# --------------------------------------------------------------------------
# Pictures
# --------------------------------------------------------------------------

def add_chart(slide: Any, chart: dict, path: Path) -> None:
    """The PNG charts.py rendered, uncropped, fitted inside the frame.

    `insert_picture` crops the image to fill the placeholder, which on a wide
    chart in a portrait-ish frame cuts the category labels clean off. The crop
    is undone and the picture refitted to the frame it was given, keeping the
    aspect ratio the model declares - so the chart in the deck is the chart in
    the PDF and in the Excel (SPEC 2).
    """
    frame = placeholder(slide, "picture", "picture")
    if frame is None:
        return
    left, top = frame.left, frame.top
    width, height = frame.width, frame.height

    picture = frame.insert_picture(str(path))
    picture.crop_left = picture.crop_right = 0
    picture.crop_top = picture.crop_bottom = 0

    native_w = int(chart.get("width_px") or picture.image.size[0])
    native_h = int(chart.get("height_px") or picture.image.size[1])
    scale = min(width / native_w, height / native_h)
    picture.width = Emu(int(native_w * scale))
    picture.height = Emu(int(native_h * scale))
    picture.left = Emu(int(left + (width - picture.width) / 2))
    picture.top = Emu(int(top + (height - picture.height) / 2))


def definition_text(model: dict, table: dict, lang: str) -> str:
    """The counting rule behind a table, in words (SPEC 7).

    A lookup by the key the table already named. The renderer still decides
    nothing.
    """
    key = table.get("definition", "")
    for definition in model.get("definitions") or []:
        if definition.get("key") == key:
            return (f"{CHROME[lang]['definition']}: {key} "
                    f"({definition.get('grain', '')}/{definition.get('counts', '')}) "
                    f"- {t(definition, 'text', lang, '')}")
    return str(key)


# --------------------------------------------------------------------------
# The deck
# --------------------------------------------------------------------------

def build_title(presentation: Any, model: dict, lang: str) -> None:
    words = CHROME[lang]
    provenance = model.get("provenance") or {}
    slide = add_slide(presentation, "title")
    set_title(slide, "title",
              t(model.get("report") or {}, "title", lang, "Report"))
    subtitle = placeholder(slide, "title", "subtitle")
    if subtitle is not None:
        write_text(subtitle, [
            (f"{words['as_of']}: {model.get('as_of_date', '')}", {}),
            (f"{words['generated']}: {model.get('generated_at', '')}",
             {"size": CAPTION_SIZE, "muted": True}),
            (f"{words['git_sha']}: {provenance.get('git_sha', '')}  |  "
             f"{words['schema_version']}: {provenance.get('schema_version', '')}  |  "
             f"{words['pipeline']}: {provenance.get('pipeline_version', '')}",
             {"size": CAPTION_SIZE, "muted": True}),
            (f"{words['image']}: {provenance.get('image_digest', '')}",
             {"size": CAPTION_SIZE, "muted": True}),
        ])
    drop_empty_placeholders(slide)


def build_headline(presentation: Any, model: dict, lang: str) -> None:
    """Slide 2: the numbers, each next to the rule it was counted under."""
    words = CHROME[lang]
    rows = [(t(item, "label", lang, item.get("key", "")),
             n(item.get("value")),
             str(item.get("definition", "")))
            for item in model.get("headline") or []]
    table_slides(presentation, words["headline"], "",
                 (words["label"], words["value"], words["definition"]),
                 rows, lang, align_right=(False, True, False))


def build_coverage(presentation: Any, model: dict, lang: str) -> None:
    """Raw coverage and verified coverage, always as two numbers (SPEC 11)."""
    words = CHROME[lang]
    coverage = model.get("coverage") or {}
    caption = (f"{words['projects_total']}: {n(coverage.get('projects_total'))}   |   "
               f"{words['fields_tracked']}: {n(coverage.get('fields_tracked'))}   |   "
               f"{words['field_coverage']}: {n(coverage.get('field_coverage_pct'))} %"
               f"   |   {words['verified_coverage']}: "
               f"{n(coverage.get('verified_coverage_pct'))} %")
    rows = [(team.get("team_id", ""),
             n(team.get("projects")),
             f"{n(team.get('coverage_pct'))} %",
             f"{n(team.get('verified_pct'))} %")
            for team in coverage.get("by_team") or []]
    table_slides(presentation, words["coverage"], caption,
                 (words["team"], words["projects"], words["coverage"],
                  words["verified"]),
                 rows, lang, align_right=(False, True, True, True))


def build_charts(presentation: Any, model: dict, lang: str,
                 model_dir: Path) -> list[str]:
    missing: list[str] = []
    tables = model.get("tables") or {}
    for chart in model.get("charts") or []:
        key = str(chart.get("key", ""))
        path = model_dir / str(chart.get("path", ""))
        slide = add_slide(presentation, "picture")
        set_title(slide, "picture", t(chart, "title", lang, key))
        if path.is_file():
            add_chart(slide, chart, path)
        else:
            # charts.py runs first in ./report.sh (SPEC 6.4). If it did not,
            # the slide says which file is missing rather than disappearing.
            missing.append(key)
            body = placeholder(slide, "picture", "picture")
            if body is not None and body.has_text_frame:
                write_text(body, [(f"[{path.name}]", {"muted": True})])
        table = tables.get(key) or {}
        add_caption(slide, presentation, definition_text(model, table, lang),
                    role="picture")
        drop_empty_placeholders(slide)
    return missing


def build_tables(presentation: Any, model: dict, lang: str) -> None:
    """Every table in the model, distributions and cross-tabs alike.

    A table the data cannot support yet gets its slide too, carrying the
    model's own note (SPEC 6.3): n/a, not a crash, not a zero, and not a
    silently missing slide.
    """
    words = CHROME[lang]
    tables = model.get("tables") or {}
    for key in sorted(tables):
        table = tables[key]
        title = t(table, "title", lang, key)
        caption = definition_text(model, table, lang)

        if not table.get("available", False):
            slide = add_slide(presentation, "content")
            set_title(slide, "content", title)
            body = placeholder(slide, "content", "body")
            if body is not None:
                write_text(body, [
                    (words["na"], {"bold": True}),
                    (str(t(table, "note", lang, "")), {"muted": True}),
                ])
            add_caption(slide, presentation, caption, role="content")
            drop_empty_placeholders(slide)
            continue

        columns = table.get("columns") or []
        first = columns[0]["key"] if columns else ""
        rows = table.get("rows") or []

        if table.get("kind") == "cross_tab" or len(columns) > 2:
            headers = [words["code"]] + [t(column, "label", lang, column["key"])
                                         for column in columns[1:]]
            body = [[f"{record.get(first, '')}  "
                     f"{record.get(f'{first}_label_{lang}', '') or ''}".strip()]
                    + [n(record.get(column["key"])) for column in columns[1:]]
                    for record in rows]
            align = [False] + [True] * (len(headers) - 1)
        else:
            headers = [words["code"], words["label"], words["count"]]
            body = [[str(record.get(first, "")),
                     str(record.get(f"{first}_label_{lang}", "") or ""),
                     n(record.get("value"))]
                    for record in rows]
            align = [False, False, True]

        table_slides(presentation, title, caption, headers, body, lang, align)


def build_definitions(presentation: Any, model: dict, lang: str) -> None:
    """The appendix. SPEC 7: the rules are printed alongside the numbers."""
    words = CHROME[lang]
    rows = [(definition.get("key", ""),
             f"{definition.get('grain', '')}/{definition.get('counts', '')}",
             t(definition, "text", lang, ""))
            for definition in model.get("definitions") or []]
    table_slides(presentation, words["definitions"], "",
                 (words["definition"], words["grain"], words["description"]),
                 rows, lang, max_rows=MAX_DEFINITION_ROWS)


def normalise(path: Path, modified: dt.datetime) -> None:
    """Rewrite the zip so the same model always produces the same bytes.

    Every zip entry carries the time it was written. Pinned to the model's own
    timestamp, the deck is as reproducible as report_model.json (SPEC 5.2) -
    the same rule, and the same code, as xlsx.py.
    """
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info.filename))
                   for info in archive.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, payload in entries:
            copy = zipfile.ZipInfo(info.filename, date_time=ZIP_EPOCH)
            copy.compress_type = info.compress_type
            copy.external_attr = info.external_attr
            copy.internal_attr = info.internal_attr
            copy.create_system = info.create_system
            archive.writestr(copy, payload)


def render(model: dict, out_dir: Path, lang: str, template: Path | None,
           model_dir: Path) -> tuple[Path, list[str]]:
    presentation, warnings = load_template(template)
    warnings += check_layouts(presentation)

    build_title(presentation, model, lang)
    build_headline(presentation, model, lang)
    build_coverage(presentation, model, lang)
    missing = build_charts(presentation, model, lang, model_dir)
    if missing:
        warnings.append(f"charts not rendered next to the model, those slides "
                        f"carry a placeholder: {', '.join(missing)}")
    build_tables(presentation, model, lang)
    build_definitions(presentation, model, lang)

    properties = presentation.core_properties
    properties.title = t(model.get("report") or {}, "title", lang, "")
    properties.author = "mig pipeline"
    properties.last_modified_by = "mig pipeline"
    properties.comments = (f"as_of {model.get('as_of_date', '')}, git "
                           f"{(model.get('provenance') or {}).get('git_sha', '')}")
    # No clock reading: the deck is as reproducible as the model (SPEC 5.2).
    properties.created = stamp(model.get("generated_at"))
    properties.modified = properties.created
    properties.revision = 1

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / OUTPUT_NAME
    presentation.save(out_path)
    normalise(out_path, properties.created)
    return out_path, warnings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render report_model.json to a PowerPoint deck.")
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--lang", default="de", choices=("de", "en"))
    parser.add_argument("--template", default=DEFAULT_TEMPLATE, type=Path,
                        help="deck template (default: templates/deck.potx)")
    args = parser.parse_args(argv)

    if not args.model.is_file():
        print(f"--model: {args.model} not found", file=sys.stderr)
        return EXIT_TOOL
    try:
        model = json.loads(args.model.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"--model: {args.model} is not valid JSON: {exc}", file=sys.stderr)
        return EXIT_TOOL

    try:
        out_path, warnings = render(model, args.out, args.lang, args.template,
                                    args.model.parent)
    except Exception as exc:  # pragma: no cover - surfaced as a tool error
        print(f"pptx: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_TOOL

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    slides = len(Presentation(str(out_path)).slides)
    print(f"pptx: {out_path}")
    print(f"  {slides} slides, template {args.template}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
