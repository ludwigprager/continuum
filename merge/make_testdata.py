#!/usr/bin/env python3
"""Builds deliberately messy legacy-style CSV extracts in merge/input/.

Three files, not one, because one source gives the merge nothing to do. They
overlap on most projects, disagree on some cells, leave gaps each other fills,
and one spells a key in the wrong case - which is what step 1 exists to resolve
(SPEC 5.4). Run ./merge/merge.sh afterwards and read merge_conflicts.csv.

They are also awkward in the ways real extracts are: a title row and a blank
line above the header, cp1252 in one file and utf-8 in another, ';' and ','
delimiters, mixed units, two date formats, and a column that is empty in every
row.

Deterministic: same seed, same files, so a re-run produces no diff.
"""
import random
import csv
from pathlib import Path

random.seed(7)

HEADERS = [
    "Projekt-Nr", "Anwendungsname", "Team", "Standort RZ", "Klassifizierung A",
    "Klassifizierung B", "Datenbank", "Sicherheitsklasse", "Storage Klasse",
    "Storage (GB)", "CPU (Kerne)", "Betriebssystem", "Migrationsstrategie",
    "Status", "Ansprechpartner", "Betriebssystem EOL", "Bemerkung", "Altspalte",
]

DBS = ["MSSQL", "MS-SQL", "mssql", "DB2", "db2", "Redis", "MSSQL/Redis", "DB2; Redis", "keine"]
SITES = ["MUC-01", "muc-01", "FRA-02", "FRA-02 ", "BER-01"]
OS = ["Windows Server 2016", "Windows Server 2019", "RHEL 7.9", "AIX 7.2", "SLES 15"]
STRAT = ["rehost", "Rehost", "replatform", "retain", "Retain", "refactor", "retire", ""]
STATUS = ["nicht bewertet", "bewertet", "in Arbeit", "migriert", "?"]
SEC = ["S1", "S2", "S3", "S4"]
CA = ["CA-1", "CA-2", "CA-3"]
CB = ["CB-1", "CB-2", "CB-3", "CB-4"]
STORAGE_CLASS = ["block", "Block", "object", "block, object", "block/object"]
TEAMS = ["Team Alpha", "Team Beta", "Team Gamma", "Team Delta"]
NAMES = [
    "Payment Gateway", "Legacy CRM", "Kundenportal", "Fakturierung", "Lagerverwaltung",
    "Reporting Hub", "Vertragsverwaltung", "Dokumentenarchiv", "Schadenmeldung",
    "Tarifrechner", "Provisionsabrechnung", "Partnerportal", "Mahnwesen",
    "Bestandsführung", "Risikoprüfung", "Zahlungsverkehr", "Stammdaten", "Archivierung",
]
COMMENTS = [
    "", "", "Migration 2023 gestoppt, Lizenzthema",
    "Hersteller unterstützt keine Container; Support endet 2027",
    "läuft auf Power-Hardware, ppc64le", "-", "k.A.",
    "Abhängigkeit zu Stammdaten, muss danach migriert werden",
]

OUT = Path(__file__).resolve().parent / "input"


def row(i: int, name: str) -> list:
    """One project's row. Deterministic given the seed."""
    return [
        f"P-{1000 + i}",
        name,
        random.choice(TEAMS),
        random.choice(SITES),
        random.choice(CA),
        random.choice(CB),
        random.choice(DBS),
        random.choice(SEC),
        random.choice(STORAGE_CLASS),
        random.choice([500, 1200, "2.500", "800 GB", 250, "", "k.A.", 4000]),
        random.choice([4, 8, 16, "32", "", 2, "8 Kerne"]),
        random.choice(OS),
        random.choice(STRAT),
        random.choice(STATUS),
        random.choice(["M. Huber", "S. Bauer", "H. Weber", "", "T. Ludwig"]),
        random.choice(["30.06.2024", "31.12.2025", "", "2027-11-30", "unbekannt"]),
        random.choice(COMMENTS),
        "",  # a column that is entirely empty, in every file
    ]


def write(name: str, header: list, rows: list, *, encoding: str, delimiter: str,
          preamble: bool = True) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    with path.open("w", encoding=encoding, newline="") as fh:
        w = csv.writer(fh, delimiter=delimiter, lineterminator="\r\n")
        if preamble:
            # Legacy exports rarely start at the header.
            w.writerow(["Projektübersicht Migration - Stand 03/2026"])
            w.writerow([])
        w.writerow(header)
        w.writerows(rows)
    print(f"wrote {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}"
          f"  ({len(rows)} rows, {encoding}, delimiter {delimiter!r})")


rows = [row(i, name) for i, name in enumerate(NAMES, start=1)]
# A duplicate name to force an id collision, and a row with gaps.
rows.append(["P-2001", "Kundenportal", "Team Beta", "BER-01", "CA-1", "CB-2", "DB2",
             "S2", "object", 900, 8, "AIX 7.2", "retain", "bewertet", "", "", "", ""])
rows.append(["P-2002", "Namenloses Altsystem", "", "", "", "", "", "", "", "", "",
             "", "", "nicht bewertet", "", "", "keine Doku vorhanden", ""])

# --- 01: the authoritative extract. Everything, and it wins every contest. ---
write("01-sap-export.csv", HEADERS, rows, encoding="cp1252", delimiter=";")

# --- 02: a CMDB dump. Hardware columns only, and it contradicts 01 on them. --
CMDB = [HEADERS[0], HEADERS[1], HEADERS[3], HEADERS[9], HEADERS[10], HEADERS[6]]
cmdb = []
for i, r in enumerate(rows[:12]):
    cmdb.append([
        r[0].lower() if i == 0 else r[0],      # one key in the wrong case
        r[1],
        str(r[3]).lower(),                      # muc-01 against MUC-01
        random.choice([256, 512, 1024, ""]),    # disagrees with 01 on storage
        random.choice([2, 4, 32, ""]),          # and on cores
        r[6],
    ])
# A row for a project 01 has never heard of, and one with no key at all.
cmdb.append(["P-3001", "Schattensystem", "FRA-02", 128, 2, "Redis"])
cmdb.append(["", "Ohne Nummer", "MUC-01", 64, 1, ""])
write("02-cmdb-dump.csv", CMDB, cmdb, encoding="utf-8", delimiter=",", preamble=False)

# --- 03: a team survey. Fills gaps 01 left, and nothing else. ----------------
SURVEY = [HEADERS[0], HEADERS[1], HEADERS[14], HEADERS[16]]
survey = [[r[0], r[1],
           random.choice(["M. Huber", "S. Bauer", "H. Weber", "T. Ludwig"]),
           random.choice(COMMENTS)]
          for r in rows[8:]]
write("03-team-umfrage.csv", SURVEY, survey, encoding="cp1252", delimiter=";")

print("\nnext: ./merge/merge.sh --key \"Projekt-Nr\"")
