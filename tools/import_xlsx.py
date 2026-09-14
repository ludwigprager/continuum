#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
legacy-xlsx-import
==================

Turns a legacy "one row per project" spreadsheet into one YAML file per project,
without requiring the target schema to be known up front.

Three subcommands, run in this order:

    profile        Read the sheet, infer per-column types, and write
                   profile.md / profile.json plus starter mapping.yaml and
                   value_map.yaml files for you to edit.

    convert        Apply mapping.yaml + value_map.yaml and write
                   projects/<group>/<id>.yaml, plus import-report.md.

    derive-schema  Emit a JSON Schema draft built from what was actually
                   found in the data (types + enum candidates).

Design rules:
  * Nothing is ever silently dropped. Unmapped columns land in `_unmapped`,
    unparseable values are kept verbatim and listed in the report.
  * Output is deterministic, so re-running after the sheet changes produces a
    clean git diff.
  * Project ids are stable across runs via id_map.csv.
  * Only two dependencies: openpyxl and PyYAML.

Author: first draft, expected to be edited.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import difflib
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required:  pip install PyYAML")


# --------------------------------------------------------------------------
# 1. Constants / tunables
# --------------------------------------------------------------------------

HEADER_SCAN_ROWS = 15           # how many top rows to consider when guessing the header
LIST_SEPARATORS = [";", "|", "\n", ",", "/"]
LIST_MIN_SHARE = 0.15           # >=15% of filled cells must contain the separator
LIST_MAX_TOKEN_LEN = 40         # longer tokens => probably free text, not a list
LIST_MAX_DISTINCT = 60          # more distinct tokens => probably free text
ENUM_MAX_DISTINCT = 40          # at most this many distinct values to call it an enum
ENUM_MAX_LEN = 60               # enum values should be short
FREETEXT_MIN_LEN = 80           # avg length above this => free text column
SIMILARITY_THRESHOLD = 0.86     # difflib ratio for "did you mean" merge hints

EMPTY_MARKERS = {
    "", "-", "--", "---", "/", "n/a", "na", "n.a.", "k.a.", "k. a.", "keine angabe",
    "unbekannt", "unknown", "tbd", "offen", "?", "??", "???", "none", "null", "nil",
    "entfällt", "entfaellt", "leer", "x.x", ".",
}

# Values that mean "nothing here" but survive clean_cell because they are
# meaningful words in some columns. Proposed as null in value_map.yaml.
NOT_SET_MARKERS = {
    "keine", "kein", "keins", "nichts", "entfaellt", "unbekannt", "unklar",
    "offen", "nochoffen", "tbd", "nichtbekannt", "nichtzutreffend", "na", "none",
}

BOOLISH_TRUE = {"ja", "yes", "y", "j", "true", "wahr", "x", "1", "vorhanden"}
BOOLISH_FALSE = {"nein", "no", "n", "false", "falsch", "0", "nicht vorhanden"}

UMLAUTS = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    "à": "a", "á": "a", "â": "a", "è": "e", "é": "e", "ê": "e",
    "ì": "i", "í": "i", "î": "i", "ò": "o", "ó": "o", "ô": "o",
    "ù": "u", "ú": "u", "û": "u", "ç": "c", "ñ": "n",
}

# Units we recognise in headers like "Storage (GB)" or "Speicher in GB"
UNIT_PATTERN = re.compile(
    r"(?:\(|\[|\bin\s+)\s*"
    r"(gb|tb|mb|kb|gib|tib|mib|stk|stück|stueck|cores?|kerne?|vcpu|cpus?|"
    r"eur|€|chf|usd|\$|%|h|std|stunden|min|minuten|tage?|days?|jahre?|years?)"
    r"\s*(?:\)|\]|$)",
    re.IGNORECASE,
)

# Trailing unit inside the value itself, e.g. "500 GB"
VALUE_UNIT_PATTERN = re.compile(r"^\s*([\d.,\s]+)\s*([A-Za-z€$%]{1,6})\s*$")

