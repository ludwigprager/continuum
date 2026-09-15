#!/usr/bin/env python3
"""
merge_csv.py - step 1 of the import. SPEC 3.1, 5.4.

    merge/input/*.csv  ->  merge/merged.csv           one row per project
                           merge/merge_conflicts.csv  every value discarded
                           merge/merge_manifest.json  the sources, with sha256

Several systems each export a CSV describing the same projects. They overlap,
they leave different gaps, and they contradict each other. This reduces them to
one row per project so that step 2 (import_csv.py) reads a single ordinary file
and never has to know there was more than one source.

The split exists so the merge is inspectable: a merge whose only visible result
is 400 YAML files is a merge nobody checks. See SPEC 3.1.

Everything this needs lives in merge/. The sources go in merge/input/ and the
results are written one level up, beside this file - so the directory that is
read contains nothing but extracts, and **every CSV in it is a source**. There
is no name to remember and no convention to observe: drop a file in, it is
merged.

Handing the result to step 2 is a **manual copy** to import/merged.csv. That is
the inspection gate: re-cutting an extract and re-running the merge cannot
change what step 2 is reading until somebody has looked at the result and
copied it across. The last line of output says exactly which command to run.

**First wins, per cell.** Files are ranked by sorted filename, so `ls
merge/input/` shows the precedence. For each column the highest-ranked file with a value for
it supplies the value; every value that loses is written to
merge_conflicts.csv, which is the ONLY record of what was discarded (SPEC
5.4.1).

Stdlib only, deliberately. The tests run in the pipeline image, which carries
ruamel.yaml and no PyYAML, so anything this imports must be importable there.
That is also why the manifest is JSON while projects/_import_manifest.yaml,
written by step 2 in the import image, is YAML.

Exit codes: 0 ok, 1 invalid data, 2 tool/usage error.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

EXIT_OK, EXIT_INVALID, EXIT_TOOL = 0, 1, 2

# Tried in order. utf-8-sig first so a BOM is stripped rather than glued to the
# first header, where it would make that column unequal to itself (SPEC 9).
ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")

# German exports use ';' because the decimal comma has taken ','.
DELIMITERS = ";,\t|"

# The merged file is written for a German Excel reader (SPEC 3.2): ';' so the
# columns land in cells, a BOM so umlauts render, CRLF so nothing re-wraps.
OUT_ENCODING = "utf-8-sig"
OUT_DELIMITER = ";"
OUT_LINETERMINATOR = "\r\n"

CONFLICT_COLUMNS = ["key", "column", "winning_value", "winning_file",
                    "discarded_value", "discarded_file"]

# What this writes.
MERGED_NAME = "merged.csv"
CONFLICTS_NAME = "merge_conflicts.csv"
MANIFEST_NAME = "merge_manifest.json"

# Also what it must never read back. With the default layout it cannot - the
# outputs are written a level above the directory that is read - so this is
# belt and braces, for a --sources pointed somewhere careless. An output read
# as a source would win every contest by sorting first, silently.
OUTPUT_NAMES = frozenset({MERGED_NAME, CONFLICTS_NAME, MANIFEST_NAME})

# Where the sources live by default, and where the results go: input/ beside
# this file, and this file's own directory. Results land in the PARENT of
# whatever --sources names, so that the read directory stays sources-only
# wherever it is pointed.
DEFAULT_SOURCE_DIR = Path(__file__).resolve().parent / "input"

# How far into a file to look for the header row before giving up.
HEADER_SEARCH_ROWS = 50


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def is_empty(value: str | None) -> bool:
    """An empty cell means nobody answered. Not zero, not `no` (SPEC 5.1)."""
    return value is None or value.strip() == ""


def norm_header(header: str) -> str:
    """For *detecting* near-misses between files, never for output."""
    return re.sub(r"\s+", " ", header).strip().casefold()


def norm_key(value: str) -> str:
    """The join key, trimmed and casefolded, so P-1001 and p-1001 are one
    project. This is the one value the merge normalises: without it there is
    nothing to merge on (SPEC 3.1.1)."""
    return re.sub(r"\s+", " ", value).strip().casefold()


def show(path: Path) -> str:
    """A path as the reader should see it: relative to the working directory.

    This runs in a container with the repo bind-mounted at /work, so absolute
    paths are correct inside it and useless outside - and the last thing this
    prints is a command meant to be pasted on the host. The container's
    working directory is the repo root, so a relative path is right in both.
    """
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Reading. Every per-file difference is dealt with here and nowhere else
# (SPEC 3.2): encoding, delimiter, quoting, header row, header text.
# --------------------------------------------------------------------------

def decode(path: Path) -> tuple[str, str]:
    last_error: Exception | None = None
    for encoding in ENCODINGS:
        try:
            return path.read_text(encoding=encoding), encoding
        except (UnicodeDecodeError, LookupError) as exc:
            last_error = exc
    raise ValueError(f"could not decode {path.name}: {last_error}")


def sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=DELIMITERS).delimiter
    except csv.Error:
        # Sniffer gives up on short or uniform samples. Counting is a worse
        # heuristic but a predictable one, and it is right about ';' vs ','
        # on every German export seen so far.
        counts = {d: sample.count(d) for d in DELIMITERS}
        best = max(counts, key=lambda d: counts[d])
        return best if counts[best] else ","


def find_header_row(rows: list[list[str]], key: str) -> tuple[int, bool]:
    """Return (index, matched_exactly) of the row holding the join key.

    The header row is not guessed. `--key` names a column that must be there,
    so the first row containing it *is* the header row - which turns a
    heuristic into a lookup, and turns "no header found" into the error the
    file actually has: the key column is missing.
    """
    for index, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        if any(cell.strip() == key for cell in row):
            return index, True
    # Second pass, forgiving of case and inner spacing, so a file that spells
    # the header differently says so instead of looking like it lacks it.
    wanted = norm_header(key)
    for index, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        if any(norm_header(cell) == wanted for cell in row):
            return index, False
    return -1, False


class Source:
    """One CSV, read and normalised into headers + rows of trimmed strings."""

    def __init__(self, path: Path, rank: int, key: str) -> None:
        self.path = path
        self.name = path.name
        self.rank = rank

        text, self.encoding = decode(path)
        self.delimiter = sniff_delimiter(text[:8192])
        raw_rows = [list(r) for r in csv.reader(text.splitlines(True),
                                                delimiter=self.delimiter)]

        self.header_row, exact = find_header_row(raw_rows, key)
        if self.header_row < 0:
            raise ValueError(
                f"{self.name}: no column named {key!r}.\n"
                f"  --key must name a column present in every source "
                f"(SPEC 3.1.1).\n"
                f"  columns found: "
                f"{', '.join(repr(c) for c in raw_rows[0][:12]) if raw_rows else '(file is empty)'}")
        self.key_matched_exactly = exact

        # Trailing empty cells are an artifact of how the file was written, not
        # columns. Dropping them keeps the union from growing a tail of ''.
        header = [c.strip() for c in raw_rows[self.header_row]]
        while header and not header[-1]:
            header.pop()
        self.headers = header

        # The key column, by whichever spelling this file uses.
        self.key_header = next(c for c in self.headers
                               if c == key or norm_header(c) == norm_header(key))
        self.key_index = self.headers.index(self.key_header)

        self.rows: list[list[str]] = []
        for row in raw_rows[self.header_row + 1:]:
            cells = [c.strip() for c in row[:len(self.headers)]]
            cells += [""] * (len(self.headers) - len(cells))
            if all(not c for c in cells):
                continue          # a blank line is not a project
            self.rows.append(cells)


def collect_sources(source_dir: Path, key: str) -> tuple[list[Source], list[str]]:
    """Every *.csv in source_dir, sorted by name.

    That is the whole rule, and it is why the sources have a directory to
    themselves: nothing else is in there, so there is no exception to state and
    dropping a new extract in is enough to include it. The OUTPUT_NAMES filter
    below cannot fire in the default layout and is there for a --sources
    pointed at the results by mistake.
    """
    if not source_dir.is_dir():
        raise FileNotFoundError(f"{show(source_dir)}/ does not exist")

    paths = sorted((p for p in source_dir.glob("*.csv")
                    if p.name not in OUTPUT_NAMES),
                   key=lambda p: p.name)
    if not paths:
        raise FileNotFoundError(
            f"no source CSVs in {show(source_dir)}/ - put the extracts there")

    sources, problems = [], []
    for rank, path in enumerate(paths, start=1):
        try:
            sources.append(Source(path, rank, key))
        except ValueError as exc:
            problems.append(str(exc))
    return sources, problems


# --------------------------------------------------------------------------
# The merge itself
# --------------------------------------------------------------------------

class Conflict:
    __slots__ = ("key", "column", "winning_value", "winning_file",
                 "discarded_value", "discarded_file")

    def __init__(self, key, column, winning_value, winning_file,
                 discarded_value, discarded_file):
        self.key = key
        self.column = column
        self.winning_value = winning_value
        self.winning_file = winning_file
        self.discarded_value = discarded_value
        self.discarded_file = discarded_file

    def as_row(self) -> list[str]:
        return [self.key, self.column, self.winning_value, self.winning_file,
                self.discarded_value, self.discarded_file]

    def sort_key(self) -> tuple:
        return (self.key, self.column, self.discarded_file, self.discarded_value)


class Project:
    """One merged row: the winning value per column, and where it came from."""

    def __init__(self, key_display: str, order: tuple) -> None:
        self.key_display = key_display
        self.order = order
        self.values: dict[str, str] = {}
        self.sources: dict[str, str] = {}      # column -> filename that won it
        self.contributors: list[str] = []


def merge(sources: list[Source], key: str) -> tuple[list[str], list[Project],
                                                    list[Conflict], dict]:
    # Union of columns in first-seen order by precedence, so the highest-ranked
    # source keeps its familiar layout and later-only columns are appended
    # rather than interleaved (SPEC 3.2).
    columns: list[str] = []
    seen_normalised: dict[str, tuple[str, str]] = {}   # norm -> (raw, file)
    near_misses: list[str] = []
    for source in sources:
        for header in source.headers:
            if not header:
                continue
            if header not in columns:
                columns.append(header)
            normalised = norm_header(header)
            previous = seen_normalised.get(normalised)
            if previous is None:
                seen_normalised[normalised] = (header, source.name)
            elif previous[0] != header:
                near_misses.append(
                    f"{previous[0]!r} in {previous[1]} vs {header!r} in "
                    f"{source.name} - kept as two columns, they will not merge")

    projects: dict[object, Project] = {}
    conflicts: list[Conflict] = []
    blank_keys = 0
    casefold_merges: Counter = Counter()

    # The key column never produces a conflict. Trimming and casefolding it is
    # the one normalisation the merge performs, by design (SPEC 5.4 rule 5), so
    # every difference it can show is exactly a difference the casefold-merge
    # warning already reports. Left in, `P-1001` vs `p-1001` would be logged as
    # a discarded value for every such row and bury the real disagreements.
    key_columns = {s.key_header for s in sources}

    for source in sources:
        for row_number, cells in enumerate(source.rows, start=1):
            raw_key = cells[source.key_index]

            if is_empty(raw_key):
                # A blank key never matches another blank key. Collapsing them
                # would invent one project holding the merged remains of
                # everything nobody keyed (SPEC 3.1.1).
                blank_keys += 1
                identity: object = ("", source.rank, row_number)
                order = (1, source.rank, row_number)
            else:
                identity = norm_key(raw_key)
                order = (0, identity)

            project = projects.get(identity)
            if project is None:
                project = Project(raw_key, order)
                projects[identity] = project
            elif not is_empty(raw_key) and raw_key != project.key_display:
                # Merged only because the keys were casefolded or trimmed. That
                # is an assumption, so it is a visible one.
                casefold_merges[(project.key_display, raw_key)] += 1

            if source.name not in project.contributors:
                project.contributors.append(source.name)

            for column, value in zip(source.headers, cells):
                if not column or is_empty(value):
                    continue                      # empty is not a contradiction
                held = project.values.get(column)
                if held is None:
                    project.values[column] = value
                    project.sources[column] = source.name
                elif held != value and column not in key_columns:
                    # Verbatim comparison: MUC-01 and muc-01 are a reported
                    # disagreement here, and value_map.yaml decides in step 2
                    # whether they mean the same thing (SPEC 5.4 rule 5).
                    conflicts.append(Conflict(
                        key=project.key_display, column=column,
                        winning_value=held,
                        winning_file=project.sources[column],
                        discarded_value=value, discarded_file=source.name))

    ordered = sorted(projects.values(), key=lambda p: p.order)
    conflicts.sort(key=Conflict.sort_key)

    stats = {
        "near_misses": near_misses,
        "blank_keys": blank_keys,
        "casefold_merges": casefold_merges,
        "rows_in": sum(len(s.rows) for s in sources),
    }
    return columns, ordered, conflicts, stats


# --------------------------------------------------------------------------
# Writing. Byte-identical for identical input: fixed column order, rows sorted
# by join key, one fixed dialect, and no clock read anywhere.
# --------------------------------------------------------------------------

def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=OUT_ENCODING, newline="") as handle:
        writer = csv.writer(handle, delimiter=OUT_DELIMITER,
                            lineterminator=OUT_LINETERMINATOR,
                            quoting=csv.QUOTE_MINIMAL)
        writer.writerow(header)
        writer.writerows(rows)


def write_manifest(path: Path, key: str, sources: list[Source],
                   columns: list[str], projects: list[Project],
                   conflicts: list[Conflict], stats: dict) -> None:
    """Provenance for step 2, which cannot see the sources (SPEC 5.4.1).

    No timestamp: this file is part of what must be byte-identical between two
    runs of the same input. The import date is stamped by step 2, which is
    where SPEC 3.5 puts it.
    """
    manifest = {
        "tool": "merge_csv.py",
        "join_key": key,
        "sources": [
            {"file": s.name, "rank": s.rank, "sha256": sha256_of(s.path),
             "encoding": s.encoding, "delimiter": s.delimiter,
             "header_row": s.header_row + 1, "rows": len(s.rows)}
            for s in sources
        ],
        "columns": columns,
        "rows_in": stats["rows_in"],
        "rows_out": len(projects),
        "conflicts": len(conflicts),
        "blank_keys": stats["blank_keys"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False,
                               sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="merge_csv.py",
        description="Merge the import CSVs into one. SPEC 3.1, 5.4.")
    parser.add_argument(
        "--key", required=True, metavar="HEADER",
        help="column header identifying a project. Required: there is no "
             "default and no auto-detection, because a wrong guess produces a "
             "plausible file with the wrong number of rows.")
    parser.add_argument("--sources", dest="source_dir",
                        default=str(DEFAULT_SOURCE_DIR), metavar="DIR",
                        help="where the source CSVs are (default: merge/input/"
                             "). A directory, never a filename, and everything "
                             "in it is a source: sorted order within it is the "
                             "precedence.")
    parser.add_argument("--out", default=None, metavar="FILE",
                        help=f"merged CSV (default: {MERGED_NAME} in the "
                             f"PARENT of --sources, so the directory that is "
                             f"read holds nothing but sources)")
    parser.add_argument("--destination", default="import/merged.csv",
                        metavar="FILE",
                        help="where step 2 expects it. NOT written - it is "
                             "named in the instruction printed at the end, and "
                             "the copy is yours to make (default: "
                             "import/merged.csv)")
    parser.add_argument("--conflicts", default=None, metavar="FILE",
                        help="discarded values (default: beside --out)")
    parser.add_argument("--manifest", default=None, metavar="FILE",
                        help="provenance for step 2 (default: beside --out)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_dir = Path(args.source_dir)
    out_path = Path(args.out) if args.out else source_dir.parent / MERGED_NAME
    conflicts_path = (Path(args.conflicts) if args.conflicts
                      else out_path.with_name(CONFLICTS_NAME))
    manifest_path = (Path(args.manifest) if args.manifest
                     else out_path.with_name(MANIFEST_NAME))

    try:
        sources, problems = collect_sources(source_dir, args.key)
    except FileNotFoundError as exc:
        print(f"merge_csv.py: {exc}", file=sys.stderr)
        return EXIT_TOOL

    if problems:
        # A source missing the key column is a data problem, not a usage one:
        # it is named rather than quietly skipped (SPEC 3.1.1).
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return EXIT_INVALID

    print(f"join key : {args.key!r}")
    print("sources  : sorted filename order is the precedence, first wins")
    for source in sources:
        print(f"  {source.rank}. {source.name:<28} {len(source.rows):>5} rows"
              f"   [{source.encoding}, delimiter {source.delimiter!r}]")

    columns, projects, conflicts, stats = merge(sources, args.key)

    for source in sources:
        if not source.key_matched_exactly:
            print(f"WARNING: {source.name} spells the key column "
                  f"{source.key_header!r}, not {args.key!r}", file=sys.stderr)
    for near_miss in stats["near_misses"]:
        print(f"WARNING: near-identical column names: {near_miss}",
              file=sys.stderr)

    write_csv(out_path, columns,
              [[p.values.get(c, "") for c in columns] for p in projects])
    write_csv(conflicts_path, CONFLICT_COLUMNS, [c.as_row() for c in conflicts])
    write_manifest(manifest_path, args.key, sources, columns, projects,
                   conflicts, stats)

    print(f"\nmerged   : {stats['rows_in']} rows in -> {len(projects)} rows out,"
          f" {len(columns)} columns")
    print(f"conflicts: {len(conflicts)} value(s) discarded -> {show(conflicts_path)}")
    if stats["casefold_merges"]:
        total = sum(stats["casefold_merges"].values())
        print(f"WARNING : {total} row(s) merged only after trimming/casefolding "
              f"the key:", file=sys.stderr)
        for (kept, other), count in sorted(stats["casefold_merges"].items()):
            print(f"          {kept!r} <- {other!r}  ({count}x)", file=sys.stderr)
    if stats["blank_keys"]:
        print(f"WARNING : {stats['blank_keys']} row(s) had no {args.key!r} and "
              f"were kept as separate rows, never merged together",
              file=sys.stderr)
    print(f"written  : {show(out_path)}")

    # The handoff is deliberately manual: nothing reaches step 2 until somebody
    # has read the merge and copied it across. So the last word is an
    # instruction, not a report - and it is a command that can be pasted.
    destination = Path(args.destination)
    print(f"\nInspect {show(out_path)}, then copy it to {show(destination)}:")
    print(f"    cp {show(out_path)} {show(destination)}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
