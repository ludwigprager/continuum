"""
store.py - reading and writing projects/*.yaml for the editor.

Reuses validate.make_loader() so an edit preserves comments and key order on
an existing file (ruamel round-trip), and validate.project_files() /
validate.relative_path() so "what counts as a project file" never disagrees
with ./check.sh. This file adds no parsing of its own.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import validate  # noqa: E402  (path set up above)

UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                          "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})


def slugify(text: str) -> str:
    """Free text -> a legal project id (schema pattern ^[a-z0-9][a-z0-9-]*$).

    A small local copy of the idea in import/import_csv.py's slugify/make_id,
    not imported from there: reaching into import/ from the editor would
    break the deliberate self-containment of the merge/import steps
    (CLAUDE.md says not to modify import_csv.py, and not to lean on it
    either). A handful of similar lines is cheaper than that coupling.
    """
    text = (text or "").translate(UMLAUTS)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text or "project"


def unique_id(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def existing_ids(projects_dir: Path) -> set[str]:
    return {p.stem for p in validate.project_files(projects_dir)}


def project_path(projects_dir: Path, project_id: str) -> Path:
    return projects_dir / f"{project_id}.yaml"


def list_projects(projects_dir: Path) -> list[dict]:
    out = []
    for path in validate.project_files(projects_dir):
        doc, _errors = validate.parse_file(path, validate.relative_path(path))
        if doc is None:
            out.append({"id": path.stem, "name": "(unreadable)",
                        "team_id": "unknown", "migration_status": "unknown"})
            continue
        out.append({
            "id": str(doc.get("id") or path.stem),
            "name": str(doc.get("name") or ""),
            "team_id": str(validate.dig(doc, "ownership.team_id") or "unknown"),
            "migration_status": str(validate.dig(doc, "migration.status") or "unknown"),
        })
    return sorted(out, key=lambda p: p["id"])


def load(projects_dir: Path, project_id: str) -> Any:
    path = project_path(projects_dir, project_id)
    loader = validate.make_loader()
    with path.open(encoding="utf-8") as handle:
        return loader.load(handle)


def save(projects_dir: Path, project_id: str, doc: Any) -> None:
    path = project_path(projects_dir, project_id)
    loader = validate.make_loader()
    with path.open("w", encoding="utf-8") as handle:
        loader.dump(doc, handle)


def create(projects_dir: Path, name: str, requested_id: str) -> str:
    """Reserve an id and write the minimal legal file. Returns the id.

    Writes immediately (rather than only on first Save) so the id is
    reserved as soon as it's shown to the user - a race between two people
    typing the same name is the only cost, and this tool is for one
    consultant, not a multi-writer service.
    """
    existing = existing_ids(projects_dir)
    base = slugify(requested_id or name)
    project_id = unique_id(base, existing)
    loader = validate.make_loader()
    doc = loader.load(f"schema_version: 1\nid: {project_id}\n")
    if name:
        doc["name"] = name
    save(projects_dir, project_id, doc)
    return project_id
