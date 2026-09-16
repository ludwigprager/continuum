# Continuum Katalog

Das Reporting-Pipeline für **Projekt Continuum**, beschrieben in `continuum.md` (`continuum.de.md` auf Deutsch). Dieses Dokument ist die Initiative; dieses Repository ist der Katalog und der tägliche Bericht basierend darauf.

Git ist das einzig gültige System: Eine YAML-Datei pro Projekt, flach in `projects/`. Alles Abgeleitete — Snapshots, der Berichtsmodell, die fünf Ausgabeformate — wird aus diesen Dateien neu aufgebaut und nie per Hand gewartet.

`SPEC.md` ist das Design-Dokument. Dieses Dokument ist die Anleitung für den täglichen Betrieb.

## Entwicklungsprozess

Die tägliche Arbeitsweise folgt diesen Schritten:

### Projekt hinzufügen
1. Neues Projekt im `projects/` Verzeichnis anlegen
2. Struktur validieren: `./check.sh`

### Daten aktualisieren
1. Daten in `projects/<projekt-id>.yaml` editieren
2. Validierung durchführen: `./check.sh`

### Schema anpassen  
1. In `schema/project.schema.yaml` Feld hinzufügen/ändern
2. Validierung durchführen: `./check.sh --check-schema`

### Daten importieren (einmalig)
1. CSV-Dateien in `merge/input/` ablegen
2. Merge durchführen: `./merge/merge.sh --key "Projekt-Nr"`
3. Ergebnis kopieren: `cp merge/merged.csv import/merged.csv`
4. Schema analysieren: `./import/import.sh profile`
5. Mapping anpassen und konvertieren: `./import/import.sh convert`

## Anforderungen

Podman (bevorzugt) oder Docker. Nichts anderes – keine Python, keine pip auf dem Host. Jedes Tool läuft in einem Container.

## Schnellstart

```bash
./verify.sh                         # alles: shellcheck, Schema Selbsttest, Tests, Exit-Codes
./check.sh tests/fixtures/projects  # validiere die Fixture-Dateien
./check.sh --check-schema           # validiere das Schema und die Referenzdateien
./check.sh                          # validiere projects/ - siehe unten
./merge/merge.sh --key "Projekt-Nr" # Schritt 1: merge/input/*.csv -> merged.csv
./import/import.sh convert                 # Schritt 2: merged.csv -> projects/
./snapshot.sh                       # projects/ -> out/tables/*.jsonl
./report.sh                         # Snapshot + Modell + alle fünf Formate -> out/reports/<Datum>/
./report.sh --lang en               # gleich, aber mit englischen Bezeichnungen
```

## Tägliche Aufgaben

### 1. Projekt hinzufügen

Ein neues Projekt wird über die YAML-Datei direkt im `projects/` Verzeichnis hinzugefügt. Jedes Projekt benötigt mindestens die folgenden Felder:
- `id`: Einzigartige Kennung des Projekts
- `schema_version`: Version des Schemas

Beispieldatei für ein neues Projekt:
```yaml
id: "projekt-123"
schema_version: 1
# Füge weitere Attribute hinzu, wenn benötigt
```

### 2. Daten korrigieren oder aktualisieren

Wenn Daten in einem Projekt korrigiert wurden:

```yaml
_meta:
  confidence: verified
  last_reviewed: 2026-09-15
  reviewed_by: s.bauer
```

Die Roh-Coverage und die bestätigte Coverage werden überall getrennt berichtet (SPEC §11), damit das sichtbar ist.

### 3. Datenfeld hinzufügen oder ändern

Das Hinzufügen oder Ändern von Feldern geschieht in einer einzigen Datei:
`schema/project.schema.yaml`

```yaml
  jira_ticket:
    $ref: '#/$defs/text'      # Art des Werts
    x-tracked: true           # zählt zur Coverage %
    x-column: true            # erscheint im Bericht und in der Excel-Tabelle
```

Anschließend `./check.sh --check-schema` ausführen. Das ist die ganze Änderung – Validierung, Referenzintegrität, Coverage, die Tabellen, das Reportmodell und die Excel-Tabelle folgen daraus. Kein Python.