DE_NUMBER = re.compile(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$")
DE_DECIMAL = re.compile(r"^-?\d+,\d+$")
PLAIN_INT = re.compile(r"^-?\d+$")
PLAIN_FLOAT = re.compile(r"^-?\d+\.\d+$")

# "2.500" is 2500 in a German sheet and 2.5 in an English one, and no amount of
# staring at a single cell resolves it. Decide per column, and say so out loud
# when the column gives no evidence either way.
DE_THOUSANDS_MULTI = re.compile(r"^-?\d{1,3}(\.\d{3}){2,}$")      # 1.234.567 -> definitely de
DE_THOUSANDS_ONE = re.compile(r"^-?\d{1,3}\.\d{3}$")              # 2.500     -> ambiguous
EN_DECIMAL_CLEAR = re.compile(r"^-?\d+\.(\d{1,2}|\d{4,})$")       # 2.5, 2.5000 -> definitely en

DATE_PATTERNS = [
    ("%d.%m.%Y", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")),
    ("%d.%m.%y", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2}$")),
    ("%Y-%m-%d", re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")),
    ("%d/%m/%Y", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")),
    ("%m/%d/%Y", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")),
]

# Headers that look like they identify the project
ID_HINTS = re.compile(
    r"\b(id|nr|nummer|projekt|project|anwendung|application|system|name|"
    r"bezeichnung|titel|title|app|service|kürzel|kuerzel|akronym)\b",
    re.IGNORECASE,
)
GROUP_HINTS = re.compile(
    r"\b(team|abteilung|bereich|organisation|org|owner|verantwortlich|"
    r"fachbereich|gruppe|unit|domain)\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# 2. Text helpers
# --------------------------------------------------------------------------

def transliterate(text: str) -> str:
    out = []
    for ch in text:
        if ch in UMLAUTS:
            out.append(UMLAUTS[ch])
        else:
            out.append(ch)
    text = "".join(out)
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def slugify(text: str, sep: str = "_") -> str:
    """Lowercase ascii slug. German umlauts are expanded, not stripped."""
    text = transliterate(str(text)).lower()
    text = re.sub(r"[^a-z0-9]+", sep, text)
    return text.strip(sep) or "feld"


def normalise_key(value: Any) -> str:
    """Aggressive normalisation used to cluster spelling variants of one value."""
    s = transliterate(str(value)).lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in EMPTY_MARKERS
    return False


def clean_cell(value: Any) -> Any:
    """Trim strings, collapse inner whitespace, map empty markers to None."""
    if value is None:
        return None
    if isinstance(value, str):
        s = re.sub(r"[\u00a0\t ]+", " ", value).strip()
        s = re.sub(r"\n{2,}", "\n", s)
        if s.lower() in EMPTY_MARKERS:
            return None
        return s
    if isinstance(value, float) and value != value:  # NaN
        return None
    return value


def dedent_header(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


# --------------------------------------------------------------------------
# 3. Scalar parsing
# --------------------------------------------------------------------------

def parse_german_number(s: str) -> float | int | None:
    if DE_NUMBER.match(s):
        s2 = s.replace(".", "").replace(",", ".")
    elif DE_DECIMAL.match(s):
        s2 = s.replace(",", ".")
    else:
        return None
    try:
        f = float(s2)
    except ValueError:
        return None
    return int(f) if f.is_integer() else f


def detect_number_locale(cells: Iterable[Any]) -> str:
    """Return 'de', 'en' or 'ambiguous' for a column's dot/comma convention."""
    de_evidence = en_evidence = ambiguous = False
    for c in cells:
        if not isinstance(c, str):
            continue
        s = c.strip()
        m = VALUE_UNIT_PATTERN.match(s)
        if m:
            s = m.group(1).replace(" ", "")
        if DE_DECIMAL.match(s) or DE_THOUSANDS_MULTI.match(s):
            de_evidence = True
        elif EN_DECIMAL_CLEAR.match(s):
            en_evidence = True
        elif DE_THOUSANDS_ONE.match(s):
            ambiguous = True
    if de_evidence and not en_evidence:
        return "de"
    if en_evidence and not de_evidence:
        return "en"
    if de_evidence and en_evidence:
        return "ambiguous"
    return "ambiguous" if ambiguous else "en"


def parse_number(value: Any, locale: str = "en") -> tuple[Any, str | None]:
    """Return (number_or_None, unit_or_None). `locale` decides what '2.500' means."""
    if isinstance(value, bool):
        return None, None
    if isinstance(value, (int, float)):
        return value, None
    if not isinstance(value, str):
        return None, None

    s = value.strip()
    unit = None
    m = VALUE_UNIT_PATTERN.match(s)
    if m:
        s = m.group(1).replace(" ", "")
        unit = m.group(2)

    if PLAIN_INT.match(s):
        return int(s), unit
    if DE_DECIMAL.match(s) or DE_NUMBER.match(s):
        return parse_german_number(s), unit
    if PLAIN_FLOAT.match(s):
        if locale != "en" and DE_THOUSANDS_ONE.match(s):
            return int(s.replace(".", "")), unit
        f = float(s)
        return (int(f) if f.is_integer() else f), unit
    return None, None


def parse_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    for fmt, rx in DATE_PATTERNS:
        if rx.match(s):
            try:
                return dt.datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    return None


def parse_boolish(value: Any) -> str | None:
    """Returns 'yes'/'no' as *strings*; never YAML booleans (the Norway problem)."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if not isinstance(value, str):
        return None
    s = transliterate(value).strip().lower()
    if s in BOOLISH_TRUE:
        return "yes"
    if s in BOOLISH_FALSE:
        return "no"
    return None


# --------------------------------------------------------------------------
# 4. Sheet loading
# --------------------------------------------------------------------------

def load_rows_xlsx(path: Path, sheet: str | None) -> tuple[str, list[list[Any]]]:
    from openpyxl import load_workbook

    # data_only=True gives the values Excel cached for formula cells. If the file
    # was last written by a tool that does not cache (e.g. openpyxl), those cells
    # read as None -- open and re-save it in Excel/LibreOffice first.
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    name = ws.title
    wb.close()
    return name, rows


def load_rows_csv(path: Path, encoding: str | None) -> tuple[str, list[list[Any]]]:
    encodings = [encoding] if encoding else ["utf-8-sig", "cp1252", "latin-1"]
    last_err: Exception | None = None
    for enc in encodings:
        try:
            raw = path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, LookupError) as exc:
            last_err = exc
    else:
        raise SystemExit(f"could not decode {path}: {last_err}")

    sample = raw[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if sample.count(";") > sample.count(",") else ","
    rows = [list(r) for r in csv.reader(io.StringIO(raw), dialect)]
    return path.stem, rows


def load_rows(path: Path, sheet: str | None, encoding: str | None) -> tuple[str, list[list[Any]]]:
    if path.suffix.lower() in {".csv", ".tsv", ".txt"}:
        return load_rows_csv(path, encoding)
    return load_rows_xlsx(path, sheet)


def score_header_row(row: Sequence[Any]) -> float:
    cells = [dedent_header(c) for c in row]
    filled = [c for c in cells if c]
    if len(filled) < 2:
        return 0.0
    strings = [c for c in filled if not re.match(r"^-?[\d.,]+$", c)]
    uniqueness = len(set(filled)) / len(filled)
    shortish = sum(1 for c in filled if len(c) <= 60) / len(filled)
    return len(filled) * (len(strings) / len(filled)) * uniqueness * shortish


def detect_header_row(rows: list[list[Any]]) -> int:
    best_idx, best_score = 0, -1.0
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        s = score_header_row(row)
        if s > best_score:
            best_idx, best_score = i, s
    return best_idx


def build_headers(rows: list[list[Any]], header_row: int, header_rows: int) -> list[str]:
    """Join multi-row headers, forward-filling merged cells on the upper rows."""
    block = rows[header_row: header_row + header_rows]
    width = max((len(r) for r in block), default=0)
    parts: list[list[str]] = []
    for r in block:
        vals = [dedent_header(r[i]) if i < len(r) else "" for i in range(width)]
        filled, last = [], ""
        for v in vals:
            if v:
                last = v
            filled.append(last if len(block) > 1 else v)
        parts.append(filled)

    headers = []
    for i in range(width):
        pieces, seen = [], set()
        for p in parts:
            v = p[i]
            if v and v not in seen:
                pieces.append(v)
                seen.add(v)
        headers.append(" / ".join(pieces))

    # Disambiguate blank and duplicate headers so mapping keys stay unique.
    out, counts = [], Counter()
    for i, h in enumerate(headers):
        h = h or f"spalte_{i + 1}"
        counts[h] += 1
        out.append(h if counts[h] == 1 else f"{h} ({counts[h]})")
    return out


# --------------------------------------------------------------------------
# 5. Column profiling
# --------------------------------------------------------------------------

@dataclass
class ColumnProfile:
    index: int
    header: str
    field: str                      # proposed field name
    filled: int = 0
    total: int = 0
    distinct: int = 0
    kind: str = "string"            # string|integer|number|date|enum|list|boolish|freetext
    unit: str | None = None
    number_locale: str = "en"       # de|en|ambiguous - what '2.500' means in this column
    list_separator: str | None = None
    values: list[tuple[str, int]] = field(default_factory=list)   # value, count
    clusters: dict[str, list[str]] = field(default_factory=dict)  # canonical -> variants
    hints: list[str] = field(default_factory=list)

    @property
    def fill_rate(self) -> float:
        return (self.filled / self.total) if self.total else 0.0


def detect_list_separator(cells: list[Any]) -> str | None:
    strings = [c for c in cells if isinstance(c, str)]
    if not strings:
        return None
    for sep in LIST_SEPARATORS:
        share = sum(1 for s in strings if sep in s) / len(strings)
        if share < LIST_MIN_SHARE:
            continue
        tokens: list[str] = []
        for s in strings:
            tokens.extend(t.strip() for t in s.split(sep) if t.strip())
        if not tokens:
            continue
        if max(len(t) for t in tokens) > LIST_MAX_TOKEN_LEN:
            continue
        if len(set(tokens)) > LIST_MAX_DISTINCT:
            continue
        # A separator that only ever yields one token is not a separator.
        if len(tokens) <= len(strings):
            continue
        return sep
    return None


def detect_scalar_kind(cells: list[Any]) -> str | None:
    """'date' or 'numeric' if every filled cell parses as one; otherwise None."""
    if not cells:
        return None
    if all(parse_date(c) is not None for c in cells):
        return "date"
    locale = detect_number_locale(cells)
    if all(parse_number(c, locale)[0] is not None for c in cells):
        return "numeric"
    return None


def cluster_values(values: Iterable[str]) -> dict[str, list[str]]:
    """Group spelling variants: 'MS-SQL', 'MSSQL', 'ms sql' -> one cluster."""
    buckets: dict[str, list[str]] = defaultdict(list)
    for v in values:
        buckets[normalise_key(v)].append(v)
    return {k: sorted(set(v)) for k, v in buckets.items() if k}


def similarity_hints(keys: Sequence[str]) -> list[tuple[str, str]]:
    hints = []
    keys = list(keys)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if abs(len(a) - len(b)) > 6:
                continue
            if difflib.SequenceMatcher(None, a, b).ratio() >= SIMILARITY_THRESHOLD:
                hints.append((a, b))
    return hints


UNIT_SUFFIX = {
    "gb": ("gb", ["gb", "gib", "gbyte"]),
    "gib": ("gb", ["gb", "gib"]),
    "tb": ("tb", ["tb", "tib"]),
    "tib": ("tb", ["tb", "tib"]),
    "mb": ("mb", ["mb", "mib"]),
    "cores": ("cores", ["core", "cores", "kern", "kerne", "cpu", "cpus", "vcpu"]),
    "core": ("cores", ["core", "cores", "kern", "kerne", "cpu", "cpus", "vcpu"]),
    "kerne": ("cores", ["core", "cores", "kern", "kerne", "cpu", "cpus", "vcpu"]),
    "kern": ("cores", ["core", "cores", "kern", "kerne", "cpu", "cpus", "vcpu"]),
    "vcpu": ("cores", ["core", "cores", "kern", "kerne", "cpu", "cpus", "vcpu"]),
    "cpu": ("cores", ["core", "cores", "kern", "kerne", "cpu", "cpus", "vcpu"]),
    "cpus": ("cores", ["core", "cores", "kern", "kerne", "cpu", "cpus", "vcpu"]),
    "h": ("hours", ["h", "hours", "std", "stunden"]),
    "std": ("hours", ["h", "hours", "std", "stunden"]),
    "stunden": ("hours", ["h", "hours", "std", "stunden"]),
    "min": ("minutes", ["min", "minutes", "minuten"]),
    "minuten": ("minutes", ["min", "minutes", "minuten"]),
    "%": ("pct", ["pct", "prozent", "percent"]),
    "eur": ("eur", ["eur", "euro"]),
    "€": ("eur", ["eur", "euro"]),
}


def apply_unit_suffix(prof: ColumnProfile) -> None:
    """Append the unit to the field name, unless it is already spelled there."""
    if not prof.unit:
        return
    entry = UNIT_SUFFIX.get(prof.unit.lower())
    if not entry:
        return
    suffix, aliases = entry
    parts = prof.field.split("_")
    if any(a in parts for a in aliases):
        return
    prof.field = f"{prof.field}_{suffix}"
    prof.hints.append(f"field renamed to carry the unit: {prof.field}")


def profile_column(index: int, header: str, cells: list[Any]) -> ColumnProfile:
    prof = ColumnProfile(index=index, header=header, field=slugify(header))
    prof.total = len(cells)
    cleaned = [clean_cell(c) for c in cells]
    filled = [c for c in cleaned if not is_empty(c)]
    prof.filled = len(filled)
    if not filled:
        prof.kind = "empty"
        prof.hints.append("column is completely empty in this sheet")
        return prof

    m = UNIT_PATTERN.search(header)
    if m:
        prof.unit = m.group(1).lower()

    # Numbers and dates are never multi-value: a German decimal comma ("1.200,5")
    # and a date slash ("01/02/2024") must not be read as list separators.
    sep = None if detect_scalar_kind(filled) else detect_list_separator(filled)
    tokens: list[Any]
    if sep:
        prof.list_separator = sep
        prof.kind = "list"
        others = [s for s in LIST_SEPARATORS
                  if s != sep and sum(1 for c in filled if isinstance(c, str) and s in c)]
        if others:
            shown = ", ".join(repr(o.replace("\n", "\\n")) for o in others)
            prof.hints.append(
                f"other separator(s) also appear in this column: {shown}. "
                f"Those cells are NOT split - map them explicitly in value_map.yaml, "
                f"e.g. \"MSSQL/Redis\": [mssql, redis]")
        tokens = []
        for c in filled:
            if isinstance(c, str):
                tokens.extend(t.strip() for t in c.split(sep) if t.strip())
            else:
                tokens.append(c)
    else:
        tokens = filled

    str_tokens = [str(t) for t in tokens]
    prof.distinct = len(set(str_tokens))
    prof.values = Counter(str_tokens).most_common(ENUM_MAX_DISTINCT + 1)

    if not sep:
        dates = [parse_date(c) for c in filled]
        if all(d is not None for d in dates):
            prof.kind = "date"
            return prof

        booly = [parse_boolish(c) for c in filled]
        if all(b is not None for b in booly) and prof.distinct <= 4:
            prof.kind = "boolish"
            prof.hints.append(
                "looks like ja/nein - consider mapping to full/partial/none in value_map.yaml"
            )
            return prof

        prof.number_locale = detect_number_locale(filled)
        nums = [parse_number(c, prof.number_locale) for c in filled]
        if all(n[0] is not None for n in nums):
            units = {n[1].lower() for n in nums if n[1]}
            if len(units) == 1:
                prof.unit = prof.unit or units.pop()
                prof.hints.append(f"unit '{prof.unit}' stripped from the values")
            elif len(units) > 1:
                prof.hints.append(f"mixed units in cells: {sorted(units)} - check these")
            if prof.number_locale == "ambiguous":
                sample = next((str(c) for c in filled
                               if isinstance(c, str) and DE_THOUSANDS_ONE.match(c.strip())), "")
                prof.hints.append(
                    f"AMBIGUOUS decimal separator: '{sample}' is "
                    f"{sample.replace('.', '')} in a German sheet and "
                    f"{sample} in an English one. Currently read as German. "
                    f"Override with --decimal en if that is wrong.")
            prof.kind = "integer" if all(isinstance(n[0], int) for n in nums) else "number"
            apply_unit_suffix(prof)
            return prof

        avg_len = sum(len(s) for s in str_tokens) / len(str_tokens)
        if avg_len >= FREETEXT_MIN_LEN or prof.distinct > ENUM_MAX_DISTINCT:
            longest = max(len(s) for s in str_tokens)
            if longest > ENUM_MAX_LEN and prof.distinct > ENUM_MAX_DISTINCT:
                prof.kind = "freetext"
                return prof

    if prof.distinct <= ENUM_MAX_DISTINCT and all(len(s) <= ENUM_MAX_LEN for s in str_tokens):
        if prof.kind != "list":
            prof.kind = "enum"
        prof.clusters = cluster_values(str_tokens)
        merges = [c for c in prof.clusters.values() if len(c) > 1]
        if merges:
            prof.hints.append(f"{len(merges)} value(s) appear in several spellings")
        for a, b in similarity_hints(list(prof.clusters.keys())):
            va = prof.clusters[a][0]
            vb = prof.clusters[b][0]
            prof.hints.append(f"possibly the same: '{va}' / '{vb}'")
    elif prof.kind != "list":
        prof.kind = "string"
    return prof


def profile_sheet(headers: list[str], data_rows: list[list[Any]]) -> list[ColumnProfile]:
    profiles = []
    for i, header in enumerate(headers):
        column = [r[i] if i < len(r) else None for r in data_rows]
        profiles.append(profile_column(i, header, column))
    return profiles


# --------------------------------------------------------------------------
# 6. Starter mapping / value_map generation  (written as text, to keep comments)
# --------------------------------------------------------------------------

def propose_target(prof: ColumnProfile) -> str:
    return prof.field


def render_mapping_yaml(profiles: list[ColumnProfile], id_col: str | None,
                        group_col: str | None) -> str:
    lines = [
        "# mapping.yaml - one entry per spreadsheet column.",
        "#",
        "#   \"<exact column header>\": <target path in the project YAML>",
        "#",
        "# Target path syntax:",
        "#   a.b.c          nested scalar          -> a: {b: {c: value}}",
        "#   a.b[]          list of scalars        -> a: {b: [v1, v2]}",
        "#   a.b[].c        list of objects        -> a: {b: [{c: v1}, {c: v2}]}",
        "#   a.b[0].c       explicit list index",
        "#   null           leave in _unmapped (the default for anything you delete)",
        "#",
        "# Columns you do not map are NOT lost: they are written to _unmapped",
        "# in each project file, so you can promote them later.",
        "#",
        f"# Generated {dt.date.today().isoformat()} - edit freely, this file is the spec.",
        "",
    ]
    if id_col:
        lines += [f"# id column (detected): {id_col}", ""]
    if group_col:
        lines += [f"# group column (detected): {group_col}", ""]

    lines.append("columns:")
    for prof in profiles:
        note = f"kind={prof.kind}"
        if prof.unit:
            note += f" unit={prof.unit}"
        if prof.list_separator:
            sep = prof.list_separator.replace("\n", "\\n")
            note += f" sep='{sep}'"
        note += f" fill={prof.fill_rate * 100:.0f}% distinct={prof.distinct}"
        lines.append(f"  # {note}")
        if prof.kind == "empty":
            lines.append(f"  {yaml_key(prof.header)}: null    # empty in this export")
            continue
        lines.append(f"  {yaml_key(prof.header)}: {propose_target(prof)}")
    lines.append("")
    return "\n".join(lines)


def render_value_map_yaml(profiles: list[ColumnProfile]) -> str:
    lines = [
        "# value_map.yaml - normalise the messy values found in the sheet.",
        "#",
        "# Keyed by the TARGET path from mapping.yaml (not the column header),",
        "# so renaming a column header does not invalidate this file.",
        "#",
        "#   target.path:",
        "#     \"MS-SQL\": mssql        # rename",
        "#     \"mssql/db2\": [mssql, db2]   # split one cell into several values",
        "#     \"keine\": null          # treat as not-set",
        "#",
        "# Values not listed here are passed through unchanged (and counted in",
        "# import-report.md so you can see what you have not normalised yet).",
        "",
        "values:",
    ]
    any_col = False
    skipped: list[str] = []
    for prof in profiles:
        if prof.kind not in {"enum", "list", "boolish"} or not prof.values:
            continue
        # A column where almost every row has its own value is an identifier or
        # free text, not a code list. Generating 800 entries for it is noise.
        if prof.kind != "list" and prof.filled > 5 and prof.distinct > 0.5 * prof.filled:
            skipped.append(f"{prof.header} ({prof.distinct} distinct / {prof.filled} filled)")
            continue
        if prof.distinct > 25:
            skipped.append(f"{prof.header} ({prof.distinct} distinct)")
            continue
        any_col = True
        lines.append(f"  # --- {prof.header}  ({prof.distinct} distinct) ---")
        for hint in prof.hints:
            lines.append(f"  # {hint}")
        lines.append(f"  {propose_target(prof)}:")
        counts = dict(prof.values)
        for value, count in sorted(prof.values, key=lambda x: (-x[1], x[0])):
            cluster = prof.clusters.get(normalise_key(value), [value])
            # Collapse spelling variants onto the most frequent one automatically:
            # 'MS-SQL' and 'mssql' both become whichever of them occurs most often.
            canonical = max(cluster, key=lambda v: (counts.get(v, 0), -len(v)))
            suffix = f"   # {count}x"
            if len(cluster) > 1:
                others = [c for c in cluster if c != value]
                suffix += f", also spelled: {', '.join(repr(o) for o in others)}"
            if normalise_key(value) in NOT_SET_MARKERS:
                lines.append(f"    {yaml_key(value)}: null{suffix}, treated as not set")
            else:
                lines.append(f"    {yaml_key(value)}: {slugify(canonical, '_')}{suffix}")
        lines.append("")
    if not any_col:
        lines.append("  {}")
    if skipped:
        lines += ["", "# Not listed above (too many distinct values to be a code list -",
                  "# these look like identifiers or free text). Add a block by hand if",
                  "# one of them really is an enum:"]
        for s in skipped:
            lines.append(f"#   - {s}")
    return "\n".join(lines)


def yaml_key(text: str) -> str:
    """Always quote keys: headers contain colons, umlauts and stray punctuation."""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


# --------------------------------------------------------------------------
# 7. Report rendering
# --------------------------------------------------------------------------

def render_profile_md(source: Path, sheet: str, header_row: int, n_rows: int,
                      profiles: list[ColumnProfile]) -> str:
    out = [
        f"# Column profile: {source.name}",
        "",
        f"- sheet: `{sheet}`",
        f"- header row: {header_row + 1}",
        f"- data rows: {n_rows}",
        f"- columns: {len(profiles)}",
        f"- generated: {dt.datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Overview",
        "",
        "| # | Column | Proposed field | Kind | Fill | Distinct |",
        "|---|--------|----------------|------|------|----------|",
    ]
    for p in profiles:
        out.append(
            f"| {p.index + 1} | {p.header} | `{p.field}` | {p.kind} | "
            f"{p.fill_rate * 100:.0f}% | {p.distinct} |"
        )
    out += ["", "## Columns in detail", ""]
    for p in profiles:
        out.append(f"### {p.index + 1}. {p.header}")
        out.append("")
        out.append(f"- proposed field: `{p.field}`")
        out.append(f"- kind: {p.kind}" + (f" (unit: {p.unit})" if p.unit else ""))
        if p.list_separator:
            out.append(f"- multi-value separator: `{p.list_separator.strip() or 'newline'}`")
        out.append(f"- filled: {p.filled}/{p.total} ({p.fill_rate * 100:.0f}%)")
        out.append(f"- distinct values: {p.distinct}")
        for h in p.hints:
            out.append(f"- **note:** {h}")
        if p.values and p.kind in {"enum", "list", "boolish", "integer", "number", "date"}:
            out.append("")
            shown = sorted(p.values, key=lambda x: (-x[1], x[0]))[:ENUM_MAX_DISTINCT]
            for value, count in shown:
                out.append(f"  - `{value}` x{count}")
            if p.distinct > len(shown):
                out.append(f"  - ... {p.distinct - len(shown)} more")
        elif p.values:
            out.append("")
            for value, _ in p.values[:3]:
                snippet = value if len(value) <= 100 else value[:100] + "..."
                out.append(f"  - example: `{snippet}`")
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# 8. Path setting
# --------------------------------------------------------------------------

PATH_TOKEN = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)(\[(\d*)\])?$")


def parse_path(path: str) -> list[tuple[str, Any]]:
    tokens: list[tuple[str, Any]] = []
    for raw in path.split("."):
        m = PATH_TOKEN.match(raw.strip())
        if not m:
            raise ValueError(f"invalid path segment {raw!r} in {path!r}")
        tokens.append(("key", m.group(1)))
        if m.group(2) is not None:
            idx = m.group(3)
            tokens.append(("index", int(idx)) if idx != "" else ("list", None))
    return tokens


def list_prefix(path: str) -> str | None:
    """The part of the path up to and including the first '[]' (list-of-objects marker)."""
    i = path.find("[]")
    if i == -1:
        return None
    return path[: i + 2]


def set_path(root: dict, tokens: list[tuple[str, Any]], value: Any, index: int = 0) -> None:
    node: Any = root
    i = 0
    while i < len(tokens):
        kind, name = tokens[i]
        last = i == len(tokens) - 1
        if kind == "key":
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if last:
                node[name] = value
                return
            if nxt and nxt[0] in {"list", "index"}:
                node.setdefault(name, [])
                node = node[name]
                i += 1
                _, idx = tokens[i]
                pos = index if idx is None else idx
                while len(node) <= pos:
                    node.append({})
                if i == len(tokens) - 1:
                    node[pos] = value
                    return
                node = node[pos]
            else:
                node = node.setdefault(name, {})
        i += 1


# --------------------------------------------------------------------------
# 9. Conversion
# --------------------------------------------------------------------------

@dataclass
class ConvertStats:
    written: int = 0
    unchanged: int = 0
    changed: int = 0
    skipped_empty_rows: int = 0
    id_collisions: list[str] = field(default_factory=list)
    coercion_failures: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    unmapped_counts: Counter = field(default_factory=Counter)
    unmapped_values: Counter = field(default_factory=Counter)
    field_coverage: Counter = field(default_factory=Counter)
    misaligned_lists: list[str] = field(default_factory=list)


def coerce(value: Any, prof: ColumnProfile, stats: ConvertStats) -> Any:
    """Convert a single cleaned cell to its target python type, keeping raw text on failure."""
    if is_empty(value):
        return None
    if prof.kind == "date":
        d = parse_date(value)
        if d is None:
            stats.coercion_failures[prof.header].append(str(value))
            return str(value)
        return d.isoformat()
    if prof.kind in {"integer", "number"}:
        n, _ = parse_number(value, prof.number_locale)
        if n is None:
            stats.coercion_failures[prof.header].append(str(value))
            return str(value)
        return n
    if prof.kind == "boolish":
        b = parse_boolish(value)
        return b if b is not None else str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return parse_date(value).isoformat()
    if isinstance(value, (int, float, bool)):
        return value
    return str(value)


def split_cell(value: Any, prof: ColumnProfile) -> list[Any]:
    if prof.list_separator and isinstance(value, str):
        return [t.strip() for t in value.split(prof.list_separator) if t.strip()]
    return [value]


def apply_value_map(value: Any, vmap: dict[str, Any]) -> list[Any]:
    """Returns a list because one source value may map to several target values."""
    if value is None:
        return []
    key = str(value)
    if key in vmap:
        mapped = vmap[key]
    else:
        # second chance: normalised lookup, so 'MS-SQL ' still matches 'MS-SQL'
        norm = {normalise_key(k): v for k, v in vmap.items()}
        if normalise_key(key) in norm:
            mapped = norm[normalise_key(key)]
        else:
            return [value]
    if mapped is None:
        return []
    if isinstance(mapped, list):
        return list(mapped)
    return [mapped]


def build_record(row: list[Any], profiles: list[ColumnProfile], mapping: dict[str, Any],
                 value_map: dict[str, Any], stats: ConvertStats,
                 keep_empty: bool) -> tuple[dict, dict]:
    record: dict[str, Any] = {}
    unmapped: dict[str, Any] = {}
    # group -> {path: [values]}
    list_groups: dict[str, dict[str, list[Any]]] = defaultdict(dict)

    for prof in profiles:
        raw = clean_cell(row[prof.index]) if prof.index < len(row) else None
        target = mapping.get(prof.header, "__MISSING__")

        if target in (None, "__MISSING__") or target == "null":
            if not is_empty(raw):
                unmapped[prof.header] = coerce(raw, prof, stats)
                stats.unmapped_counts[prof.header] += 1
                stats.unmapped_values[prof.header] += 1
            elif target == "__MISSING__":
                stats.unmapped_counts[prof.header] += 0
            continue

        target = str(target).strip()
        vmap = value_map.get(target, {})

        pieces: list[Any] = []
        for piece in split_cell(raw, prof):
            coerced = coerce(piece, prof, stats)
            pieces.extend(apply_value_map(coerced, vmap))

        if not pieces:
            if keep_empty:
                pieces = [None]
            else:
                continue

        stats.field_coverage[target] += 1
        prefix = list_prefix(target)
        if prefix is not None:
            list_groups[prefix][target] = pieces
        elif target.endswith("[]"):
            set_path(record, parse_path(target[:-2]), pieces)
        else:
            value = pieces[0] if len(pieces) == 1 else pieces
            set_path(record, parse_path(target), value)

    for prefix, columns in list_groups.items():
        length = max(len(v) for v in columns.values())
        if len({len(v) for v in columns.values()}) > 1:
            stats.misaligned_lists.append(prefix)
        for target, values in columns.items():
            tokens = parse_path(target)
            for i in range(length):
                set_path(record, tokens, values[i] if i < len(values) else None, index=i)
    return record, unmapped


def read_existing(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None


def strip_meta(record: dict) -> dict:
    """Everything except _meta, for deciding whether a record really changed."""
    return {k: v for k, v in (record or {}).items() if k != "_meta"}


def order_record(record: dict) -> dict:
    """Stable key order: top-level scalars first, then nested blocks, both alphabetical."""
    scalars = {k: v for k, v in record.items() if not isinstance(v, (dict, list))}
    blocks = {k: v for k, v in record.items() if isinstance(v, (dict, list))}
    out: dict[str, Any] = {}
    for k in sorted(scalars):
        out[k] = scalars[k]
    for k in sorted(blocks):
        v = blocks[k]
        out[k] = order_record(v) if isinstance(v, dict) else v
    return out


def make_id(value: Any, used: dict[str, str], source_key: str,
            id_map: dict[str, str], stats: ConvertStats) -> str:
    if source_key in id_map:
        return id_map[source_key]
    base = slugify(value if not is_empty(value) else "projekt", "-")
    base = base[:60].strip("-") or "projekt"
    candidate, n = base, 1
    while candidate in used and used[candidate] != source_key:
        n += 1
        candidate = f"{base}-{n}"
        if n == 2:
            stats.id_collisions.append(base)
    used[candidate] = source_key
    id_map[source_key] = candidate
    return candidate


class LiteralDumper(yaml.SafeDumper):
    pass


def _str_presenter(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _ordered_dict_presenter(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


LiteralDumper.add_representer(str, _str_presenter)
LiteralDumper.add_representer(OrderedDict, _ordered_dict_presenter)
LiteralDumper.add_representer(defaultdict, _ordered_dict_presenter)


def dump_yaml(data: dict) -> str:
    return yaml.dump(
        data, Dumper=LiteralDumper, sort_keys=False, allow_unicode=True,
        default_flow_style=False, width=100,
    )


# --------------------------------------------------------------------------
# 10. Config loading
# --------------------------------------------------------------------------

def load_yaml_file(path: Path, key: str) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if key in data:
        data = data[key] or {}
    return data if isinstance(data, dict) else {}


def load_id_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out[row["source_key"]] = row["project_id"]
    return out


def save_id_map(path: Path, id_map: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source_key", "project_id"])
        for k in sorted(id_map):
            w.writerow([k, id_map[k]])


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pick_column(headers: list[str], profiles: list[ColumnProfile],
                explicit: str | None, hint_rx: re.Pattern, need_unique: bool,
                require_hint: bool = False) -> str | None:
    if explicit:
        if explicit not in headers:
            raise SystemExit(f"column {explicit!r} not found. Available: {headers}")
        return explicit
    candidates = []
    for p in profiles:
        if p.kind == "empty" or p.fill_rate < 0.9:
            continue
        unique = p.distinct == p.filled
        if need_unique and not unique:
            continue
        # A column with a different value in every row cannot be a grouping.
        if require_hint and unique and p.filled > 3:
            continue
        hinted = bool(hint_rx.search(p.header))
        if require_hint and not hinted:
            continue
        score = (2 if hinted else 0) + (1 if unique else 0)
        if score:
            candidates.append((score, -p.index, p.header))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


# --------------------------------------------------------------------------
# 11. Subcommands
# --------------------------------------------------------------------------

def prepare(args) -> tuple[Path, str, int, list[str], list[list[Any]], list[ColumnProfile]]:
    source = Path(args.source)
    if not source.exists():
        raise SystemExit(f"no such file: {source}")
    sheet, rows = load_rows(source, args.sheet, getattr(args, "encoding", None))
    if not rows:
        raise SystemExit("sheet is empty")

    header_row = (args.header_row - 1) if args.header_row else detect_header_row(rows)
    headers = build_headers(rows, header_row, args.header_rows)
    data_rows = [r for r in rows[header_row + args.header_rows:]
                 if any(not is_empty(clean_cell(c)) for c in r)]
    profiles = profile_sheet(headers, data_rows)
    override = getattr(args, "decimal", "auto")
    if override != "auto":
        for prof in profiles:
            if prof.kind in {"integer", "number"} and prof.number_locale != override:
                prof.number_locale = override
                prof.hints = [h for h in prof.hints if not h.startswith("AMBIGUOUS")]
                prof.hints.append(f"decimal convention forced to '{override}' via --decimal")
    return source, sheet, header_row, headers, data_rows, profiles


def cmd_profile(args) -> None:
    source, sheet, header_row, headers, data_rows, profiles = prepare(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    (out / "profile.md").write_text(
        render_profile_md(source, sheet, header_row, len(data_rows), profiles),
        encoding="utf-8")
    (out / "profile.json").write_text(
        json.dumps([asdict(p) for p in profiles], indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")

    id_col = pick_column(headers, profiles, args.id_column, ID_HINTS, need_unique=True)
    group_col = pick_column(headers, profiles, args.group_column, GROUP_HINTS,
                            need_unique=False, require_hint=True)

    for name, text in (("mapping.yaml", render_mapping_yaml(profiles, id_col, group_col)),
                       ("value_map.yaml", render_value_map_yaml(profiles))):
        target = out / name
        if target.exists() and not args.force:
            print(f"  keeping existing {target} (use --force to regenerate)")
            continue
        target.write_text(text, encoding="utf-8")

    print(f"sheet        : {sheet} (header row {header_row + 1}, {len(data_rows)} data rows)")
    print(f"columns      : {len(profiles)}")
    print(f"id column    : {id_col or 'NOT DETECTED - pass --id-column'}")
    print(f"group column : {group_col or 'none - all files go in one directory'}")
    print(f"written      : {out}/profile.md, profile.json, mapping.yaml, value_map.yaml")
    print("\nNext: review mapping.yaml and value_map.yaml, then run `convert`.")


def cmd_convert(args) -> None:
    source, sheet, header_row, headers, data_rows, profiles = prepare(args)
    cfg = Path(args.config)
    mapping = load_yaml_file(cfg / "mapping.yaml", "columns")
    value_map = load_yaml_file(cfg / "value_map.yaml", "values")

    unknown = [h for h in mapping if h not in headers]
    if unknown:
        print("WARNING: mapping.yaml refers to columns that are not in the sheet:")
        for h in unknown:
            close = difflib.get_close_matches(h, headers, n=1)
            print(f"  - {h!r}" + (f"   did you mean {close[0]!r}?" if close else ""))

    id_col = pick_column(headers, profiles, args.id_column, ID_HINTS, need_unique=True)
    group_col = pick_column(headers, profiles, args.group_column, GROUP_HINTS,
                            need_unique=False, require_hint=True)
    if not id_col:
        print("WARNING: no id column detected, falling back to row numbers "
              "(ids will move if rows are reordered). Pass --id-column.")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    id_map = load_id_map(Path(args.id_map))
    used = {v: k for k, v in id_map.items()}
    stats = ConvertStats()
    imported_at = dt.date.today().isoformat()
    digest = sha256_of(source)[:16]

    id_idx = headers.index(id_col) if id_col else None
    group_idx = headers.index(group_col) if group_col else None
    written_paths: list[Path] = []

    for row_no, row in enumerate(data_rows, start=header_row + args.header_rows + 1):
        record, unmapped = build_record(row, profiles, mapping, value_map, stats, args.keep_empty)
        if not record and not unmapped:
            stats.skipped_empty_rows += 1
            continue

        raw_id = clean_cell(row[id_idx]) if id_idx is not None and id_idx < len(row) else None
        source_key = str(raw_id) if not is_empty(raw_id) else f"__row_{row_no}"
        project_id = make_id(raw_id or f"projekt-{row_no}", used, source_key, id_map, stats)

        final: dict[str, Any] = OrderedDict()
        final["schema_version"] = 1
        final["id"] = project_id
        final.update(order_record(record))
        if unmapped:
            final["_unmapped"] = OrderedDict(sorted(unmapped.items()))
        final["_meta"] = OrderedDict([
            ("source", source.name),
            ("source_sheet", sheet),
            ("source_row", row_no),
            ("imported_at", imported_at),
            ("confidence", "imported"),
            ("last_reviewed", None),
            ("reviewed_by", None),
        ])

        subdir = out
        if group_idx is not None and group_idx < len(row):
            g = clean_cell(row[group_idx])
            if not is_empty(g):
                subdir = out / slugify(g, "-")
        subdir.mkdir(parents=True, exist_ok=True)
        path = subdir / f"{project_id}.yaml"

        # Re-import must not churn files that did not change, and must not
        # overwrite review state a human has set by hand.
        existing = read_existing(path)
        if existing is not None:
            old_meta = existing.get("_meta") or {}
            for key in ("last_reviewed", "reviewed_by"):
                if old_meta.get(key) is not None:
                    final["_meta"][key] = old_meta[key]
            if strip_meta(existing) == strip_meta(final):
                final["_meta"]["imported_at"] = old_meta.get("imported_at", imported_at)
                final["_meta"]["confidence"] = old_meta.get("confidence", "imported")
                stats.unchanged += 1
                written_paths.append(path)
                continue
            stats.changed += 1

        path.write_text(
            f"# generated by import_xlsx.py from {source.name} row {row_no}\n"
            f"# re-import preserves _meta.last_reviewed / reviewed_by and leaves\n"
            f"# unchanged files untouched, so `git status` shows only real changes\n"
            + dump_yaml(final),
            encoding="utf-8")
        written_paths.append(path)
        stats.written += 1

    save_id_map(Path(args.id_map), id_map)
    manifest = OrderedDict([
        ("source", source.name),
        ("source_sha256", sha256_of(source)),
        ("source_sheet", sheet),
        ("imported_at", imported_at),
        ("rows_read", len(data_rows)),
        ("files_total", len(written_paths)),
        ("files_written", stats.written),
        ("files_unchanged", stats.unchanged),
        ("tool", "import_xlsx.py"),
    ])
    (out / "_import_manifest.yaml").write_text(dump_yaml(manifest), encoding="utf-8")

    report = render_import_report(source, sheet, profiles, mapping, stats, len(data_rows))
    Path(args.report).write_text(report, encoding="utf-8")

    print(f"written : {stats.written} file(s) "
          f"({stats.changed} updated, {stats.written - stats.changed} new)")
    print(f"unchanged: {stats.unchanged} file(s) left untouched")
    print(f"output  : {out}")
    print(f"report  : {args.report}")
    if stats.coercion_failures:
        print(f"WARNING : {len(stats.coercion_failures)} column(s) had unparseable values "
              f"(kept as text, see report)")
    if stats.id_collisions:
        print(f"WARNING : {len(set(stats.id_collisions))} id collision(s), suffixed -2, -3 ...")
    if stats.misaligned_lists:
        print(f"WARNING : misaligned multi-value columns under {sorted(set(stats.misaligned_lists))}")


def render_import_report(source: Path, sheet: str, profiles: list[ColumnProfile],
                         mapping: dict, stats: ConvertStats, n_rows: int) -> str:
    mapped = [p for p in profiles if mapping.get(p.header) not in (None, "null")
              and p.header in mapping]
    unmapped = [p for p in profiles if p not in mapped]
    out = [
        f"# Import report: {source.name}",
        "",
        f"- generated: {dt.datetime.now().isoformat(timespec='seconds')}",
        f"- sheet: `{sheet}`",
        f"- rows read: {n_rows}",
        f"- project files written: {stats.written}",
        f"- empty rows skipped: {stats.skipped_empty_rows}",
        f"- columns mapped: {len(mapped)}/{len(profiles)}",
        "",
        "## Field coverage (how many projects have a value)",
        "",
        "| Target field | Projects | Coverage |",
        "|---|---|---|",
    ]
    for target, count in sorted(stats.field_coverage.items(), key=lambda x: (-x[1], x[0])):
        pct = count / stats.written * 100 if stats.written else 0
        out.append(f"| `{target}` | {count} | {pct:.0f}% |")

    out += ["", "## Unmapped columns (currently in `_unmapped`)", ""]
    if unmapped:
        out += ["| Column | Non-empty values | Kind |", "|---|---|---|"]
        for p in sorted(unmapped, key=lambda p: -stats.unmapped_counts.get(p.header, 0)):
            out.append(f"| {p.header} | {stats.unmapped_counts.get(p.header, 0)} | {p.kind} |")
        out += ["", "Promote the ones near the top of this list into `mapping.yaml` next.", ""]
    else:
        out += ["None - every column is mapped.", ""]

    out += ["## Values that passed through unmapped", ""]
    out.append("Values seen in the data but not listed in `value_map.yaml` are written")
    out.append("through unchanged. Check `profile.md` for the full distinct-value lists.")
    out.append("")

    if stats.coercion_failures:
        out += ["## Unparseable values (kept as text)", ""]
        for header, values in sorted(stats.coercion_failures.items()):
            sample = ", ".join(repr(v) for v in sorted(set(values))[:8])
            out.append(f"- **{header}**: {len(values)} cell(s) - {sample}")
        out.append("")

    if stats.id_collisions:
        out += ["## Id collisions", "",
                "These names produced the same slug and were suffixed:", ""]
        for base in sorted(set(stats.id_collisions)):
            out.append(f"- `{base}`")
        out.append("")

    if stats.misaligned_lists:
        out += ["## Misaligned multi-value columns", "",
                "Columns feeding the same list had different token counts, so some",
                "entries were padded with null. Check these rows by hand:", ""]
        for prefix in sorted(set(stats.misaligned_lists)):
            out.append(f"- `{prefix}`")
        out.append("")

    out += [
        "## Reminder",
        "",
        "Every field here is `confidence: imported`, which means nobody has verified it.",
        "Report *verified* coverage separately from raw coverage; the gap between the",
        "two is the real remaining work.",
        "",
    ]
    return "\n".join(out)


def cmd_derive_schema(args) -> None:
    _, _, _, headers, _, profiles = prepare(args)
    cfg = Path(args.config)
    mapping = load_yaml_file(cfg / "mapping.yaml", "columns")
    value_map = load_yaml_file(cfg / "value_map.yaml", "values")

    type_for = {"integer": "integer", "number": "number", "date": "string",
                "enum": "string", "string": "string", "freetext": "string",
                "boolish": "string", "list": "array"}

    root: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Project record (derived from legacy import)",
        "type": "object",
        "required": ["schema_version", "id"],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": 1},
            "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]*$"},
            "_unmapped": {"type": "object"},
            "_meta": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "source_sha256": {"type": "string"},
                    "source_sheet": {"type": "string"},
                    "source_row": {"type": "integer"},
                    "imported_at": {"type": "string", "format": "date"},
                    "confidence": {"enum": ["verified", "estimated", "imported", "unknown"]},
                    "last_reviewed": {"type": ["string", "null"], "format": "date"},
                    "reviewed_by": {"type": ["string", "null"]},
                },
            },
        },
    }

    for prof in profiles:
        target = mapping.get(prof.header)
        if not target or target == "null":
            continue
        target = str(target).strip()
        # If the path already says "[]", the list-ness lives in the path and the
        # leaf is a scalar. Only a multi-value column mapped to a plain path
        # produces an array at the leaf.
        path_is_list = "[" in target
        leaf_kind = prof.kind
        if path_is_list and leaf_kind == "list":
            leaf_kind = "enum" if prof.distinct <= ENUM_MAX_DISTINCT else "string"
        leaf: dict[str, Any] = {"type": type_for.get(leaf_kind, "string")}
        leaf["description"] = f"from spreadsheet column: {prof.header}"
        if prof.kind == "date":
            leaf["format"] = "date"
        looks_like_code_list = (
            prof.kind in {"enum", "boolish", "list"}
            and prof.distinct <= ENUM_MAX_DISTINCT
            and (prof.kind == "list" or prof.filled <= 5
                 or prof.distinct <= 0.5 * prof.filled)
        )
        if looks_like_code_list:
            vmap = value_map.get(target, {})
            vals: set[str] = set()
            for value, _ in prof.values:
                for mapped in apply_value_map(value, vmap):
                    vals.add(str(mapped))
            if vals:
                vals.add("unknown")
                item = {"enum": sorted(vals)}
                wrap = prof.kind == "list" and not path_is_list
                leaf = {"type": "array", "items": item} if wrap else item
                leaf["description"] = f"from spreadsheet column: {prof.header}"
        insert_schema(root, target, leaf)

    Path(args.out).write_text(json.dumps(root, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"written: {args.out}")
    print("This is a starting point derived from the data as it is today. Tighten it by hand:")
    print("  - mark genuinely required fields")
    print("  - add 'unknown' to every enum you want teams to be able to leave undecided")
    print("  - add min/max where a number has a sane range")


def insert_schema(root: dict, path: str, leaf: dict) -> None:
    node = root
    tokens = parse_path(path)
    i = 0
    while i < len(tokens):
        kind, name = tokens[i]
        if kind != "key":
            i += 1
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        props = node.setdefault("properties", {})
        if nxt and nxt[0] in {"list", "index"}:
            arr = props.setdefault(name, {"type": "array", "items": {"type": "object"}})
            if i + 2 >= len(tokens):
                arr["items"] = leaf if "enum" not in leaf else leaf
                return
            node = arr.setdefault("items", {"type": "object"})
            i += 2
            continue
        if i == len(tokens) - 1:
            props[name] = leaf
            return
        node = props.setdefault(name, {"type": "object", "properties": {}})
        i += 1


# --------------------------------------------------------------------------
# 12. CLI
# --------------------------------------------------------------------------

def add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("source", help="path to the .xlsx / .csv file")
    p.add_argument("--sheet", help="sheet name (default: first sheet)")
    p.add_argument("--header-row", type=int, help="1-based header row (default: auto-detect)")
    p.add_argument("--header-rows", type=int, default=1,
                   help="number of header rows to join (default: 1)")
    p.add_argument("--encoding", help="CSV encoding (default: try utf-8, cp1252, latin-1)")
    p.add_argument("--id-column", help="column holding the stable project identifier")
    p.add_argument("--group-column", help="column to use as output sub-directory (e.g. team)")
    p.add_argument("--decimal", choices=["auto", "de", "en"], default="auto",
                   help="how to read '2.500': de=2500, en=2.5 (default: auto-detect per column)")


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="import_xlsx.py",
        description="Convert a legacy one-row-per-project spreadsheet into per-project YAML.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
typical run:

  python import_xlsx.py profile        projekte.xlsx --out import/
  $EDITOR import/mapping.yaml import/value_map.yaml
  python import_xlsx.py convert        projekte.xlsx --config import/ --out projects/
  python import_xlsx.py derive-schema  projekte.xlsx --config import/ --out schema/project.schema.json
""")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("profile", help="inspect the sheet, write starter mapping files")
    add_common(p1)
    p1.add_argument("--out", default="import", help="output directory (default: import)")
    p1.add_argument("--force", action="store_true",
                    help="overwrite existing mapping.yaml / value_map.yaml")
    p1.set_defaults(func=cmd_profile)

    p2 = sub.add_parser("convert", help="write one YAML file per project")
    add_common(p2)
    p2.add_argument("--config", default="import", help="directory holding mapping.yaml")
    p2.add_argument("--out", default="projects", help="output directory (default: projects)")
    p2.add_argument("--id-map", default="import/id_map.csv",
                    help="CSV keeping ids stable across re-imports")
    p2.add_argument("--report", default="import/import-report.md")
    p2.add_argument("--keep-empty", action="store_true",
                    help="write mapped-but-empty fields as null instead of omitting them")
    p2.set_defaults(func=cmd_convert)

    p3 = sub.add_parser("derive-schema", help="emit a JSON Schema draft from the data")
    add_common(p3)
    p3.add_argument("--config", default="import")
    p3.add_argument("--out", default="schema/project.schema.json")
    p3.set_defaults(func=cmd_derive_schema)

    args = ap.parse_args(argv)
    Path(getattr(args, "out", ".")).parent.mkdir(parents=True, exist_ok=True)
    args.func(args)


if __name__ == "__main__":
    main()
