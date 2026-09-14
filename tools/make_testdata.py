#!/usr/bin/env python3
"""Builds a deliberately messy legacy-style spreadsheet for testing the importer."""
import random
from openpyxl import Workbook

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

wb = Workbook()
ws = wb.active
ws.title = "Projektübersicht"

# Legacy sheets rarely start at A1.
ws.append(["Projektübersicht Migration - Stand 03/2026"])
ws.append([])
ws.append(HEADERS)

for i, name in enumerate(NAMES, start=1):
    ws.append([
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
        "",  # column that is entirely empty
    ])

# A duplicate name to force an id collision, and a row with gaps.
ws.append(["P-2001", "Kundenportal", "Team Beta", "BER-01", "CA-1", "CB-2", "DB2",
           "S2", "object", 900, 8, "AIX 7.2", "retain", "bewertet", "", "", "", ""])
ws.append(["P-2002", "Namenloses Altsystem", "", "", "", "", "", "", "", "", "",
           "", "", "nicht bewertet", "", "", "keine Doku vorhanden", ""])
ws.append([])  # blank row in the middle

wb.save("projekte.xlsx")
print("wrote projekte.xlsx")
