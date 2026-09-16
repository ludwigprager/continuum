"""tests/test_editor.py - formgen/store unit tests, plus one integration test
across the seam between the HTML the form renders and the names formgen.py
decodes back - the only place that checks the two actually agree with each
other, the same reason test_validate.py has an end-to-end case (SPEC 10)."""

from __future__ import annotations

import importlib.util
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import pytest
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

import formgen
import store
import validate

REPO = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO / "schema"


def _schema():
    root, taxonomy, references, _ = validate.load_schema_dir(SCHEMA_DIR)
    return validate.Schema(root), taxonomy, references


# --------------------------------------------------------------------------
# formgen: schema -> sections. Against the real schema, not a stub, so a
# schema edit that breaks the form generator is caught here too.
# --------------------------------------------------------------------------

def test_build_sections_covers_known_fields():
    schema, taxonomy, references = _schema()
    sections = formgen.build_sections(schema, taxonomy, references, ["payment-gateway"])
    by_key = {s.key: s for s in sections}
    assert "ownership" in by_key
    assert "datastores" in by_key

    team_field = next(f for f in formgen.flatten(by_key["ownership"].items)
                       if f.path == ("ownership", "team_id"))
    assert team_field.kind == "code"
    labels = dict(team_field.options)
    assert "Team Alpha" in labels["team-alpha"]

    group = by_key["datastores"].items[0]
    assert isinstance(group, formgen.Group)
    assert {f.path[-1] for f in group.fields} >= {"engine", "version", "role"}


def test_id_schema_version_and_unmapped_are_not_generic_fields():
    schema, taxonomy, references = _schema()
    sections = formgen.build_sections(schema, taxonomy, references, [])
    assert {s.key for s in sections}.isdisjoint({"id", "schema_version", "_unmapped"})


def test_null_label_prints_bare_code():
    # classification_a's codes are all label_de: null (SPEC 12.1) - the
    # dropdown must show the bare code, never an invented word.
    schema, taxonomy, references = _schema()
    sections = formgen.build_sections(schema, taxonomy, references, [])
    classification = next(s for s in sections if s.key == "classification")
    field_a = next(f for f in formgen.flatten(classification.items)
                   if f.path == ("classification", "classification_a"))
    assert dict(field_a.options)["CA-1"] == "CA-1"


# --------------------------------------------------------------------------
# formgen: submitted form -> nested dict, and back
# --------------------------------------------------------------------------

def test_decode_scalar_multi_and_group_row_drops_blank_row():
    schema, taxonomy, references = _schema()
    sections = formgen.build_sections(schema, taxonomy, references, [])
    fields = {
        "ownership.team_id": ["team-alpha"],
        "platform.storage_classes[]": ["block", "object"],
        "datastores[0].engine": ["db2"],
        "datastores[0].version": ["11.5"],
        "datastores[1].engine": [""],  # an added-then-untouched row
    }
    decoded = formgen.decode_form(fields, sections)
    doc: dict = {}
    formgen.apply_form(doc, decoded)

    assert doc["ownership"]["team_id"] == "team-alpha"
    assert doc["platform"]["storage_classes"] == ["block", "object"]
    assert doc["datastores"] == [{"engine": "db2", "version": "11.5"}]


def test_decode_quotes_ambiguous_scalars():
    # SPEC 9: an unquoted 7.9 becomes a float the next time anything reads
    # this file back. A version field must come out of the form still quoted.
    schema, taxonomy, references = _schema()
    sections = formgen.build_sections(schema, taxonomy, references, [])
    decoded = formgen.decode_form({"datastores[0].version": ["7.9"]}, sections)
    value = decoded["datastores"][0]["version"]
    assert value == "7.9"
    assert isinstance(value, DoubleQuotedScalarString)


def test_decode_count_becomes_a_real_int_or_stays_unknown():
    schema, taxonomy, references = _schema()
    sections = formgen.build_sections(schema, taxonomy, references, [])
    numeric = formgen.decode_form({"platform.cpu_cores": ["16"]}, sections)
    assert numeric["platform"]["cpu_cores"] == 16
    assert isinstance(numeric["platform"]["cpu_cores"], int)

    unknown = formgen.decode_form({"platform.cpu_cores": ["unknown"]}, sections)
    assert unknown["platform"]["cpu_cores"] == "unknown"


def test_prune_drops_nulls_and_empty_containers():
    assert formgen.prune({"a": None, "b": {"c": None}, "d": []}) is None
    assert formgen.prune({"a": None, "b": "kept"}) == {"b": "kept"}


# --------------------------------------------------------------------------
# store: id allocation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Kundenportal", "kundenportal"),
    ("Zahlungs-Gateway für Österreich", "zahlungs-gateway-fuer-oesterreich"),
    ("!!!", "project"),
])
def test_slugify(text, expected):
    assert store.slugify(text) == expected


def test_unique_id_deduplicates():
    existing = {"foo", "foo-2"}
    assert store.unique_id("foo", existing) == "foo-3"
    assert store.unique_id("bar", existing) == "bar"


# --------------------------------------------------------------------------
# Integration: the running server, across the encode/decode seam
# --------------------------------------------------------------------------

