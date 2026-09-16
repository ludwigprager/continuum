"""
formgen.py - schema/project.schema.yaml -> a form field tree, and back.

Uses validate.Schema.resolve() for every bit of $ref/$defs/annotation
resolution, the same primitive validate.py itself uses to walk the schema
(SPEC 6.1.1: "no field names, no codes, no enum values" in code). This file
knows the shape of a $def kind (text/code/count/date/version) and how
x-taxonomy/x-ref pick a dropdown's options - it does not know any field name.

Two directions:
    build_sections()  schema -> list[Section] for rendering the form
    decode_form()     submitted form fields -> a nested dict to merge into
                       the project doc

The field-name convention that ties them together:
    ownership.team_id                  scalar, dotted path
    platform.storage_classes[]         array-of-scalar-code (checkboxes)
    datastores[0].engine               array-of-object, indexed row
    placement.environments[0].os.family  array-of-object, nested object
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any

from ruamel.yaml.scalarstring import DoubleQuotedScalarString as DQ

# Top-level properties handled outside the generic form: id/schema_version
# are fixed once a file exists (renaming means renaming the file too, which
# this tool does not do), and _unmapped's shape is arbitrary by design
# (SPEC 3.4) - there is nothing generic to build a widget for.
SKIP_TOP_LEVEL = {"schema_version", "id", "_unmapped"}


@dataclass
class Field:
    path: tuple
    label: str
    kind: str                                  # text | code | count | date | version
    widget: str                                # text | number | select | checkboxes
    options: list[tuple[str, str]] = dc_field(default_factory=list)
    multi: bool = False


@dataclass
class Group:
    path: tuple
    label: str
    fields: list  # list[Field] - one row's worth, rendered repeatably


@dataclass
class Section:
    key: str
    label: str
    items: list  # list[Field | Group]


def humanize(key: str) -> str:
    return key.replace("_", " ")


def option_label(code: str, labels: dict | None) -> str:
    # A code with no label prints bare, never an invented word (SPEC 12.1) -
    # the same rule every renderer already follows.
    de = (labels or {}).get("label_de")
    return f"{code} — {de}" if de else code


def build_options(annots: dict, taxonomy: dict, references: dict,
                   project_ids: list[str]) -> list[tuple[str, str]]:
    group = annots.get("x-taxonomy")
    if group:
        codes = taxonomy.get(group, {})
        return [(code, option_label(code, labels)) for code, labels in codes.items()]
    ref = annots.get("x-ref")
    if ref == "projects":
        return [(pid, pid) for pid in project_ids]
    if ref:
        entries = (references.get(ref) or {}).get("entries") or {}
        return [(code, option_label(code, labels)) for code, labels in entries.items()]
    return []


def _annots(resolved: dict) -> dict:
    return {k: v for k, v in resolved.items() if k.startswith("x-")}


def build_node(schema, node, path, taxonomy, references, project_ids) -> list:
    resolved = schema.resolve(node)
    if not resolved:
        return []
    annots = _annots(resolved)
    props = resolved.get("properties")
    items = resolved.get("items")

    if annots.get("x-kind"):
        kind = annots["x-kind"]
        options = build_options(annots, taxonomy, references, project_ids)
        widget = "select" if options else ("number" if kind == "count" else "text")
        return [Field(path=path, label=humanize(path[-1]), kind=kind,
                      widget=widget, options=options)]

    if isinstance(props, dict):
        out = []
        for key, child in props.items():
            out.extend(build_node(schema, child, path + (key,), taxonomy, references, project_ids))
        return out

    if isinstance(items, dict):
        item_resolved = schema.resolve(items)
        item_annots = _annots(item_resolved)
        item_props = item_resolved.get("properties")
        if item_annots.get("x-kind") and not isinstance(item_props, dict):
            kind = item_annots["x-kind"]
            options = build_options(item_annots, taxonomy, references, project_ids)
            return [Field(path=path, label=humanize(path[-1]), kind=kind,
                          widget="checkboxes" if options else "text",
                          options=options, multi=True)]
        if isinstance(item_props, dict):
            subfields = build_node(schema, items, path + ("[]",), taxonomy, references, project_ids)
            return [Group(path=path, label=humanize(path[-1]), fields=subfields)]
    return []


def build_sections(schema, taxonomy, references, project_ids) -> list:
    root = schema.resolve(schema.root)
    sections = []
    for key, child in (root.get("properties") or {}).items():
        if key in SKIP_TOP_LEVEL:
            continue
        items = build_node(schema, child, (key,), taxonomy, references, project_ids)
        if items:
            sections.append(Section(key=key, label=humanize(key), items=items))
    return sections


def flatten(items: list) -> list:
    """Every Field reachable from a section list, Groups included."""
    out = []
    for item in items:
        if isinstance(item, Group):
            out.extend(flatten(item.fields))
        else:
            out.append(item)
    return out


# --------------------------------------------------------------------------
# Field <-> HTML name
# --------------------------------------------------------------------------

def field_name(path: tuple, index=None) -> str:
    """('datastores', '[]', 'engine') -> 'datastores[N].engine' (or a real index)."""
    marker = "N" if index is None else str(index)
    parts: list[str] = []
    for seg in path:
        if seg == "[]":
            if parts:
                parts[-1] = parts[-1] + f"[{marker}]"
            else:
                parts.append(f"[{marker}]")
        else:
            parts.append(f".{seg}" if parts else seg)
    return "".join(parts)


def template_key(f: Field) -> str:
    name = field_name(f.path)
    return name + "[]" if f.multi else name


def kind_by_key(sections: list) -> dict[str, str]:
    return {template_key(f): f.kind for f in flatten([i for s in sections for i in s.items])}


# --------------------------------------------------------------------------
# Rendering: schema + current doc -> plain dicts the template can iterate
# without doing any path arithmetic of its own.
# --------------------------------------------------------------------------

def value_at(container: Any, path: tuple) -> Any:
    node = container
    for seg in path:
        if not isinstance(node, dict):
            return None
        node = node.get(seg)
        if node is None:
            return None
    return node


def rows_at(doc: dict, path: tuple) -> list:
    node = value_at(doc, path)
    return node if isinstance(node, list) else []


def row_relative(path: tuple) -> tuple:
    idx = path.index("[]")
    return path[idx + 1:]


def _leaf_view(f: Field, value: Any, index=None) -> dict:
    name = field_name(f.path, index=index)
    if f.multi:
        name += "[]"
        selected = set(value or [])
        if f.options:
            opts = [{"code": c, "label": lbl, "checked": c in selected} for c, lbl in f.options]
            return {"type": "checkboxes", "label": f.label, "name": name, "options": opts}
        return {"type": "text", "label": f.label, "name": name,
                "value": ", ".join(value or [])}
    if f.options:
        return {"type": "select", "label": f.label, "name": name,
                "value": "" if value is None else str(value), "options": f.options}
    widget = "number" if f.kind == "count" else "text"
    return {"type": widget, "label": f.label, "name": name,
            "value": "" if value is None else str(value)}


def view_sections(sections: list, doc: dict) -> list[dict]:
    out = []
    for section in sections:
        items = []
        for item in section.items:
            if isinstance(item, Group):
                rows = rows_at(doc, item.path)
                row_views = [
                    {"fields": [_leaf_view(f, value_at(row, row_relative(f.path)), index=i)
                               for f in item.fields]}
                    for i, row in enumerate(rows)
                ]
                template_row = [_leaf_view(f, None, index="__IDX__") for f in item.fields]
                dom_id = "-".join(str(p) for p in item.path if p != "[]")
                items.append({"type": "group", "label": item.label, "dom_id": dom_id,
                             "rows": row_views, "template_row": template_row})
            else:
                items.append(_leaf_view(item, value_at(doc, item.path)))
        # "elements", not "items": a plain dict's own .items() method would
        # shadow a same-named key under Jinja's attribute-then-item lookup.
        out.append({"key": section.key, "label": section.label, "elements": items})
    return out


# --------------------------------------------------------------------------
# Decoding a submitted form back into a nested dict
# --------------------------------------------------------------------------

_NAME_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
_RISKY = re.compile(
    r"^(?:[-+]?[0-9]+(?:\.[0-9]+)?|true|false|yes|no|on|off|null|~)$", re.IGNORECASE)
_NORMALISE_INDEX = re.compile(r"\[\d+\]")


def parse_name(name: str) -> list:
    tokens: list = []
    for m in _NAME_TOKEN.finditer(name):
        tokens.append(m.group(1) if m.group(1) is not None else int(m.group(2)))
    return tokens


def normalise_name(name: str) -> str:
    return _NORMALISE_INDEX.sub("[N]", name)


def safe_scalar(value: str) -> Any:
    # A digit string, or one of YAML 1.1's yes/no/on/off/null spellings,
    # would otherwise be silently re-typed the next time anything reads this
    # file back (SPEC 9's version/date trap and the Norway problem). Quoting
    # only when it's actually ambiguous keeps the common case (plain text,
    # codes) free of diff noise from an edit to an unrelated field.
    return DQ(value) if _RISKY.match(value.strip()) else value


def coerce(value: str, kind: str) -> Any:
    if kind == "count":
        v = value.strip()
        if v.lower() == "unknown":
            return "unknown"
        try:
            return int(v)
        except ValueError:
            return safe_scalar(value)  # invalid; save it anyway, let validation say so
    return safe_scalar(value)


def set_path(root: dict, tokens: list, value: Any) -> None:
    node: Any = root
    for i, tok in enumerate(tokens):
        last = i == len(tokens) - 1
        nxt = tokens[i + 1] if not last else None
        if isinstance(tok, int):
            while len(node) <= tok:
                node.append(None)
            if last:
                node[tok] = value
            else:
                if node[tok] in (None, {}):
                    node[tok] = [] if isinstance(nxt, int) else {}
                node = node[tok]
        else:
            if last:
                node[tok] = value
            else:
                if tok not in node or node[tok] is None:
                    node[tok] = [] if isinstance(nxt, int) else {}
                node = node[tok]


def decode_form(fields: dict[str, list[str]], sections: list) -> dict:
    kinds = kind_by_key(sections)
    doc: dict = {}
    for name, values in fields.items():
        key = normalise_name(name)
        kind = kinds.get(key, "text")
        if name.endswith("[]"):
            tokens = parse_name(name[:-2])
            cleaned = [coerce(v, kind) for v in values if v != ""]
            set_path(doc, tokens, cleaned or None)
            continue
        raw = values[0] if values else ""
        tokens = parse_name(name)
        set_path(doc, tokens, coerce(raw, kind) if raw != "" else None)
    return doc


def prune(node: Any) -> Any:
    """Drop everything a blank input wrote as null, and any now-empty
    container, so an untouched section never litters the file with
    `field: null` for every field it never had (SPEC 2: missing IS unknown,
    no need to say so explicitly)."""
    if isinstance(node, dict):
        out = {k: p for k, v in node.items() if (p := prune(v)) is not None}
        return out or None
    if isinstance(node, list):
        out = [p for v in node if (p := prune(v)) is not None]
        return out or None
    return node


def apply_form(doc: dict, decoded: dict) -> dict:
    """Replace exactly the top-level keys the form covers; id/schema_version
    and _unmapped, which the form never touches, are untouched here too."""
    for key, value in decoded.items():
        pruned = prune(value)
        if pruned is None:
            doc.pop(key, None)
        else:
            doc[key] = pruned
    return doc
