// report.typ - the PDF. SPEC 6.4, milestone M5.
//
// Typst, not LaTeX (image size and speed) and not WeasyPrint (this is a
// paginated management report, not a web page). pdf.py copies this file next
// to report_model.json and runs `typst compile`; the model is read here with
// `json()` and nothing else is read at all.
//
// This template lays out and computes nothing (SPEC 11). Every number, label,
// title and counting rule already exists in the model. There is no field
// name, no code and no enum value anywhere below (SPEC 6.1.1): the tables are
// whatever `data.tables` happens to hold today, and a table nobody has
// collected data for yet prints its own note.
//
// Air gap (SPEC 8.2): no `#import "@preview/..."`, ever. A package import
// makes `typst compile` reach for the registry, which inside the air gap
// means a failed daily report. Everything here is the standard library.

#let data = json("report_model.json")
#let lang = sys.inputs.at("lang", default: "de")

// Chrome, not taxonomy: the words around the numbers. The codes and their
// labels come from the model, and the ones with no label yet stay bare codes
// rather than invented words (SPEC 12.1).
#let chrome = (
  de: (
    as_of: "Stand", generated: "Erzeugt", git_sha: "Git-Commit",
    git_tag: "Git-Tag", schema_version: "Schema-Version",
    pipeline: "Pipeline-Version", image: "Image", projects: "Projekte",
    headline: "Kennzahlen", coverage: "Abdeckung",
    projects_total: "Projekte gesamt", fields_tracked: "Felder erfasst",
    field_coverage: "Feldabdeckung", verified_coverage: "davon verifiziert",
    team: "Team", verified: "verifiziert", tables: "Tabellen",
    code: "Code", label: "Bezeichnung", count: "Anzahl", total: "Summe",
    charts: "Diagramme", definitions: "Zählregeln", definition: "Zählregel",
    grain: "Grundgesamtheit", counts: "zählt", na: "Keine Daten",
    page: "Seite", of: "von", provenance: "Herkunft",
    appendix: "Anhang: Zählregeln", description: "Beschreibung",
  ),
  en: (
    as_of: "As of", generated: "Generated", git_sha: "Git commit",
    git_tag: "Git tag", schema_version: "Schema version",
    pipeline: "Pipeline version", image: "Image", projects: "Projects",
    headline: "Headline", coverage: "Coverage",
    projects_total: "Projects total", fields_tracked: "Fields tracked",
    field_coverage: "Field coverage", verified_coverage: "of which verified",
    team: "Team", verified: "Verified", tables: "Tables",
    code: "Code", label: "Label", count: "Count", total: "Total",
    charts: "Charts", definitions: "Counting rules", definition: "Counting rule",
    grain: "Grain", counts: "counts", na: "No data",
    page: "Page", of: "of", provenance: "Provenance",
    appendix: "Appendix: counting rules", description: "Description",
  ),
)
#let c = chrome.at(lang, default: chrome.de)

// The same ink as the charts, so a page does not look like two documents.
#let ink = rgb("#0b0b0b")
#let ink-muted = rgb("#52514e")
#let grid-line = rgb("#dcdcd8")
#let accent = rgb("#2a78d6")

// Liberation Sans is metric-compatible with the Arial the Excel uses and is
// installed explicitly in the image (SPEC 8.1). pdf.py asserts it resolves
// before compiling: Typst warns about an unknown family and substitutes
// silently, which is the trap in SPEC 8.2.
#let body-font = ("Liberation Sans", "DejaVu Sans")
#let mono-font = ("Liberation Mono", "DejaVu Sans Mono")

// `field` in the requested language, falling back to the other one. The same
// rule every renderer uses.
#let tr(item, field, default: "") = {
  for candidate in (field + "_" + lang, field + "_de", field + "_en", field) {
    let value = item.at(candidate, default: none)
    if value != none and value != "" { return value }
  }
  default
}

#let n(value) = if value == none { "n/a" } else { str(value) }

// `key` as a string, with `none` and "missing" treated the same way.
//
// A model key can be present and null - `git_tag` is, until a run is tagged,
// and `image_digest` is whenever the pipeline runs outside a container. `.at`
// hands back that null rather than its default, and `str(none)` is a compile
// error, so the whole report would fail on a field that is simply not filled
// in yet. Missing renders as n/a, never as a crash (SPEC 5.1).
#let s(item, key, default: "") = {
  let value = item.at(key, default: none)
  if value == none { default } else { str(value) }
}

#set document(
  title: tr(data.at("report", default: (:)), "title", default: "Report"),
  author: "mig pipeline",
  // `auto` is the compiler's creation timestamp, and pdf.py always pins that
  // to the model's own `generated_at` (--creation-timestamp). So the PDF
  // metadata says when the report was generated AND the same model compiles
  // to the same bytes (SPEC 5.2). Left unpinned, `auto` reads the clock and
  // two runs differ - which is what tests/test_pdf.py checks.
  date: auto,
)

