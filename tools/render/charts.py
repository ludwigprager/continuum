#!/usr/bin/env python3
"""
charts.py - the chart PNGs. SPEC 6.4, milestone M4.

    tools/render/charts.py --model report_model.json --out DIR [--lang de|en]

Charts are rendered once, here, and embedded by the xlsx, the pdf and the deck
(SPEC 2). Five renderers each drawing their own chart from their own query is
how five outputs end up with five different numbers.

This renderer is dumb, like every other one (SPEC 11). It draws
`model["charts"]`, each entry naming a table that is already counted, already
labelled and already ordered. It sums nothing, sorts nothing and opens no
taxonomy: even the colours were baked into the model by build_model.py, so
`unknown` is the same grey in every chart of every report.

    distribution  -> horizontal bars, one bar per code, value at the bar end
    cross_tab     -> horizontal stacked bars, one segment per column code
    not available -> a panel carrying the model's own note, at the same size

A table nobody has collected data for yet still gets its PNG. The file has to
exist: the Excel dashboard, the deck and the PDF all reference it by the path
the model gave them, and a hole in the deck is worse than a panel saying the
field is not there.

Determinism (SPEC 5.2): same model in, same bytes out. The figure size comes
from the model, the font is pinned, nothing is sorted here, and the PNG's
`Software` tag - which carries the matplotlib version - is dropped rather than
written, so an image is stable for as long as its pins are.

Air gap (SPEC 8.2): matplotlib builds a font cache on first use. It is baked
into the image at $MPLCONFIGDIR; `check_font` below fails loudly rather than
letting matplotlib substitute silently for a font that is not installed.

Exit codes: 0 ok, 2 tool/usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")  # before pyplot: there is no display and never will be.

from matplotlib import font_manager  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

# A renderer has no verdict on the data, so it never exits 1.
EXIT_OK, EXIT_TOOL = 0, 2

# Fonts, in order of preference. Both are installed explicitly in the image
# (SPEC 8.1); Liberation Sans is metric-compatible with the Arial the Excel
# uses, so a chart pasted next to a sheet does not look like a different
# document. A missing font does not error in matplotlib, it substitutes
# silently, which is why this list is asserted at startup and not just set.
FONT_STACK = ("Liberation Sans", "DejaVu Sans")

DPI = 100  # width_px / height_px in the model are pixels at this dpi.
BAR_HEIGHT = 0.52  # thin marks: the bar carries the number, not the ink

# Ink. Text never wears a series colour; the bar next to it carries identity.
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#dcdcd8"
SURFACE = "#ffffff"

# One series, one colour: colouring each bar differently would double-encode
# bar length as hue and burn the only free channel. Bars whose code carries a
# colour in the taxonomy keep theirs - that is how `unknown` stays grey.
SERIES = "#2a78d6"

# Fixed order, never cycled, for the segments of a stacked cross-tab. This is
# the validated categorical order; codes past the eighth fold into the tail
# ramp below rather than repeating a hue that a colourblind reader could not
# separate from the one it repeats.
PALETTE = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100",
           "#e87ba4", "#008300", "#4a3aa7", "#e34948")
TAIL_RAMP = ("#86b6ef", "#5598e7", "#256abf", "#184f95", "#0d366b")

# Chrome, not taxonomy. The codes and their labels come from the model; these
# are the words around them, and there is nothing to invent (SPEC 12.1).
CHROME = {
    "de": {"as_of": "Stand", "coverage": "Abdeckung",
           "verified": "davon verifiziert", "definition": "Zählregel",
           "na": "Keine Daten"},
    "en": {"as_of": "as of", "coverage": "coverage",
           "verified": "of which verified", "definition": "counting rule",
           "na": "no data"},
}

NOTE_WRAP = 150       # characters per line in the footer
NOTE_LINES = 2        # of the counting rule; the rest is in the appendix
TITLE_WRAP = 70       # characters per line in the title


def t(item: dict, field: str, lang: str, default: Any = "") -> Any:
    """`field` in the requested language, falling back to the other one."""
    for candidate in (f"{field}_{lang}", f"{field}_de", f"{field}_en", field):
        value = item.get(candidate)
        if value not in (None, ""):
            return value
    return default


# --------------------------------------------------------------------------
# Fonts: assert, do not hope
# --------------------------------------------------------------------------

def check_font(stack: Sequence[str] = FONT_STACK) -> str:
    """The first font in the stack that actually resolves to a file.

    SPEC 8.2: a missing font does not error, it silently substitutes, and the
    report then looks different inside the air gap than it did outside. So the
    lookup is done once, up front, with matplotlib's fallback switched off,
    and the answer is what the charts are drawn with.
    """
    for family in stack:
        try:
            font_manager.findfont(FontProperties(family=family),
                                  fallback_to_default=False)
        except ValueError:
            continue
        return family
    raise RuntimeError(
        f"none of the fonts {', '.join(stack)} is installed. Charts would be "
        f"drawn in a substitute font without saying so. Install them in "
        f"docker/Dockerfile.pipeline (SPEC 8.1).")


def configure(font: str) -> None:
    """Every rcParam the output depends on, set explicitly.

    Whatever a matplotlibrc on the host says must not reach the report: the
    PNG has to be a function of the model and the pins, nothing else.
    """
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": [font],
        "font.size": 13,
        "axes.titlesize": 20,
        "axes.labelsize": 13,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "text.color": INK,
        "axes.labelcolor": INK_MUTED,
        "xtick.color": INK_MUTED,
        "ytick.color": INK,
        "axes.unicode_minus": False,
        "svg.hashsalt": "mig",
    })


# --------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------

def segment_colours(codes: Sequence[str], declared: Sequence[str | None]
                    ) -> tuple[list[str], list[str]]:
    """Colours for the segments of a stacked bar, plus any warnings.

    A code the taxonomy gave a colour keeps it, wherever it sits in the order;
    the rest take the next unused categorical slot. Past the eighth slot the
    tail steps through one blue ramp instead of cycling the hues, because a
    repeated hue is indistinguishable from the one it repeats and a reader
    cannot tell which of the two a segment belongs to.
    """
    warnings: list[str] = []
    colours: list[str] = []
    slot = 0
    for code, given in zip(codes, declared):
        if given:
            colours.append(given)
            continue
        if slot < len(PALETTE):
            colours.append(PALETTE[slot])
        else:
            index = slot - len(PALETTE)
            if index == 0:
                warnings.append(
                    f"{len(codes)} codes on one stacked chart: past the "
                    f"{len(PALETTE)} categorical colours the tail is drawn in "
                    f"one ramp, which is hard to read. A distribution per code "
                    f"would say it better.")
            colours.append(TAIL_RAMP[min(index, len(TAIL_RAMP) - 1)])
        slot += 1
    return colours, warnings


# --------------------------------------------------------------------------
# Figure furniture
# --------------------------------------------------------------------------

def figure(chart: dict) -> tuple[Any, Any]:
    width = int(chart.get("width_px") or 1600)
    height = int(chart.get("height_px") or 900)
    fig, ax = plt.subplots(figsize=(width / DPI, height / DPI), dpi=DPI)
    return fig, ax


def decorate(fig: Any, ax: Any, chart: dict, table: dict, model: dict,
             lang: str) -> None:
    """Title, the counting rule, and coverage - on every chart.

    SPEC 7: put coverage next to every count, at least for the first months
    when it will be embarrassing and therefore useful. Raw and verified stay
    two numbers, never merged into one (SPEC 11). Both come from the model.
    """
    words = CHROME[lang]
    # The title sits on the figure, not on the axes, so it lines up with the
    # footer at the left margin instead of starting wherever the longest
    # category label happens to push the plot area to.
    title = "\n".join(textwrap.wrap(str(t(chart, "title", lang,
                                          chart.get("key", ""))), TITLE_WRAP))
    fig.text(0.012, 0.955, title, color=INK, fontweight="bold",
             fontsize=plt.rcParams["axes.titlesize"], va="top", ha="left")

    coverage = model.get("coverage") or {}
    # The counting rule, then coverage, each on its own lines. The rule is
    # wrapped and cut at NOTE_LINES: it is a pointer to the definitions
    # appendix, not a substitute for it, and a rule that grows must not push
    # the coverage line off the bottom of the image.
    rule = textwrap.wrap(f"{words['definition']}: "
                         f"{definition_text(model, table, lang)}", NOTE_WRAP)
    if len(rule) > NOTE_LINES:
        rule = rule[:NOTE_LINES]
        rule[-1] = rule[-1] + " \u2026"
    lines = rule + [
        f"{words['as_of']}: {model.get('as_of_date', '')}   |   "
        f"{words['coverage']}: {coverage.get('field_coverage_pct')}%   |   "
        f"{words['verified']}: {coverage.get('verified_coverage_pct')}%"]
    fig.text(0.012, 0.015, "\n".join(lines),
             fontsize=10, color=INK_MUTED, va="bottom", ha="left",
             linespacing=1.45)

    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)


def definition_text(model: dict, table: dict, lang: str) -> str:
    """The counting rule behind this chart, in words.

    Every table names the definition it was counted under (SPEC 7) and the
    model carries the definitions themselves, so this is a lookup by the key
    the table already gave - the renderer still decides nothing. A chart whose
    rule is not printed next to it is how two slides come to disagree.
    """
    key = table.get("definition", "")
    for definition in model.get("definitions") or []:
        if definition.get("key") == key:
            grain = definition.get("grain", "")
            counts = definition.get("counts", "")
            return f"{key} ({grain}/{counts}) - {t(definition, 'text', lang, '')}"
    return str(key)


def row_label(record: dict, key: str, lang: str) -> str:
    """The label if the taxonomy has one, the bare code if it does not.

    SPEC 12.1: the codes have no labels yet and inventing them is not on the
    table, so a chart axis reading `CB-3` is the honest outcome. The model
    already made that choice; this just reads it.
    """
    label = record.get(f"{key}_label_{lang}")
    code = str(record.get(key, ""))
    if not label or str(label) == code:
        return code
    return f"{label}\n{code}"


# --------------------------------------------------------------------------
# The three shapes
# --------------------------------------------------------------------------

def draw_distribution(fig: Any, ax: Any, table: dict, lang: str) -> list[str]:
    key = table["columns"][0]["key"]
    rows = list(table.get("rows") or [])
    labels = [row_label(record, key, lang) for record in rows]
    values = [record.get("value", 0) for record in rows]
    colours = [record.get(f"{key}_colour") or SERIES for record in rows]

    positions = range(len(rows))
    ax.barh(list(positions), values, color=colours, height=BAR_HEIGHT)
    ax.set_yticks(list(positions), labels)
    ax.invert_yaxis()  # the model's order, read top to bottom

    # The value at the end of every bar, so the x axis is not needed at all.
    span = max(values) if values else 0
    for position, value in zip(positions, values):
        ax.text(value + (span or 1) * 0.012, position, str(value),
                va="center", ha="left", color=INK, fontsize=12)

    ax.set_xlim(0, (span or 1) * 1.12)
    ax.get_xaxis().set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_color(GRID)
    return []


def draw_cross_tab(fig: Any, ax: Any, table: dict, lang: str) -> list[str]:
    columns = table.get("columns") or []
    rows = list(table.get("rows") or [])
    row_key = columns[0]["key"]
    # Every column but the row label and the total the model already added.
    segments = [c for c in columns[1:] if c["key"] != "total"]
    codes = [c["key"] for c in segments]
    colours, warnings = segment_colours(
        codes, [c.get("colour") for c in segments])

    labels = [row_label(record, row_key, lang) for record in rows]
    positions = list(range(len(rows)))
    offsets = [0] * len(rows)
    for code, colour, column in zip(codes, colours, segments):
        values = [record.get(code, 0) for record in rows]
        ax.barh(positions, values, left=offsets, height=BAR_HEIGHT, color=colour,
                label=t(column, "label", lang, code),
                edgecolor=SURFACE, linewidth=2)  # 2px gap between segments
        offsets = [a + b for a, b in zip(offsets, values)]

    ax.set_yticks(positions, labels)
    ax.invert_yaxis()
    # The row total the model computed, at the end of each bar.
    span = max([record.get("total", 0) for record in rows] or [0])
    for position, record in zip(positions, rows):
        ax.text(record.get("total", 0) + (span or 1) * 0.012, position,
                str(record.get("total", 0)), va="center", ha="left",
                color=INK, fontsize=12)
    ax.set_xlim(0, (span or 1) * 1.12)
    ax.get_xaxis().set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_color(GRID)
    # More than one series, so identity is never colour alone.
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.0), ncols=len(codes),
              frameon=False, fontsize=11, handlelength=1.2)
    return warnings


def draw_unavailable(fig: Any, ax: Any, table: dict, lang: str) -> list[str]:
    """A field named in the report spec that the data does not have yet.

    n/a, not a crash and not a zero (SPEC 6.3). The note is the model's; it
    names the field so the reader knows what is missing rather than wondering
    whether the number is really nought.
    """
    ax.axis("off")
    ax.text(0.5, 0.55, CHROME[lang]["na"], ha="center", va="center",
            fontsize=30, color=INK_MUTED)
    ax.text(0.5, 0.42, str(t(table, "note", lang, "")), ha="center",
            va="center", fontsize=14, color=INK_MUTED)
    return []


def draw(fig: Any, ax: Any, table: dict, lang: str) -> list[str]:
    if not table.get("available", False):
        return draw_unavailable(fig, ax, table, lang)
    if table.get("kind") == "cross_tab" or len(table.get("columns") or []) > 2:
        return draw_cross_tab(fig, ax, table, lang)
    return draw_distribution(fig, ax, table, lang)


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def render(model: dict, out_dir: Path, lang: str) -> tuple[list[Path], list[str]]:
    configure(check_font())
    out_dir.mkdir(parents=True, exist_ok=True)

    tables = model.get("tables") or {}
    written: list[Path] = []
    warnings: list[str] = []

    for chart in model.get("charts") or []:
        key = str(chart.get("key", ""))
        table = tables.get(key)
        if table is None:
            # build_model.py refuses to emit this, so reaching it means the
            # model was hand-edited. Say so; do not guess what was meant.
            warnings.append(f"chart {key!r} names no table in the model, skipped")
            continue

        fig, ax = figure(chart)
        try:
            warnings += [f"{key}: {w}" for w in draw(fig, ax, table, lang)]
            decorate(fig, ax, chart, table, model, lang)
            fig.subplots_adjust(left=0.20, right=0.97, top=0.88, bottom=0.13)
            path = out_dir / str(chart.get("path") or f"{key}.png")
            # `Software` carries the matplotlib version into the file and would
            # change the bytes on an upgrade the pins already govern. Dropped,
            # so the PNG is a function of the model alone (SPEC 5.2).
            fig.savefig(path, format="png", dpi=DPI,
                        metadata={"Software": None})
            written.append(path)
        finally:
            plt.close(fig)

    return written, warnings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render the charts named in report_model.json as PNG.")
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--lang", default="de", choices=("de", "en"))
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
        written, warnings = render(model, args.out, args.lang)
    except Exception as exc:  # pragma: no cover - surfaced as a tool error
        print(f"charts: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_TOOL

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"charts: {len(written)} png in {args.out}")
    for path in written:
        print(f"  {path.name}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
