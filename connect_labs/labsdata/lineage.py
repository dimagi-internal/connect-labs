"""What a semantic registry's columns and properties inherit from the dictionary.

A registry never names a form path: its properties are SQL over aggregates, over Layer 1
columns, which the workflow's pipelines extract from the forms (each pipeline field
with its own list of fallback paths). So the lineage of a property is that chain walked
backwards -- property -> the columns its SQL reads -> ... -> pipeline fields -> form
paths -- and its setting is the strictest of everything at the bottom.

**A property with any unset source counts as sensitive.** A path the dictionary does
not know, or knows but nobody reviewed, makes everything above it ``unset``; so does a
name the registry reads that this walk cannot place. Nothing here is guessed in the
permissive direction.

Read for a WORKFLOW space only: the workflow is what names the pipelines and binds the
registry (``definition.registry_source``, or the dictionary's own ``registry_id`` to try
a candidate). Everything is read as the person, with their own Connect token.
"""

from __future__ import annotations

from connect_labs.labs.deid import fieldscan, transforms

from . import dictionary as dictionary_mod
from . import spaces

UNSET = "unset"
#: Strictest first; ``unset`` is stricter than anything a person chose.
_ORDER = (UNSET, *transforms.STRICTNESS)
_RANK = {s: i for i, s in enumerate(_ORDER)}

#: Layer 1's own base columns, each a column of ``visits`` (``layer1._BASE_COLUMNS``).
BASE_COLUMNS = (
    "visit_id",
    "visit_date",
    "username",
    "opportunity_id",
    "entity_id",
    "entity_name",
    "status",
    "deliver_unit",
)


def strictest(settings) -> str:
    settings = list(settings)
    if not settings:
        return transforms.KEEP
    return min(settings, key=lambda s: _RANK.get(s, 0))


def _field_paths(config, name: str) -> list[str] | None:
    for f in getattr(config, "fields", None) or []:
        if f.name == name:
            paths = list(getattr(f, "paths", None) or []) or ([f.path] if getattr(f, "path", "") else [])
            return paths
    return None


