# Column profile: projekte.xlsx

- sheet: `Projektübersicht`
- header row: 3
- data rows: 20
- columns: 18
- generated: 2026-09-14T15:14:52

## Overview

| # | Column | Proposed field | Kind | Fill | Distinct |
|---|--------|----------------|------|------|----------|
| 1 | Projekt-Nr | `projekt_nr` | enum | 100% | 20 |
| 2 | Anwendungsname | `anwendungsname` | enum | 100% | 19 |
| 3 | Team | `team` | enum | 95% | 4 |
| 4 | Standort RZ | `standort_rz` | enum | 95% | 4 |
| 5 | Klassifizierung A | `klassifizierung_a` | enum | 95% | 3 |
| 6 | Klassifizierung B | `klassifizierung_b` | enum | 95% | 4 |
| 7 | Datenbank | `datenbank` | list | 95% | 8 |
| 8 | Sicherheitsklasse | `sicherheitsklasse` | enum | 95% | 4 |
| 9 | Storage Klasse | `storage_klasse` | enum | 95% | 5 |
| 10 | Storage (GB) | `storage_gb` | integer | 65% | 7 |
| 11 | CPU (Kerne) | `cpu_kerne` | integer | 80% | 5 |
| 12 | Betriebssystem | `betriebssystem` | enum | 95% | 5 |
| 13 | Migrationsstrategie | `migrationsstrategie` | enum | 95% | 7 |
| 14 | Status | `status` | enum | 75% | 4 |
| 15 | Ansprechpartner | `ansprechpartner` | enum | 75% | 4 |
| 16 | Betriebssystem EOL | `betriebssystem_eol` | date | 70% | 3 |
| 17 | Bemerkung | `bemerkung` | enum | 50% | 5 |
| 18 | Altspalte | `altspalte` | empty | 0% | 0 |

## Columns in detail

### 1. Projekt-Nr

- proposed field: `projekt_nr`
- kind: enum
- filled: 20/20 (100%)
- distinct values: 20

  - `P-1001` x1
  - `P-1002` x1
  - `P-1003` x1
  - `P-1004` x1
  - `P-1005` x1
  - `P-1006` x1
  - `P-1007` x1
  - `P-1008` x1
  - `P-1009` x1
  - `P-1010` x1
  - `P-1011` x1
  - `P-1012` x1
  - `P-1013` x1
  - `P-1014` x1
  - `P-1015` x1
  - `P-1016` x1
  - `P-1017` x1
  - `P-1018` x1
  - `P-2001` x1
  - `P-2002` x1

### 2. Anwendungsname

- proposed field: `anwendungsname`
- kind: enum
- filled: 20/20 (100%)
- distinct values: 19

  - `Kundenportal` x2
  - `Archivierung` x1
  - `Bestandsführung` x1
  - `Dokumentenarchiv` x1
  - `Fakturierung` x1
  - `Lagerverwaltung` x1
  - `Legacy CRM` x1
  - `Mahnwesen` x1
  - `Namenloses Altsystem` x1
  - `Partnerportal` x1
  - `Payment Gateway` x1
  - `Provisionsabrechnung` x1
  - `Reporting Hub` x1
  - `Risikoprüfung` x1
  - `Schadenmeldung` x1
  - `Stammdaten` x1
  - `Tarifrechner` x1
  - `Vertragsverwaltung` x1
  - `Zahlungsverkehr` x1

### 3. Team

- proposed field: `team`
- kind: enum
- filled: 19/20 (95%)
- distinct values: 4

  - `Team Gamma` x7
  - `Team Delta` x6
  - `Team Beta` x5
  - `Team Alpha` x1

### 4. Standort RZ

- proposed field: `standort_rz`
- kind: enum
- filled: 19/20 (95%)
- distinct values: 4
- **note:** 1 value(s) appear in several spellings

  - `MUC-01` x6
  - `FRA-02` x5
  - `BER-01` x4
  - `muc-01` x4

### 5. Klassifizierung A

- proposed field: `klassifizierung_a`
- kind: enum
- filled: 19/20 (95%)
- distinct values: 3

  - `CA-1` x9
  - `CA-2` x6
  - `CA-3` x4

### 6. Klassifizierung B

- proposed field: `klassifizierung_b`
- kind: enum
- filled: 19/20 (95%)
- distinct values: 4

  - `CB-4` x6
  - `CB-1` x5
  - `CB-2` x5
  - `CB-3` x3

