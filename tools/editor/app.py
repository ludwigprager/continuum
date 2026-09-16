#!/usr/bin/env python3
"""
app.py - the project editor. SPEC 6.6, milestone M7.

    tools/editor/app.py [--projects DIR] [--schema DIR] [--port N] [--bind ADDR]

A generic, schema-driven form for creating and editing projects/*.yaml,
built for one specific case (SPEC 2): a team member who cannot use git or
the CLI at all. It only ever writes local YAML - it never runs git, and
there is no auth, matching serve.sh's existing no-auth / trusted-network
precedent (see edit.sh for the bind default, which defaults to loopback
unlike serve.sh, because this one is read-write).

Contains no field names, codes or enum values of its own (SPEC 6.1.1): the
form is built from schema/project.schema.yaml by formgen.py, which walks it
through the same validate.Schema primitives validate.py itself uses.

Saving is unconditional. validate.validate() runs afterwards and the
findings for the edited file are shown inline, but nothing here ever
refuses to write (SPEC 2: incomplete never fails validation) - ./check.sh
and the pre-commit hook stay the one real gate.

Exit codes: 0 normal shutdown (Ctrl-C), 2 tool/usage error.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

EDITOR_DIR = Path(__file__).resolve().parent
TOOLS_DIR = EDITOR_DIR.parent
for p in (TOOLS_DIR, EDITOR_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate  # noqa: E402
import formgen  # noqa: E402
import store  # noqa: E402

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError:  # pragma: no cover
    sys.exit("jinja2 is required (it is in the pipeline image): exit 2")

EXIT_OK, EXIT_TOOL = 0, 2
DEFAULT_TEMPLATE_DIR = Path("templates/editor")


def make_env(template_dir: Path) -> Environment:
    return Environment(loader=FileSystemLoader(str(template_dir)),
                        autoescape=True, undefined=StrictUndefined)


class Context:
    """Built once at startup, read on every request. The schema is re-read
    per request rather than cached: it is a moving target (SPEC 1) and this
    tool is low-traffic enough that re-parsing a few small YAML files on
    every request is not worth a cache-invalidation story."""

    def __init__(self, projects_dir: Path, schema_dir: Path, template_dir: Path):
        self.projects_dir = projects_dir
        self.schema_dir = schema_dir
        self.env = make_env(template_dir)
        self.lock = threading.Lock()  # one write at a time; single-user tool

    def sections(self) -> list:
        root, taxonomy, references, _ = validate.load_schema_dir(self.schema_dir)
        schema = validate.Schema(root)
        project_ids = sorted(store.existing_ids(self.projects_dir))
        return formgen.build_sections(schema, taxonomy, references, project_ids)

    def findings_for(self, rel_path: str) -> list:
        findings, _coverage = validate.validate(self.projects_dir, self.schema_dir)
        return [f for f in findings if f.file == rel_path]


ROUTES: list[tuple[str, str, str]] = [
    ("GET", r"^/$", "list_projects"),
    ("GET", r"^/projects/new$", "new_project_form"),
    ("POST", r"^/projects$", "create_project"),
    ("GET", r"^/projects/(?P<pid>[a-z0-9][a-z0-9-]*)/edit$", "edit_form"),
    ("POST", r"^/projects/(?P<pid>[a-z0-9][a-z0-9-]*)/edit$", "save_project"),
]
COMPILED_ROUTES = [(m, re.compile(p), h) for m, p, h in ROUTES]


class Handler(BaseHTTPRequestHandler):
    ctx: Context  # set on the class before serving

    def log_message(self, fmt, *args):
        sys.stderr.write("edit.sh: " + (fmt % args) + "\n")

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def dispatch(self, method: str):
        path = urlsplit(self.path).path
        for verb, pattern, handler_name in COMPILED_ROUTES:
            if verb != method:
                continue
            match = pattern.match(path)
            if match:
                try:
                    getattr(self, handler_name)(**match.groupdict())
                except Exception:
                    traceback.print_exc(file=sys.stderr)
                    self.respond_html(500, "<h1>Internal error</h1><pre>"
                                       + html.escape(traceback.format_exc()) + "</pre>")
                return
        self.respond_html(404, "<h1>Not found</h1>")

    # -- helpers --------------------------------------------------------

    def render(self, template: str, **ctx):
        page = self.ctx.env.get_template(template).render(**ctx)
        self.respond_html(200, page)

    def respond_html(self, status: int, body: str):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location: str):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def form_body(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return parse_qs(raw, keep_blank_values=True)

    # -- routes -----------------------------------------------------------

    def list_projects(self):
        self.render("list.html.j2", projects=store.list_projects(self.ctx.projects_dir))

    def new_project_form(self):
        self.render("new.html.j2")

    def create_project(self):
        fields = self.form_body()
        name = (fields.get("name") or [""])[0]
        requested_id = (fields.get("id") or [""])[0]
        with self.ctx.lock:
            pid = store.create(self.ctx.projects_dir, name, requested_id)
        self.redirect(f"/projects/{pid}/edit")

    def _render_edit(self, pid: str, doc, saved: bool):
        sections = self.ctx.sections()
        path = store.project_path(self.ctx.projects_dir, pid)
        findings = self.ctx.findings_for(validate.relative_path(path)) if saved else []
        self.render("form.html.j2", pid=pid,
                    sections=formgen.view_sections(sections, doc),
                    unmapped=doc.get("_unmapped"), findings=findings, saved=saved)

    def edit_form(self, pid: str):
        if not store.project_path(self.ctx.projects_dir, pid).exists():
            self.respond_html(404, f"<h1>No such project: {html.escape(pid)}</h1>")
            return
        doc = store.load(self.ctx.projects_dir, pid)
        self._render_edit(pid, doc, saved=False)

    def save_project(self, pid: str):
        if not store.project_path(self.ctx.projects_dir, pid).exists():
            self.respond_html(404, f"<h1>No such project: {html.escape(pid)}</h1>")
            return
        fields = self.form_body()
        with self.ctx.lock:
            sections = self.ctx.sections()
            decoded = formgen.decode_form(fields, sections)
            doc = store.load(self.ctx.projects_dir, pid)
            formgen.apply_form(doc, decoded)
            store.save(self.ctx.projects_dir, pid, doc)
        self._render_edit(pid, doc, saved=True)


def serve(projects_dir: Path, schema_dir: Path, template_dir: Path,
          host: str, port: int) -> int:
    Handler.ctx = Context(projects_dir, schema_dir, template_dir)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"editor listening on http://{host}:{port}/  (Ctrl-C to stop)", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects", type=Path, default=Path("projects"))
    parser.add_argument("--schema", type=Path, default=Path("schema"))
    parser.add_argument("--templates", type=Path, default=DEFAULT_TEMPLATE_DIR)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args(argv)

    if not args.schema.exists():
        print(f"edit: --schema {args.schema} does not exist", file=sys.stderr)
        return EXIT_TOOL
    if not args.templates.exists():
        print(f"edit: --templates {args.templates} does not exist", file=sys.stderr)
        return EXIT_TOOL
    args.projects.mkdir(parents=True, exist_ok=True)
    return serve(args.projects, args.schema, args.templates, args.bind, args.port)


if __name__ == "__main__":
    sys.exit(main())