### 4. Projektdaten importieren

Um Projektdaten aus Legacy-Systemen zu importieren:

1. CSV-Dateien in `merge/input/` ablegen
2. Merge durchführen: 
   ```bash 
   ./merge/merge.sh --key "Projekt-Nr"
   ```
3. Ergebnis kopieren:
   ```bash
   cp merge/merged.csv import/merged.csv
   ```
4. Schema analysieren:
   ```bash
   ./import/import.sh profile
   ```
5. Mapping und Wertemap anpassen (in `mapping.yaml` und `value_map.yaml`)
6. Daten konvertieren:
   ```bash
   ./import/import.sh convert
   ```

### 5. Validierung durchführen

Die Validierung hat fünf Ebenen:

1. **Parse** – `ruamel.yaml` im Round-Trip-Modus
2. **Struktur** – JSON Schema draft 2020-12 mit `additionalProperties: false`
3. **Referenzial** – Taxonomie-Codes, Site/Team/Projekt-Referenzen, Duplikate, Abhängigkeitszyklen
4. **Plausibilität** – Warnungen, nie Fehler
5. **Vollständigkeit** – Coverage-Zahl, nie Urteil

Fehler melden Datei, Zeile und Lösung:

```
projects/payment-gateway.yaml:9:21
  classification_b: 'CB-7' ist nicht ein bekannter Code
  meintest du: CB-4
  gültig: CB-1, CB-2, CB-3, CB-4, unknown  (siehe schema/taxonomy.yaml, Gruppe 'classification_b')
  Um einen Code hinzuzufügen, füge ihn dieser Gruppe in taxonomy.yaml hinzu. Nichts anderes ändert sich.
```

## Tests

```bash
./verify.sh
```

`tests/fixtures/projects/` enthält 12 gültige Dateien mit den Randfällen (minimal, alle-unbekannt, alle-null, Umlaute, Multi-Environment, beide `placement`-Formate, unquoted-version-Falle). `tests/fixtures/invalid/` enthält ein Verzeichnis pro Fehlerfall, jedes mit `expect.txt`, das die Fehlercodes benennt, die gemeldet werden müssen. **Hinzufügen eines Fehlerfalls ist ein neues Verzeichnis, keine Python-Änderung.**

## Architektur

- **Git als Versionskontrolle**: Das einzige System für Datenänderungen
- **Einzelne YAML-Dateien**: Jedes Projekt ist eine separate Datei in `projects/`
- **Schema-basierte Validierung**: Alle Daten werden nach dem Schema validiert
- **Reproducibilität**: Alle Ausgaben sind deterministisch

## Wichtige Hinweise

### Bootstrapping (Einrichtung)
Die Import-Funktion ist ein **Bootstrap**, sie weigert sich, wenn `projects/` bereits Daten enthält. Danach wird `projects/` das System der Wahrheit, und die Extrakte sind Geschichte.

### Merge-Konventionen
Das Merge-Tool folgt den Konventionen im SPEC §5.4:
1. Erste Datei gewinnt pro Zelle
2. Dateien werden alphabetisch nach Dateinamen sortiert
3. Der Schlüsselwert (z.B. "Projekt-Nr") identifiziert Projekte
4. Leerwerte werden als "niemand hat beantwortet" betrachtet

### Stabilität
Projekt-IDs bleiben stabil über verschiedene Importe hinweg durch `import/id_map.csv`.

## Fehlerbehandlung

Die Pipeline weist auf folgende Probleme hin:
1. Ungültige YAML-Dateien
2. Strukturfehler im Schema
3. Referenzfehler (z.B. nicht existierende Sites/Teams)
4. Plausibilitätsprobleme (z.B. widersprüchliche Werte)
5. Coverage-Probleme

## Wartung

### Regelmäßige Überprüfungen:
1. `./check.sh` - tägliche Validierung
2. `./verify.sh` - vollständige Testlauf
3. `./report.sh` - täglicher Bericht

### Änderungsmanagement:
1. Alle Änderungen werden in Git protokolliert
2. Schema-Änderungen werden durch `check.sh --check-schema` validiert
3. Datenimporte sind einheitlich und reproduzierbar  