### 7. Datenbank

- proposed field: `datenbank`
- kind: list
- multi-value separator: `;`
- filled: 19/20 (95%)
- distinct values: 8
- **note:** other separator(s) also appear in this column: '/'. Those cells are NOT split - map them explicitly in value_map.yaml, e.g. "MSSQL/Redis": [mssql, redis]
- **note:** 2 value(s) appear in several spellings

  - `DB2` x7
  - `Redis` x7
  - `mssql` x3
  - `MS-SQL` x2
  - `MSSQL` x1
  - `MSSQL/Redis` x1
  - `db2` x1
  - `keine` x1

### 8. Sicherheitsklasse

- proposed field: `sicherheitsklasse`
- kind: enum
- filled: 19/20 (95%)
- distinct values: 4

  - `S1` x6
  - `S3` x5
  - `S2` x4
  - `S4` x4

### 9. Storage Klasse

- proposed field: `storage_klasse`
- kind: enum
- filled: 19/20 (95%)
- distinct values: 5
- **note:** 2 value(s) appear in several spellings

  - `Block` x5
  - `block` x5
  - `object` x5
  - `block, object` x2
  - `block/object` x2

### 10. Storage (GB)

- proposed field: `storage_gb`
- kind: integer (unit: gb)
- filled: 13/20 (65%)
- distinct values: 7
- **note:** unit 'gb' stripped from the values
- **note:** AMBIGUOUS decimal separator: '2.500' is 2500 in a German sheet and 2.500 in an English one. Currently read as German. Override with --decimal en if that is wrong.

  - `250` x4
  - `500` x3
  - `2.500` x2
  - `1200` x1
  - `4000` x1
  - `800 GB` x1
  - `900` x1

### 11. CPU (Kerne)

- proposed field: `cpu_kerne`
- kind: integer (unit: kerne)
- filled: 16/20 (80%)
- distinct values: 5
- **note:** unit 'kerne' stripped from the values

  - `32` x6
  - `8` x5
  - `4` x2
  - `8 Kerne` x2
  - `16` x1

### 12. Betriebssystem

- proposed field: `betriebssystem`
- kind: enum
- filled: 19/20 (95%)
- distinct values: 5
- **note:** possibly the same: 'Windows Server 2019' / 'Windows Server 2016'

  - `Windows Server 2016` x7
  - `AIX 7.2` x6
  - `Windows Server 2019` x3
  - `SLES 15` x2
  - `RHEL 7.9` x1

### 13. Migrationsstrategie

- proposed field: `migrationsstrategie`
- kind: enum
- filled: 19/20 (95%)
- distinct values: 7
- **note:** 2 value(s) appear in several spellings

  - `retain` x5
  - `Rehost` x4
  - `rehost` x3
  - `retire` x3
  - `replatform` x2
  - `Retain` x1
  - `refactor` x1

### 14. Status

- proposed field: `status`
- kind: enum
- filled: 15/20 (75%)
- distinct values: 4

  - `nicht bewertet` x6
  - `bewertet` x3
  - `in Arbeit` x3
  - `migriert` x3

### 15. Ansprechpartner

- proposed field: `ansprechpartner`
- kind: enum
- filled: 15/20 (75%)
- distinct values: 4

  - `H. Weber` x5
  - `S. Bauer` x4
  - `M. Huber` x3
  - `T. Ludwig` x3

### 16. Betriebssystem EOL

- proposed field: `betriebssystem_eol`
- kind: date
- filled: 14/20 (70%)
- distinct values: 3

  - `2027-11-30` x7
  - `31.12.2025` x4
  - `30.06.2024` x3

### 17. Bemerkung

- proposed field: `bemerkung`
- kind: enum
- filled: 10/20 (50%)
- distinct values: 5

  - `Hersteller unterstützt keine Container; Support endet 2027` x4
  - `Abhängigkeit zu Stammdaten, muss danach migriert werden` x2
  - `Migration 2023 gestoppt, Lizenzthema` x2
  - `keine Doku vorhanden` x1
  - `läuft auf Power-Hardware, ppc64le` x1

### 18. Altspalte

- proposed field: `altspalte`
- kind: empty
- filled: 0/20 (0%)
- distinct values: 0
- **note:** column is completely empty in this sheet
