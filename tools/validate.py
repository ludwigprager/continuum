#!/usr/bin/env python3
"""
validate.py - five-layer validation of the project catalogue.

    1. Parse          ruamel.yaml round-trip: line/column for every node,
                      duplicate keys rejected.
    2. Structure      JSON Schema draft 2020-12, additionalProperties: false.
    3. Referential    taxonomy codes, site/team/project references, graph cycles.
    4. Plausibility   warnings, never errors.
    5. Completeness   a coverage number, not a verdict.

This file contains NO field names, NO codes and NO enum values. Every rule in
layers 2, 3 and 5 is driven by annotations in schema/project.schema.json:

    "x-taxonomy": "<group>"   value must be a code in that taxonomy.yaml group
    "x-ref":      "<source>"  value must exist in sites/teams/projects
    "x-tracked":  true        counts toward the coverage percentage
    "x-graph":    "<name>"    edges of a graph that must stay acyclic
    "x-kind":     "<kind>"    wording of the error message (set on $defs)

Adding a field is therefore a schema edit, not a code change. The only place
domain field names appear is the plausibility rules at the bottom, which are
inherently about specific fields; each is a small self-contained function.

Exit codes (HANDOFF 5.3): 0 ok, 1 invalid data, 2 tool/usage error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

try:
    from ruamel.yaml import YAML
    from ruamel.yaml.constructor import DuplicateKeyError
    from ruamel.yaml.error import MarkedYAMLError
except ImportError:  # pragma: no cover
    sys.exit("ruamel.yaml is required (it is in the pipeline image): exit 2")

try:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError
except ImportError:  # pragma: no cover
    sys.exit("jsonschema is required (it is in the pipeline image): exit 2")


EXIT_OK, EXIT_INVALID, EXIT_TOOL = 0, 1, 2

# Values that all mean "nobody has said". Treated identically everywhere:
# a missing key, an explicit null and the string `unknown` are the same thing.
UNKNOWN = "unknown"

ERROR, WARNING, INFO = "error", "warning", "info"


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    message: str
    file: str | None = None
    line: int | None = None
    col: int | None = None
    fix: str | None = None

    def sort_key(self) -> tuple:
        order = {ERROR: 0, WARNING: 1, INFO: 2}
        return (self.file or "", self.line or 0, self.col or 0,
                order.get(self.level, 3), self.code, self.message)

    def render_text(self) -> str:
        if self.file and self.line:
            head = f"{self.file}:{self.line}:{self.col}"
        elif self.file:
            head = self.file
        else:
            head = "(catalogue)"
        marker = "" if self.level == ERROR else f" [{self.level}]"
        out = [f"{head}{marker}", f"  {self.message}"]
        if self.fix:
            out.extend(f"  {line}" for line in self.fix.splitlines())
        return "\n".join(out)

    def as_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "file": self.file,
                "line": self.line, "col": self.col, "message": self.message,
                "fix": self.fix}


def joined(values: Sequence[str], limit: int = 12) -> str:
    vals = list(values)
    shown = ", ".join(vals[:limit])
    return shown + (f", ... ({len(vals)} total)" if len(vals) > limit else "")


def did_you_mean(value: str, options: Sequence[str]) -> str | None:
    import difflib
    match = difflib.get_close_matches(str(value), [str(o) for o in options], n=1, cutoff=0.6)
    return match[0] if match else None


# --------------------------------------------------------------------------
# Layer 1: parse
# --------------------------------------------------------------------------

def make_loader() -> YAML:
    # typ='rt' is not a preference. It is the only mode that populates .lc,
    # and .lc is where every line:col in every error message comes from.
    y = YAML(typ="rt")
    y.preserve_quotes = True
    return y


def normalise_dates(node: Any) -> Any:
    """YAML resolves 2026-09-14 to a date object, which fails `type: string`.

    Both spellings are legal input; this makes them the same value before the
    schema sees them. Versions deliberately get no such treatment - an unquoted
    7.9 must fail loudly, because it has already lost precision (HANDOFF 9).
    """
    if isinstance(node, dict):
        for key in list(node.keys()):
            node[key] = normalise_dates(node[key])
        return node
    if isinstance(node, list):
        for i, item in enumerate(node):
            node[i] = normalise_dates(item)
        return node
    if isinstance(node, dt.datetime):
        return node.date().isoformat()
    if isinstance(node, dt.date):
        return node.isoformat()
    return node


def mark_of(exc: Any) -> tuple[int, int]:
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    if mark is None:
        return (1, 1)
    return (mark.line + 1, mark.column + 1)


def parse_file(path: Path, rel: str) -> tuple[Any, list[Finding]]:
    loader = make_loader()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, [Finding(ERROR, "parse.unreadable", f"cannot read file: {exc}", rel)]
    try:
        doc = loader.load(text)
    except DuplicateKeyError as exc:
        line, col = mark_of(exc)
        return None, [Finding(
            ERROR, "parse.duplicate-key",
            "duplicate key: the same key appears twice in this block",
            rel, line, col,
            fix="Only the second one would survive, silently. Delete or rename one.\n"
                "(PyYAML keeps the last without complaining; this is why the\n"
                " validator uses ruamel.yaml.)")]
    except MarkedYAMLError as exc:
        line, col = mark_of(exc)
        problem = (getattr(exc, "problem", None) or str(exc)).strip()
        return None, [Finding(ERROR, "parse.invalid-yaml", f"invalid YAML: {problem}",
                              rel, line, col)]
    except Exception as exc:  # pragma: no cover
        return None, [Finding(ERROR, "parse.invalid-yaml", f"invalid YAML: {exc}", rel)]

    if doc is None:
        return None, [Finding(ERROR, "parse.empty", "file is empty", rel, 1, 1)]
    if not isinstance(doc, dict):
        return None, [Finding(ERROR, "parse.not-a-mapping",
                              f"top level must be a mapping, found {type(doc).__name__}",
                              rel, 1, 1)]
    return normalise_dates(doc), []


def locate(node: Any, path: Sequence[Any], key: Any = None) -> tuple[int, int]:
    """Map a JSON-Schema instance path onto a line:col in the source file.

    jsonschema hands back a path of keys and indices; ruamel hangs .lc off each
    node. Nothing connects the two, so this walks one with the other and
    degrades to the nearest ancestor it could resolve.
    """
    line = col = None
    cur = node
    lc = getattr(cur, "lc", None)
    if lc is not None and lc.line is not None:
        line, col = lc.line, lc.col

    for step in path:
        lc = getattr(cur, "lc", None)
        if lc is None:
            break
        try:
            pos = lc.item(step) if isinstance(step, int) else lc.value(step)
        except (KeyError, IndexError, TypeError, AttributeError):
            break
        if pos:
            line, col = pos[0], pos[1]
        try:
            cur = cur[step]
        except (KeyError, IndexError, TypeError):
            break

    if key is not None:
        lc = getattr(cur, "lc", None)
        if lc is not None:
            try:
                pos = lc.key(key)
                if pos:
                    line, col = pos[0], pos[1]
            except (KeyError, IndexError, TypeError, AttributeError):
                pass

    if line is None:
        return (1, 1)
    return (line + 1, (col or 0) + 1)


def path_str(path: Sequence[Any]) -> str:
    out = ""
    for step in path:
        if isinstance(step, int):
            out += f"[{step}]"
        else:
            out = f"{out}.{step}" if out else str(step)
    return out or "(root)"


def path_pattern(path: Sequence[Any]) -> str:
    """('placement','environments',0,'datacenter') -> placement.environments[].datacenter"""
    parts: list[str] = []
    for step in path:
        if isinstance(step, int):
            if parts:
                parts[-1] += "[]"
        else:
            parts.append(str(step))
    return ".".join(parts)


# --------------------------------------------------------------------------
# Schema: $ref resolution and annotation walking
# --------------------------------------------------------------------------

ANNOTATION_PREFIX = "x-"


class Schema:
    def __init__(self, root: dict):
        self.root = root

    def resolve(self, node: Any) -> dict:
        """Follow local $refs. Annotations on the referencing node win."""
        if not isinstance(node, dict):
            return {}
        seen = 0
        annotations = {k: v for k, v in node.items() if k.startswith(ANNOTATION_PREFIX)}
        while isinstance(node, dict) and "$ref" in node and seen < 16:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/"):
                break
            target: Any = self.root
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or part not in target:
                    return {}
                target = target[part]
            node = target
            seen += 1
        merged = dict(node) if isinstance(node, dict) else {}
        for key, value in annotations.items():
            merged.setdefault(key, value)
            merged[key] = value
        return merged

    def annotations(self, node: Any) -> dict:
        resolved = self.resolve(node)
        return {k: v for k, v in resolved.items() if k.startswith(ANNOTATION_PREFIX)}

    def walk_instance(self, inst: Any, node: Any = None,
                      path: tuple = ()) -> Iterator[tuple[tuple, Any, dict]]:
        """Yield (path, value, annotations) for every annotated node present."""
        node = self.root if node is None else node
        resolved = self.resolve(node)
        if not resolved:
            return
        annots = {k: v for k, v in resolved.items() if k.startswith(ANNOTATION_PREFIX)}
        if annots:
            yield (path, inst, annots)

        props = resolved.get("properties")
        if isinstance(inst, dict) and isinstance(props, dict):
            for key, value in inst.items():
                if key in props:
                    yield from self.walk_instance(value, props[key], path + (key,))
            return

        items = resolved.get("items")
        if isinstance(inst, list) and isinstance(items, dict):
            for index, value in enumerate(inst):
                yield from self.walk_instance(value, items, path + (index,))

    def walk_schema(self, node: Any = None, pattern: str = "",
                    depth: int = 0) -> Iterator[tuple[str, dict]]:
        """Yield (pattern, annotations) for every annotated node in the schema."""
        if depth > 24:
            return
        node = self.root if node is None else node
        resolved = self.resolve(node)
        if not resolved:
            return
        annots = {k: v for k, v in resolved.items() if k.startswith(ANNOTATION_PREFIX)}
        if annots and pattern:
            yield (pattern, annots)

        props = resolved.get("properties")
        if isinstance(props, dict):
            for key, child in props.items():
                child_pattern = f"{pattern}.{key}" if pattern else key
                yield from self.walk_schema(child, child_pattern, depth + 1)

        items = resolved.get("items")
        if isinstance(items, dict):
            yield from self.walk_schema(items, f"{pattern}[]", depth + 1)


def is_known(value: Any) -> bool:
    """Is this an actual answer, as opposed to a way of saying nothing?"""
    if value is None:
        return False
    if isinstance(value, str) and (not value.strip() or value.strip().lower() == UNKNOWN):
        return False
    if isinstance(value, (list, dict)) and len(value) == 0:
        return False
    if isinstance(value, list):
        return any(is_known(v) for v in value)
    return True


# --------------------------------------------------------------------------
# Layer 2: structure
# --------------------------------------------------------------------------

def describe_type(value: Any) -> str:
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int):
        return f"an integer ({value})"
    if isinstance(value, float):
        return f"a float ({value!r})"
    if isinstance(value, str):
        return f"a string ({value!r})"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a mapping"
    if value is None:
        return "null"
    return type(value).__name__


KIND_FIX = {
    "version": (
        "Version numbers must be quoted strings.\n"
        "  Unquoted 7.9 is a float and eventually prints as 7.9000000000000004;\n"
        "  unquoted 2016 is an integer. Write:  version: \"7.9\"\n"
        "  Use \"unknown\" if nobody has established it."
    ),
    "count": (
        "Expected a whole number >= 0, or \"unknown\".\n"
        "  Do not write 0 to mean \"not established\" - 0 is a real answer\n"
        "  and it would be counted as one."
    ),
    "date": (
        "Expected an ISO date (2026-09-14) or \"unknown\"."
    ),
    "code": (
        "Expected a code (a string), or \"unknown\"."
    ),
    "id": (
        "id must be lowercase letters, digits and hyphens, e.g. payment-gateway.\n"
        "  It is the filename and the key every other file refers to."
    ),
    "schema_version": (
        "This validator understands schema_version: 1 only."
    ),
}


def structure_findings(doc: Any, rel: str, schema: Schema,
                       validator: Draft202012Validator) -> list[Finding]:
    findings: list[Finding] = []
    for err in validator.iter_errors(doc):
        path = list(err.absolute_path)

        # `_unmapped` holds whatever the importer could not map. Arbitrary
        # content by contract (HANDOFF 3) - never structurally validated.
        if path and path[0] == "_unmapped":
            continue

        sub = err.schema if isinstance(err.schema, dict) else {}
        kind = sub.get("x-kind")
        where = path_str(path)

        if err.validator == "additionalProperties":
            known = list((err.schema or {}).get("properties", {}).keys())
            unknown_keys = [k for k in (err.instance or {}) if k not in known]
            for bad in sorted(unknown_keys):
                line, col = locate(doc, path, key=bad)
                suggestion = did_you_mean(bad, known)
                fix = ""
                if suggestion:
                    fix += f"did you mean: {suggestion}\n"
                fix += (f"valid here: {joined(sorted(known))}\n"
                        f"If {bad!r} is a new field, add it under "
                        f"{'properties of ' + where if where != '(root)' else 'the root'} "
                        f"in schema/project.schema.json.\n"
                        "Until then the importer parks unknown columns in _unmapped.")
                findings.append(Finding(
                    ERROR, "structure.unknown-field",
                    f"unknown field {bad!r}", rel, line, col, fix))
            continue

        line, col = locate(doc, path)
        name = path[-1] if path else "(root)"

        if err.validator == "required":
            missing = re.findall(r"'([^']+)'", err.message)
            label = missing[0] if missing else "?"
            if not path:
                fix = (f"Every project file needs {label!r}. "
                       "Everything else may be missing (HANDOFF 5.1).")
            else:
                # A nested `required` only applies because the parent block is
                # present. Saying "every project file needs it" would be false
                # and would send someone looking in the wrong place.
                parent = path_str(path)
                fix = (f"{parent}.{label} is required whenever {parent!r} is present.\n"
                       f"  Either fill it in, or remove the whole {parent!r} block - "
                       f"a project\n"
                       f"  that has not been assessed yet is allowed to omit it entirely.")
            findings.append(Finding(
                ERROR, "structure.missing-field",
                f"missing required field {label!r}", rel, line, col, fix=fix))
            continue

        if err.validator in ("type", "anyOf", "const", "enum", "pattern", "minimum"):
            got = describe_type(err.instance)
            message = f"{name}: got {got}"
            fix = KIND_FIX.get(kind or "")
            if err.validator == "pattern" and kind == "version":
                message = f"{name}: {err.instance!r} is not a valid version string"
            elif err.validator == "pattern" and kind == "date":
                message = f"{name}: {err.instance!r} is not a valid date"
            elif err.validator == "const":
                message = f"{name}: expected {sub.get('const')!r}, got {err.instance!r}"
            elif kind:
                message = f"{name}: expected {kind}, got {got}"
            if fix is None:
                fix = f"schema says: {json.dumps({k: v for k, v in sub.items() if not k.startswith('x-') and k != 'description'}, ensure_ascii=False)}"
            findings.append(Finding(ERROR, f"structure.{err.validator}", message,
                                    rel, line, col, fix))
            continue

        findings.append(Finding(ERROR, f"structure.{err.validator}",
                                f"{where}: {err.message}", rel, line, col))
    return findings


# --------------------------------------------------------------------------
# Layer 3: referential integrity
# --------------------------------------------------------------------------

def referential_findings(doc: Any, rel: str, schema: Schema,
                         taxonomy: dict, refsets: dict[str, set[str]],
                         graphs: dict[str, dict[str, list]]) -> list[Finding]:
    findings: list[Finding] = []
    project_id = doc.get("id")

    for path, value, annots in schema.walk_instance(doc):
        group = annots.get("x-taxonomy")
        source = annots.get("x-ref")
        graph = annots.get("x-graph")

        if graph and isinstance(value, list) and project_id:
            edges = graphs.setdefault(graph, {})
            edges[str(project_id)] = [(str(v), path + (i,))
                                      for i, v in enumerate(value) if is_known(v)]

        if not isinstance(value, str) or not is_known(value):
            continue
        name = path[-1] if path else "?"
        if isinstance(name, int):
            name = path[-2] if len(path) > 1 else "?"

        if group:
            codes = taxonomy.get(group, {})
            if value not in codes:
                line, col = locate(doc, path)
                suggestion = did_you_mean(value, list(codes))
                fix = (f"did you mean: {suggestion}\n" if suggestion else "")
                fix += (f"valid: {joined(sorted(codes))}  "
                        f"(see schema/taxonomy.yaml, group {group!r})\n"
                        "To add a code, add it to that group in taxonomy.yaml. "
                        "Nothing else changes.")
                findings.append(Finding(
                    ERROR, "ref.unknown-code",
                    f"{name}: {value!r} is not a known code", rel, line, col, fix))

        if source:
            known = refsets.get(source, set())
            if value not in known:
                line, col = locate(doc, path)
                suggestion = did_you_mean(value, list(known))
                fix = (f"did you mean: {suggestion}\n" if suggestion else "")
                if source == "projects":
                    fix += ("Every dependency must name a project id that exists.\n"
                            "  If the target is not in the catalogue yet, remove the\n"
                            "  entry or add the project file - a dangling reference\n"
                            "  silently drops out of the dependency graph.")
                else:
                    fix += (f"valid: {joined(sorted(known))}  "
                            f"(see schema/{source}.yaml)\n"
                            f"To add one, add it to schema/{source}.yaml.")
                findings.append(Finding(
                    ERROR, f"ref.unknown-{source}",
                    f"{name}: {value!r} is not in schema/{source}.yaml"
                    if source != "projects" else
                    f"{name}: depends on {value!r}, which is not a known project",
                    rel, line, col, fix))
    return findings


def cycle_findings(graphs: dict[str, dict[str, list]],
                   locations: dict[str, tuple[str, Any]]) -> list[Finding]:
    """Report the cycle itself, not just that one exists (HANDOFF 6.1)."""
    findings: list[Finding] = []
    for graph_name, edges in sorted(graphs.items()):
        colour: dict[str, int] = {}
        stack: list[str] = []
        reported: set[tuple] = set()

        def visit(node: str) -> None:
            colour[node] = 1
            stack.append(node)
            for target, path in edges.get(node, []):
                state = colour.get(target, 0)
                if state == 1:
                    cycle = stack[stack.index(target):] + [target]
                    canonical = tuple(sorted(set(cycle)))
                    if canonical not in reported:
                        reported.add(canonical)
                        rel, doc = locations.get(node, (None, None))
                        line, col = locate(doc, path) if doc is not None else (None, None)
                        findings.append(Finding(
                            ERROR, "ref.cycle",
                            f"dependency cycle: {' -> '.join(cycle)}",
                            rel, line, col,
                            fix=f"{graph_name} must be acyclic: nothing in this ring can be\n"
                                "  migrated first. Break it by removing one edge, or record\n"
                                "  the weaker direction somewhere other than depends_on."))
                elif state == 0:
                    visit(target)
            stack.pop()
            colour[node] = 2

        for node in sorted(edges):
            if colour.get(node, 0) == 0:
                visit(node)
    return findings


# --------------------------------------------------------------------------
# Layer 4: plausibility (warnings only)
# --------------------------------------------------------------------------

RULES: list = []


def rule(code: str, level: str = WARNING):
    def decorator(fn):
        RULES.append((code, level, fn))
        return fn
    return decorator


def dig(doc: Any, dotted: str) -> Any:
    cur = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


@rule("plausible.container-not-containerised")
def _container(doc):
    if dig(doc, "platform.deployment_model") == "container" \
            and dig(doc, "platform.containerised") == "none":
        yield ("platform.containerised",
               "deployment_model is 'container' but containerised is 'none'",
               "One of the two is stale. Which is right changes the migration strategy.")


@rule("plausible.tier1-long-rto")
def _rto(doc):
    rto = dig(doc, "operations.rto_hours")
    if dig(doc, "operations.tier") == "tier-1" and isinstance(rto, int) and rto >= 168:
        yield ("operations.rto_hours",
               f"tier-1 project with rto_hours: {rto} (a week or more)",
               "Either the tier or the RTO is wrong. Both drive the migration order.")


@rule("plausible.storage-class-without-size")
def _storage(doc):
    classes = dig(doc, "platform.storage_classes")
    size = dig(doc, "platform.storage_gb")
    if classes and size == 0:
        yield ("platform.storage_gb",
               "storage classes are declared but storage_gb is 0",
               "0 means zero, not unknown. Use \"unknown\" if nobody has measured it.")


@rule("plausible.no-prod-environment")
def _prod(doc):
    envs = dig(doc, "placement.environments")
    if isinstance(envs, list) and envs:
        names = {str(e.get("name") or "").lower() for e in envs if isinstance(e, dict)}
        names.discard("")
        # Imported environments have no name at all: the legacy sheet has one
        # column per project, so there is nothing to derive a name from. No
        # names means no evidence either way, which is not a finding.
        if names and not names & {"prod", "produktion", "production"}:
            yield ("placement.environments",
                   f"no prod environment (found: {joined(sorted(n for n in names if n))})",
                   "Either it is genuinely not in production, or an environment is missing.")


@rule("plausible.migrated-with-open-blockers")
def _blockers(doc):
    if dig(doc, "migration.status") != "migrated":
        return
    blockers = dig(doc, "migration.blockers") or []
    open_ids = [str(b.get("id") or "?") for b in blockers
                if isinstance(b, dict) and b.get("status") == "open"]
    if open_ids:
        yield ("migration.blockers",
               f"status is 'migrated' but {len(open_ids)} blocker(s) are still open: "
               f"{joined(open_ids)}",
               "Close the blockers or correct the status. This pair is reported to "
               "management as done.")


@rule("plausible.both-placement-shapes")
def _placement(doc):
    if dig(doc, "placement.datacenter") and dig(doc, "placement.environments"):
        yield ("placement",
               "both a single 'datacenter' and an 'environments' list are set",
               "Both shapes are legal while HANDOFF 12.4 is open, but not together:\n"
               "  the site cross-tab would count this project twice.")


@rule("plausible.verified-without-review")
def _verified(doc):
    if dig(doc, "_meta.confidence") == "verified" and not dig(doc, "_meta.last_reviewed"):
        yield ("_meta.last_reviewed",
               "confidence is 'verified' but last_reviewed is empty",
               "Verified coverage is reported separately to management (HANDOFF 11).\n"
               "  A verification with no date behind it cannot be audited.")


def plausibility_findings(doc: Any, rel: str) -> list[Finding]:
    findings: list[Finding] = []
    for code, level, fn in RULES:
        try:
            for dotted, message, fix in fn(doc):
                path = tuple(p for p in dotted.split(".") if p)
                line, col = locate(doc, path)
                findings.append(Finding(level, code, message, rel, line, col, fix))
        except Exception as exc:  # a broken rule must not fail the run
            findings.append(Finding(
                WARNING, "plausible.rule-crashed",
                f"plausibility rule {code!r} crashed: {exc}", rel))
    return findings


# --------------------------------------------------------------------------
# Layer 5: completeness
# --------------------------------------------------------------------------

def coverage_for(doc: Any, schema: Schema, tracked: set[str]) -> tuple[int, int]:
    filled = set()
    for path, value, annots in schema.walk_instance(doc):
        if not annots.get("x-tracked"):
            continue
        pattern = path_pattern(path)
        if pattern in tracked and is_known(value):
            filled.add(pattern)
    return len(filled), len(tracked)


def pct(part: int, total: int) -> float:
    return round(100.0 * part / total, 1) if total else 0.0


# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

def load_reference(path: Path) -> dict:
    loader = YAML(typ="safe")
    with path.open(encoding="utf-8") as handle:
        return loader.load(handle) or {}


SCHEMA_BASENAME = "project.schema"


def schema_file_in(schema_dir: Path) -> Path:
    """The project schema, as YAML by preference.

    YAML because the people who maintain it hand-edit YAML all day (HANDOFF 2)
    and because it takes comments: the recipe for adding a field lives at the
    top of the file being edited. JSON is still accepted so an older checkout,
    or `import_xlsx.py derive-schema` output, keeps working.
    """
    for suffix in (".yaml", ".yml", ".json"):
        candidate = schema_dir / (SCHEMA_BASENAME + suffix)
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"{schema_dir / (SCHEMA_BASENAME + '.yaml')} not found")


def load_schema_dir(schema_dir: Path) -> tuple[dict, dict, dict, dict]:
    schema_file = schema_file_in(schema_dir)
    if schema_file.suffix == ".json":
        root = json.loads(schema_file.read_text(encoding="utf-8"))
    else:
        root = load_reference(schema_file)

    taxonomy_file = schema_dir / "taxonomy.yaml"
    taxonomy_raw = load_reference(taxonomy_file) if taxonomy_file.exists() else {}
    taxonomy = {name: dict(group.get("codes") or {})
                for name, group in (taxonomy_raw.get("groups") or {}).items()}

    references: dict[str, dict] = {}
    for candidate in sorted(schema_dir.glob("*.yaml")):
        if candidate.name == "taxonomy.yaml" or \
                candidate.name.startswith(SCHEMA_BASENAME):
            continue
        data = load_reference(candidate)
        references[candidate.stem] = data
    return root, taxonomy, references, taxonomy_raw


# --------------------------------------------------------------------------
# --check-schema: the self-test
# --------------------------------------------------------------------------

def check_schema(schema_dir: Path) -> list[Finding]:
    """A typo in an annotation would otherwise validate nothing, silently.

    That is the worst failure mode this design has, so it gets its own check.
    """
    findings: list[Finding] = []
    try:
        root, taxonomy, references, taxonomy_raw = load_schema_dir(schema_dir)
    except Exception as exc:
        return [Finding(ERROR, "schema.unreadable", str(exc), str(schema_dir))]

    rel_schema = str(schema_file_in(schema_dir))
    try:
        Draft202012Validator.check_schema(root)
    except SchemaError as exc:
        return [Finding(ERROR, "schema.invalid",
                        f"project.schema.json is not a valid JSON Schema: {exc.message}",
                        rel_schema)]

    schema = Schema(root)
    known_sources = set(references) | {"projects"}
    known_annotations = {"x-taxonomy", "x-ref", "x-tracked", "x-graph", "x-kind",
                         "x-column", "x-doc"}

    tracked = 0
    for pattern, annots in schema.walk_schema():
        for key in annots:
            if key not in known_annotations:
                findings.append(Finding(
                    ERROR, "schema.unknown-annotation",
                    f"{pattern}: unknown annotation {key!r}", rel_schema,
                    fix=f"known: {joined(sorted(known_annotations))}\n"
                        "An unrecognised annotation is ignored at runtime, which means "
                        "the field would be validated less than you think."))
        group = annots.get("x-taxonomy")
        if group and group not in taxonomy:
            findings.append(Finding(
                ERROR, "schema.unknown-taxonomy-group",
                f"{pattern}: x-taxonomy names group {group!r}, which is not in taxonomy.yaml",
                rel_schema,
                fix=f"groups: {joined(sorted(taxonomy))}"))
        source = annots.get("x-ref")
        if source and source not in known_sources:
            findings.append(Finding(
                ERROR, "schema.unknown-ref-source",
                f"{pattern}: x-ref names {source!r}, for which there is no schema/{source}.yaml",
                rel_schema,
                fix=f"available: {joined(sorted(known_sources))}"))
        if annots.get("x-tracked"):
            tracked += 1
        if annots.get("x-column") and "[]" in pattern:
            findings.append(Finding(
                ERROR, "schema.column-on-repeated-field",
                f"{pattern}: x-column cannot go on a field inside a list",
                rel_schema,
                fix="The projects table has one row per project, so a repeated\n"
                    "  field has no single value to put in the column. It belongs on\n"
                    "  the datastores/environments table, at its own grain."))

    if tracked == 0:
        findings.append(Finding(
            WARNING, "schema.nothing-tracked",
            "no field is marked x-tracked, so coverage would always be 0%", rel_schema))

    # taxonomy internal consistency
    rel_tax = str(schema_dir / "taxonomy.yaml")
    unlabelled: list[str] = []
    for name, group in sorted((taxonomy_raw.get("groups") or {}).items()):
        codes = dict(group.get("codes") or {})
        order = list(group.get("order") or [])
        for code in sorted(set(codes) - set(order)):
            findings.append(Finding(
                ERROR, "schema.code-not-in-order",
                f"taxonomy group {name!r}: code {code!r} is missing from `order:`", rel_tax,
                fix="`order:` decides the sort order of every chart and table. "
                    "A code missing from it has no defined position."))
        for code in sorted(set(order) - set(codes)):
            findings.append(Finding(
                ERROR, "schema.order-not-a-code",
                f"taxonomy group {name!r}: `order:` lists {code!r}, which is not a code", rel_tax))
        if UNKNOWN not in codes:
            findings.append(Finding(
                WARNING, "schema.no-unknown-code",
                f"taxonomy group {name!r} has no 'unknown' code", rel_tax,
                fix="unknown must be an explicit row in every distribution, "
                    "never silently dropped (HANDOFF 11)."))
        for code, labels in sorted(codes.items()):
            if not isinstance(labels, dict) or labels.get("label_de") is None:
                unlabelled.append(f"{name}/{code}")

    if unlabelled:
        findings.append(Finding(
            WARNING, "schema.unlabelled-codes",
            f"{len(unlabelled)} code(s) have no German label: {joined(unlabelled, 8)}",
            rel_tax,
            fix="HANDOFF 12.1: real labels are the top blocker for the deck (M5).\n"
                "  Until they are filled in, charts would be labelled with bare codes."))

    for name, data in sorted(references.items()):
        if data.get("provisional"):
            findings.append(Finding(
                WARNING, "schema.provisional-reference",
                f"schema/{name}.yaml is marked provisional",
                str(schema_dir / f"{name}.yaml"),
                fix="It was seeded from fixtures, not from the authoritative list. "
                    "Anything missing from it makes real data fail validation."))
    return findings


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def project_files(projects_dir: Path) -> list[Path]:
    # Skip anything starting with `_`: _import_manifest.yaml is provenance,
    # not a project (HANDOFF 3).
    return sorted(p for p in projects_dir.rglob("*.yaml")
                  if not p.name.startswith("_")
                  and not any(part.startswith("_") for part in p.relative_to(projects_dir).parts))


def validate(projects_dir: Path, schema_dir: Path) -> tuple[list[Finding], dict]:
    root, taxonomy, references, _ = load_schema_dir(schema_dir)
    schema = Schema(root)
    validator = Draft202012Validator(root)

    tracked = {pattern for pattern, annots in schema.walk_schema() if annots.get("x-tracked")}

    files = project_files(projects_dir)
    findings: list[Finding] = []
    docs: dict[str, tuple[str, Any]] = {}
    order: list[tuple[str, Any]] = []

    for path in files:
        # Relative to the working directory, which is the repo root (/work in
        # the container). Anything else - an absolute /work/... path, or a path
        # relative to --projects - is not clickable in the caller's editor,
        # and that is the entire point of layer 1.
        rel = relative_path(path)
        doc, parse_errors = parse_file(path, rel)
        findings.extend(parse_errors)
        if doc is None:
            continue
        order.append((rel, doc))
        pid = str(doc.get("id") or "")
        if pid:
            if pid in docs:
                findings.append(Finding(
                    ERROR, "ref.duplicate-id",
                    f"project id {pid!r} is also used by {docs[pid][0]}", rel,
                    *locate(doc, ("id",)),
                    fix="Ids are the key every other file refers to; two files "
                        "sharing one makes every reference ambiguous."))
            else:
                docs[pid] = (rel, doc)

    refsets = {name: set((data.get("entries") or {}).keys())
               for name, data in references.items()}
    refsets["projects"] = set(docs)

    graphs: dict[str, dict[str, list]] = {}
    locations = {pid: (rel, doc) for pid, (rel, doc) in docs.items()}

    per_project: list[dict] = []
    for rel, doc in order:
        findings.extend(structure_findings(doc, rel, schema, validator))
        findings.extend(referential_findings(doc, rel, schema, taxonomy, refsets, graphs))
        findings.extend(plausibility_findings(doc, rel))
        filled, total = coverage_for(doc, schema, tracked)
        per_project.append({
            "id": str(doc.get("id") or ""),
            "file": rel,
            "team_id": str(dig(doc, "ownership.team_id") or UNKNOWN),
            "fields_filled": filled,
            "fields_tracked": total,
            "coverage_pct": pct(filled, total),
            "confidence": str(dig(doc, "_meta.confidence") or UNKNOWN),
        })

    findings.extend(cycle_findings(graphs, locations))

    total_filled = sum(p["fields_filled"] for p in per_project)
    total_tracked = sum(p["fields_tracked"] for p in per_project)
    verified = [p for p in per_project if p["confidence"] == "verified"]

    by_team: dict[str, dict] = {}
    for entry in per_project:
        team = by_team.setdefault(entry["team_id"],
                                  {"team_id": entry["team_id"], "projects": 0,
                                   "filled": 0, "tracked": 0, "verified": 0})
        team["projects"] += 1
        team["filled"] += entry["fields_filled"]
        team["tracked"] += entry["fields_tracked"]
        team["verified"] += 1 if entry["confidence"] == "verified" else 0

    coverage = {
        "projects_total": len(per_project),
        "fields_tracked": len(tracked),
        # Raw and verified coverage are two different numbers and are never
        # merged into one (HANDOFF 11).
        "field_coverage_pct": pct(total_filled, total_tracked),
        "verified_coverage_pct": pct(len(verified), len(per_project)),
        "by_team": [
            {"team_id": t["team_id"], "projects": t["projects"],
             "coverage_pct": pct(t["filled"], t["tracked"]),
             "verified_pct": pct(t["verified"], t["projects"])}
            for t in sorted(by_team.values(), key=lambda t: t["team_id"])
        ],
        "by_project": sorted(per_project, key=lambda p: (p["file"], p["id"])),
    }
    return findings, coverage


def render_text(findings: list[Finding], coverage: dict | None, files: int) -> str:
    out: list[str] = []
    for finding in findings:
        out.append(finding.render_text())
        out.append("")
    errors = sum(1 for f in findings if f.level == ERROR)
    warnings = sum(1 for f in findings if f.level == WARNING)
    out.append(f"{files} file(s): {errors} error(s), {warnings} warning(s)")
    if coverage:
        out.append(
            f"coverage: {coverage['field_coverage_pct']}% of "
            f"{coverage['fields_tracked']} tracked fields, "
            f"{coverage['verified_coverage_pct']}% of projects verified")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the project catalogue.",
        epilog="exit: 0 ok, 1 invalid data, 2 tool/usage error")
    parser.add_argument("--projects", default="projects", type=Path)
    parser.add_argument("--schema", default="schema", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--strict", action="store_true",
                        help="warnings fail the run too")
    parser.add_argument("--check-schema", action="store_true",
                        help="validate the schema and reference files themselves, "
                             "then exit")
    args = parser.parse_args(argv)

    if args.check_schema:
        findings = sorted(check_schema(args.schema), key=Finding.sort_key)
        if args.format == "json":
            print(json.dumps({"version": 1, "mode": "check-schema",
                              "findings": [f.as_dict() for f in findings]},
                             indent=2, ensure_ascii=False, sort_keys=True))
        else:
            print(render_text(findings, None, 0))
        errors = sum(1 for f in findings if f.level == ERROR)
        warnings = sum(1 for f in findings if f.level == WARNING)
        if errors or (args.strict and warnings):
            return EXIT_INVALID
        return EXIT_OK

    if not args.projects.is_dir():
        print(f"--projects: {args.projects} does not exist.", file=sys.stderr)
        if str(args.projects) == "projects":
            # Likely a fresh checkout. projects/ is produced by the importer,
            # it is not part of the repo skeleton.
            print(
                "\n  projects/ is created by the import, from the legacy spreadsheet:\n"
                "      python3 tools/import_xlsx.py profile projekte.xlsx --out import/\n"
                "      # edit import/mapping.yaml and import/value_map.yaml\n"
                "      python3 tools/import_xlsx.py convert projekte.xlsx "
                "--config import/ --out projects/\n"
                "\n  To validate the fixtures instead:\n"
                "      ./check.sh tests/fixtures/projects\n",
                file=sys.stderr)
        return EXIT_TOOL
    if not args.schema.is_dir():
        print(f"--schema: {args.schema} is not a directory", file=sys.stderr)
        return EXIT_TOOL

    files = project_files(args.projects)
    if not files:
        # Exiting 0 on an empty directory is how a miswired CI job passes forever.
        print(f"--projects: {args.projects} exists but contains no project files.\n"
              "  Expected *.yaml under it; names starting with '_' are skipped by "
              "design (HANDOFF 3).\n"
              "  Exiting 2 rather than 0: an empty directory is a wiring mistake, "
              "not a clean bill of health.", file=sys.stderr)
        return EXIT_TOOL

    try:
        findings, coverage = validate(args.projects, args.schema)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_TOOL
    except json.JSONDecodeError as exc:
        print(f"schema/project.schema.json is not valid JSON: {exc}", file=sys.stderr)
        return EXIT_TOOL

    findings = sorted(findings, key=Finding.sort_key)

    if args.format == "json":
        errors = sum(1 for f in findings if f.level == ERROR)
        warnings = sum(1 for f in findings if f.level == WARNING)
        print(json.dumps({
            "version": 1,
            "summary": {"files": len(files), "errors": errors, "warnings": warnings},
            "coverage": coverage,
            "findings": [f.as_dict() for f in findings],
        }, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(render_text(findings, coverage, len(files)))

    errors = sum(1 for f in findings if f.level == ERROR)
    warnings = sum(1 for f in findings if f.level == WARNING)
    if errors:
        return EXIT_INVALID
    if args.strict and warnings:
        return EXIT_INVALID
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(EXIT_TOOL)