def _load_app():
    path = REPO / "tools" / "editor" / "app.py"
    spec = importlib.util.spec_from_file_location("continuum_editor_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def running_server(tmp_path):
    app = _load_app()
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    app.Handler.ctx = app.Context(projects_dir, SCHEMA_DIR, REPO / "templates" / "editor")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd, projects_dir, app
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def test_create_edit_save_round_trip(running_server):
    httpd, projects_dir, _app = running_server
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    body = urlencode({"name": "Kundenportal Test"}).encode()
    with urllib.request.urlopen(f"{base}/projects", data=body) as resp:
        assert resp.status == 200  # followed the 303 to the edit page
        pid = resp.geturl().rsplit("/", 2)[-2]
    assert pid == "kundenportal-test"

    save_body = urlencode({
        "name": "Kundenportal Test",
        "ownership.team_id": "team-alpha",
        "datastores[0].engine": "db2",
        "datastores[0].version": "11.5",
    }).encode()
    with urllib.request.urlopen(f"{base}/projects/{pid}/edit", data=save_body) as resp:
        assert resp.status == 200
        page = resp.read().decode()
    assert "gespeichert" in page

    doc = store.load(projects_dir, pid)
    assert doc["id"] == pid
    assert doc["ownership"]["team_id"] == "team-alpha"
    assert doc["datastores"][0]["engine"] == "db2"

    findings, _coverage = validate.validate(projects_dir, SCHEMA_DIR)
    assert [f for f in findings if f.level == "error"] == []


def test_create_writes_exactly_one_file(running_server):
    """No git call, no write anywhere else - creating a project touches
    exactly the one YAML file it creates (SPEC: this tool only writes local
    YAML; nothing here ever calls git)."""
    httpd, projects_dir, _app = running_server
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    body = urlencode({"name": "Isolation Test"}).encode()
    with urllib.request.urlopen(f"{base}/projects", data=body):
        pass

    assert [p.name for p in projects_dir.iterdir()] == ["isolation-test.yaml"]


# --------------------------------------------------------------------------
# "Generate report" route. reportgen.generate() itself is exercised for real
# (it just sequences the same tools ./report.sh does - see reportgen.py);
# what is worth testing at the HTTP layer is the wiring around it: the route
# calls it, shows success or failure, links to the report server when it
# knows about one, and refuses a second run while one is in flight. All
# three monkeypatch reportgen.generate so none of them pay for a real PDF/
# deck render, and none of them touch out/reports/<today> - the real thing
# ./report.sh (or a real click of the button) would write to.
# --------------------------------------------------------------------------

def test_generate_report_links_to_serve_on_success(running_server, monkeypatch):
    httpd, _projects_dir, app = running_server
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    app.Handler.ctx.serve_port = 9000
    monkeypatch.setattr(app.reportgen, "generate", lambda *a, **k: app.reportgen.Result(
        date="2026-01-01", ok=True, report_dir="out/reports/2026-01-01"))

    req = urllib.request.Request(f"{base}/reports/generate", data=b"", method="POST")
    with urllib.request.urlopen(req) as resp:
        page = resp.read().decode()

    assert "Bericht erzeugt" in page
    assert "http://127.0.0.1:9000/2026-01-01/" in page


def test_generate_report_shows_failing_step_output(running_server, monkeypatch):
    httpd, _projects_dir, app = running_server
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setattr(app.reportgen, "generate", lambda *a, **k: app.reportgen.Result(
        date="2026-01-01", ok=False, report_dir="",
        steps=[app.reportgen.Step("build_model", False, "boom: schema missing")]))

    req = urllib.request.Request(f"{base}/reports/generate", data=b"", method="POST")
    with urllib.request.urlopen(req) as resp:
        page = resp.read().decode()

    assert "fehlgeschlagen" in page
    assert "build_model" in page
    assert "boom: schema missing" in page


def test_generate_report_refuses_a_second_run_while_one_is_in_flight(running_server):
    httpd, _projects_dir, app = running_server
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    app.Handler.ctx.report_lock.acquire()
    try:
        req = urllib.request.Request(f"{base}/reports/generate", data=b"", method="POST")
        with urllib.request.urlopen(req) as resp:
            page = resp.read().decode()
        assert "already being generated" in page
    finally:
        app.Handler.ctx.report_lock.release()


# --------------------------------------------------------------------------
# reportgen.generate() itself: real snapshot/build_model/renderer flags, but
# with _load faked out so the assertion is about sequencing - stop at the
# first failing step, report every step that actually ran - not about
# whether Typst or python-pptx work (verify.sh's full-pipeline step already
# proves that, through report.sh).
# --------------------------------------------------------------------------

def test_reportgen_stops_at_the_first_failing_step(monkeypatch, tmp_path):
    import reportgen

    ran = []

    def fake_load(name, _path):
        def main(_argv, _name=name):
            ran.append(_name)
            return 0 if _name != "build_model" else 2
        return type("FakeModule", (), {"main": staticmethod(main)})

    monkeypatch.setattr(reportgen, "_load", fake_load)
    date = "reportgen-unit-test"
    report_dir = reportgen.REPO_ROOT / "out" / "reports" / date
    try:
        result = reportgen.generate(tmp_path, SCHEMA_DIR, date)
    finally:
        if report_dir.is_dir():
            import shutil
            shutil.rmtree(report_dir)

    assert ran == ["snapshot", "build_model"]  # never reached the renderers
    assert result.ok is False
    assert [s.name for s in result.steps] == ["snapshot", "build_model"]
    assert result.steps[0].ok is True
    assert result.steps[1].ok is False