def compute(entries: list[dict], props_doc: dict, configs: dict) -> dict:
    """The lineage of every registry column and property (pure: testable without Connect).

    ``entries`` is ``dictionary.snapshot``; ``configs`` maps a pipeline alias to its
    ``AnalysisPipelineConfig``."""
    from connect_labs.semantic.compiler import fragment_columns
    from connect_labs.semantic.layer1 import _reads
    from connect_labs.semantic.model import resolve_model

    by_path: dict[str, str] = {}
    for e in entries:
        if e["column"]:
            continue
        for s in e["sources"]:
            by_path[fieldscan.normalise(s["path"])] = dictionary_mod.effective(e) if e["reviewed"] else UNSET
    by_column = {
        e["column"]: (dictionary_mod.effective(e) if e["reviewed"] else UNSET) for e in entries if e["column"]
    }

    model = resolve_model(props_doc)
    # Layer 1: name -> (setting, paths, unset paths)
    nodes: dict[str, dict] = {}

    def layer1(name: str, paths: list[str]) -> None:
        settings, unset = [], []
        for p in paths:
            s = by_path.get(fieldscan.normalise(p), UNSET)
            settings.append(s)
            if s == UNSET:
                unset.append(p)
        nodes[name] = {
            "name": name,
            "layer": "layer1",
            "inherits": strictest(settings) if settings else UNSET,
            "paths": paths,
            "unset_paths": unset if settings else ["(no form path)"],
            "reads": [],
        }

    entity = configs.get(model.entity_pipeline) if model.entity_pipeline else None
    for f in getattr(entity, "fields", None) or []:
        layer1(f.name, _field_paths(entity, f.name) or [])
    for column, alias in (model.extra_fields or {}).items():
        paths = _field_paths(configs.get(alias), column)
        layer1(column, paths or [])
    for lookup in model.lookups or ():
        config = configs.get(lookup.pipeline)
        for column, field_name in (lookup.fields or {}).items():
            layer1(column, _field_paths(config, field_name) or [])
    for column in BASE_COLUMNS:
        nodes[column] = {
            "name": column,
            "layer": "visits",
            "inherits": by_column.get(column, UNSET),
            "paths": [],
            "unset_paths": [] if by_column.get(column, UNSET) != UNSET else [f"visits.{column}"],
            "reads": [],
        }

    derived: list[tuple[str, str, set[str], str]] = []
    for col in model.visit_columns:
        derived.append((col.name, "visit_column", set(_reads(col)), ""))
    for agg in props_doc.get("aggregates") or []:
        derived.append((agg["name"], "aggregate", _columns(fragment_columns, agg.get("sql")), agg.get("label", "")))
    for prop in props_doc.get("properties") or []:
        derived.append((prop["name"], "property", _columns(fragment_columns, prop.get("sql")), prop.get("label", "")))
    pending = {name: (layer, reads, label) for name, layer, reads, label in derived}

    def resolve(name: str, stack: tuple = ()) -> dict:
        if name in nodes:
            return nodes[name]
        if name not in pending or name in stack:
            return {"inherits": UNSET, "unset_paths": [f"(cannot place {name!r})"]}
        layer, reads, label = pending[name]
        parents = [resolve(r, stack + (name,)) for r in sorted(reads)]
        node = {
            "name": name,
            "layer": layer,
            "label": label,
            "reads": sorted(reads),
            "inherits": strictest(p["inherits"] for p in parents),
            "unset_paths": sorted({u for p in parents for u in p.get("unset_paths", [])}),
            "paths": sorted({x for p in parents for x in p.get("paths", [])}),
        }
        nodes[name] = node
        return node

    for name in pending:
        resolve(name)
    properties = [nodes[n] for n, (layer, *_r) in pending.items() if layer == "property"]
    return {
        "available": True,
        "properties": properties,
        "columns": [n for n in nodes.values() if n["layer"] in ("layer1", "visits", "visit_column", "aggregate")],
        "counts": {
            "properties": len(properties),
            "unset": sum(1 for p in properties if p["inherits"] == UNSET),
        },
    }


def _columns(fragment_columns, sql) -> set[str]:
    if not sql:
        return set()
    try:
        return set(fragment_columns(str(sql)))
    except Exception:  # noqa: BLE001 -- unparseable reads as "cannot place", i.e. unset
        return {"(unparseable sql)"}


def for_space(caller, opened: spaces.OpenSpace) -> dict:
    """The lineage of the space's workflow's registry, read as the person."""
    from connect_labs.semantic.workflow_binding import resolve_registry_for
    from connect_labs.workflow.data_access import SemanticRegistryDataAccess

    from .workflow_context import read_pipeline, read_workflow

    dictionary = dictionary_mod.for_space(opened)
    workflow = dict(opened.space.workflow or {})
    if not workflow:
        return {
            "available": False,
            "reason": "Lineage reads a workflow's pipelines and registry: make this space from a workflow.",
        }
    definition, token, owner, _opps = read_workflow(
        caller,
        int(workflow["id"]),
        opportunity_id=workflow.get("opportunity_id"),
        program_id=workflow.get("program_id"),
    )
    try:
        props_doc, *_rest = resolve_registry_for(
            definition,
            registry_access_factory=lambda: SemanticRegistryDataAccess(access_token=token, **owner),
            registry_id_override=dictionary.registry_id,
        )
    except Exception as e:  # noqa: BLE001 -- said as itself on the page
        return {"available": False, "reason": f"The registry could not be read: {str(e)[:300]}"}
    configs = {}
    for source in getattr(definition, "pipeline_sources", None) or []:
        if not isinstance(source, dict) or source.get("pipeline_id") is None:
            continue
        entry, config = read_pipeline(source, token, owner)
        if config is not None and source.get("alias"):
            configs[source["alias"]] = config
    out = compute(dictionary_mod.snapshot(dictionary), props_doc or {}, configs)
    out["registry"] = {"id": dictionary.registry_id or (definition.registry_source or {}).get("registry_id")}
    return out