#set page(
  paper: "a4",
  margin: (x: 18mm, y: 16mm),
  header: context {
    if counter(page).get().first() > 1 {
      set text(size: 8pt, fill: ink-muted)
      grid(
        columns: (1fr, auto),
        align: (left, right),
        tr(data.at("report", default: (:)), "title", default: ""),
        c.as_of + ": " + s(data, "as_of_date"),
      )
      line(length: 100%, stroke: 0.4pt + grid-line)
    }
  },
  footer: context {
    set text(size: 8pt, fill: ink-muted)
    grid(
      columns: (1fr, auto),
      align: (left, right),
      s(data.provenance, "git_sha").slice(0, calc.min(7,
        s(data.provenance, "git_sha").len())),
      c.page + " " + counter(page).display() + " " + c.of + " "
        + context str(counter(page).final().first()),
    )
  },
)

#set text(font: body-font, size: 9.5pt, fill: ink, lang: lang)
#set par(justify: false, leading: 0.62em)
#show heading: set text(fill: ink)
#show heading.where(level: 1): set text(size: 17pt)
#show heading.where(level: 2): set text(size: 12pt)
#show heading.where(level: 3): set text(size: 10pt)
#set heading(numbering: none)

#let key-value(rows) = table(
  columns: (auto, 1fr),
  column-gutter: 6mm,
  stroke: none,
  inset: (x: 0pt, y: 3pt),
  align: (left, left),
  ..rows.map(((k, v)) => (text(fill: ink-muted)[#k], text(fill: ink)[#v])).flatten()
)

// --------------------------------------------------------------------------
// Page 1 - title, provenance
// --------------------------------------------------------------------------

#v(30mm)
#block[
  #text(size: 28pt, weight: "bold")[
    #tr(data.at("report", default: (:)), "title", default: "Report")
  ]
]
#v(2mm)
#text(size: 13pt, fill: ink-muted)[
  #c.as_of: #s(data, "as_of_date")
]
#v(10mm)
#line(length: 100%, stroke: 0.8pt + accent)
#v(6mm)

#text(size: 10pt, weight: "bold")[#c.provenance]
#v(2mm)
#key-value((
  (c.generated, s(data, "generated_at")),
  (c.git_sha, raw(s(data.provenance, "git_sha"))),
  (c.git_tag, s(data.provenance, "git_tag", default: "-")),
  (c.schema_version, s(data.provenance, "schema_version")),
  (c.pipeline, s(data.provenance, "pipeline_version")),
  (c.image, raw(s(data.provenance, "image_digest", default: "-"))),
  (c.projects, s(data.provenance, "project_count", default: "n/a")),
))

// --------------------------------------------------------------------------
// Page 2 - the headline numbers and coverage
// --------------------------------------------------------------------------

#pagebreak()
= #c.headline

#let headline = data.at("headline", default: ())
#grid(
  columns: (1fr,) * calc.min(3, calc.max(1, headline.len())),
  gutter: 5mm,
  ..headline.map(item => block(
    width: 100%,
    inset: 5mm,
    radius: 1mm,
    fill: rgb("#f5f5f3"),
    [
      #text(size: 26pt, weight: "bold")[#n(item.at("value", default: none))]
      #linebreak()
      #text(size: 10pt)[#tr(item, "label", default: item.at("key", default: ""))]
      #linebreak()
      #text(size: 7pt, fill: ink-muted)[#item.at("definition", default: "")]
    ],
  ))
)

#v(8mm)
== #c.coverage

// Raw coverage and verified coverage are two numbers, never merged into one
// (SPEC 11). An import gives 90% raw coverage that nobody has looked at.
#let cov = data.at("coverage", default: (:))
#key-value((
  (c.projects_total, n(cov.at("projects_total", default: none))),
  (c.fields_tracked, n(cov.at("fields_tracked", default: none))),
  (c.field_coverage, n(cov.at("field_coverage_pct", default: none)) + " %"),
  (c.verified_coverage, n(cov.at("verified_coverage_pct", default: none)) + " %"),
))

