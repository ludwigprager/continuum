# Import report: projekte.xlsx

- generated: 2026-09-14T15:14:53
- sheet: `Projektübersicht`
- rows read: 20
- project files written: 20
- empty rows skipped: 0
- columns mapped: 17/18

## Field coverage (how many projects have a value)

| Target field | Projects | Coverage |
|---|---|---|
| `anwendungsname` | 20 | 100% |
| `projekt_nr` | 20 | 100% |
| `betriebssystem` | 19 | 95% |
| `klassifizierung_a` | 19 | 95% |
| `klassifizierung_b` | 19 | 95% |
| `migrationsstrategie` | 19 | 95% |
| `sicherheitsklasse` | 19 | 95% |
| `standort_rz` | 19 | 95% |
| `storage_klasse` | 19 | 95% |
| `team` | 19 | 95% |
| `datenbank` | 18 | 90% |
| `cpu_kerne` | 16 | 80% |
| `ansprechpartner` | 15 | 75% |
| `status` | 15 | 75% |
| `betriebssystem_eol` | 14 | 70% |
| `storage_gb` | 13 | 65% |
| `bemerkung` | 10 | 50% |

## Unmapped columns (currently in `_unmapped`)

| Column | Non-empty values | Kind |
|---|---|---|
| Altspalte | 0 | empty |

Promote the ones near the top of this list into `mapping.yaml` next.

## Values that passed through unmapped

Values seen in the data but not listed in `value_map.yaml` are written
through unchanged. Check `profile.md` for the full distinct-value lists.

## Reminder

Every field here is `confidence: imported`, which means nobody has verified it.
Report *verified* coverage separately from raw coverage; the gap between the
two is the real remaining work.