#v(4mm)
#table(
  columns: (1fr, auto, auto, auto),
  align: (left, right, right, right),
  stroke: (x, y) => if y == 0 { (bottom: 0.6pt + ink) } else { (bottom: 0.3pt + grid-line) },
  inset: (x: 3pt, y: 4pt),
  table.header(
    text(weight: "bold")[#c.team],
    text(weight: "bold")[#c.projects],
    text(weight: "bold")[#c.coverage],
    text(weight: "bold")[#c.verified],
  ),
  ..cov.at("by_team", default: ()).map(team => (
    s(team, "team_id"),
    n(team.at("projects", default: none)),
    n(team.at("coverage_pct", default: none)) + " %",
    n(team.at("verified_pct", default: none)) + " %",
  )).flatten()
)

// --------------------------------------------------------------------------
// Charts - rendered once by charts.py and embedded here (SPEC 2)
// --------------------------------------------------------------------------

#let charts = data.at("charts", default: ())
#if charts.len() > 0 {
  pagebreak()
  heading(level: 1)[#c.charts]
  for chart in charts {
    // No heading here: charts.py draws the title, the counting rule and the
    // coverage into the PNG itself, so the chart carries its own caption and
    // printing it again above the image would say everything twice.
    block(breakable: false)[
      #image(s(chart, "path"), width: 100%)
      #v(5mm)
    ]
  }
}

// --------------------------------------------------------------------------
// Tables - every table in the model, in the model's own order
// --------------------------------------------------------------------------

#let tables = data.at("tables", default: (:))

// A distribution: code, label, count. `unknown` is a row like any other and
// is never dropped (SPEC 11).
#let distribution(table-data) = {
  let columns = table-data.at("columns", default: ())
  let first = if columns.len() > 0 { columns.at(0).key } else { "" }
  table(
    columns: (auto, 1fr, auto),
    align: (left, left, right),
    stroke: (x, y) => if y == 0 { (bottom: 0.6pt + ink) } else { (bottom: 0.3pt + grid-line) },
    inset: (x: 3pt, y: 3.5pt),
    table.header(
      text(weight: "bold")[#c.code],
      text(weight: "bold")[#c.label],
      text(weight: "bold")[#c.count],
    ),
    ..table-data.at("rows", default: ()).map(record => {
      let code = s(record, first)
      // The label if the taxonomy has one, the bare code if it does not
      // (SPEC 12.1). The model already made that choice; this reads it.
      let label = record.at(first + "_label_" + lang, default: none)
      (
        raw(code),
        if label == none or str(label) == code { "" } else { str(label) },
        n(record.at("value", default: none)),
      )
    }).flatten()
  )
}

// A cross-tab keeps its shape: the row code, one column per code, then the
// row total the model computed.
#let cross-tab(table-data) = {
  let columns = table-data.at("columns", default: ())
  let first = if columns.len() > 0 { columns.at(0).key } else { "" }
  let rest = columns.slice(calc.min(1, columns.len()))
  table(
    columns: (auto,) + (1fr,) * rest.len(),
    align: (left,) + (right,) * rest.len(),
    stroke: (x, y) => if y == 0 { (bottom: 0.6pt + ink) } else { (bottom: 0.3pt + grid-line) },
    inset: (x: 3pt, y: 3.5pt),
    table.header(
      text(weight: "bold")[#c.code],
      ..rest.map(column => text(weight: "bold", size: 8pt)[
        #tr(column, "label", default: column.key)
      ]),
    ),
    ..table-data.at("rows", default: ()).map(record => {
      let code = s(record, first)
      let label = record.at(first + "_label_" + lang, default: none)
      let head = if label == none or str(label) == code { raw(code) } else [#raw(code) #str(label)]
      (head,) + rest.map(column => n(record.at(column.key, default: none)))
    }).flatten()
  )
}

#pagebreak()
= #c.tables

#for key in tables.keys() {
  let table-data = tables.at(key)
  block(breakable: false, width: 100%)[
    #heading(level: 2)[#tr(table-data, "title", default: key)]
    #text(size: 7.5pt, fill: ink-muted)[
      // SPEC 7: a number and the rule it was counted under never travel
      // separately.
      #key #sym.bar.v #s(table-data, "definition")
      #sym.bar.v #s(table-data, "grain")/#s(table-data, "counts")
    ]
    #v(2mm)
    #if not table-data.at("available", default: false) [
      // n/a, not a crash and not a zero (SPEC 6.3).
      #block(inset: 4mm, fill: rgb("#f5f5f3"), width: 100%)[
        #text(fill: ink-muted)[*#c.na.* #tr(table-data, "note")]
      ]
    ] else if table-data.at("kind", default: "") == "cross_tab" [
      #cross-tab(table-data)
    ] else [
      #distribution(table-data)
    ]
    #v(6mm)
  ]
}

// --------------------------------------------------------------------------
// Appendix - the counting rules, in full (SPEC 7)
// --------------------------------------------------------------------------

#pagebreak()
= #c.appendix

#table(
  columns: (auto, auto, 1fr),
  align: (left, left, left),
  stroke: (x, y) => if y == 0 { (bottom: 0.6pt + ink) } else { (bottom: 0.3pt + grid-line) },
  inset: (x: 3pt, y: 4pt),
  table.header(
    text(weight: "bold")[#c.definition],
    text(weight: "bold")[#c.grain / #c.counts],
    text(weight: "bold")[#c.description],
  ),
  ..data.at("definitions", default: ()).map(definition => (
    raw(s(definition, "key")),
    text(size: 8pt, fill: ink-muted)[
      #s(definition, "grain")/#s(definition, "counts")
    ],
    tr(definition, "text"),
  )).flatten()
)
