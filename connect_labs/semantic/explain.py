"""Explain an indicator: the exact SQL behind a number, in dependency order.

The registry is data so that a second engine -- a person, or an agent working
from a compute spec -- can read the logic instead of trusting a label. This is
the reader. Given an indicator id it returns everything that determines its
value, resolved and in the order it is evaluated:

  constants   the :NAME values that were substituted (section-2 cutoffs)
  aggregates  the per-baby aggregates over the visit rows it touches
  weight_series
              the window-function derivations over the weight series
  properties  the Layer-2 property chain, transitively, constants substituted
  measure     the Layer-3 numerator / denominator / expression, compiled

plus the full compiled statement for one scope, with the pipeline rows as a
named placeholder (Layer 1 is the pipeline schema and lives with the pipeline).

Pure: no database, no request. `semantic_registry_explain` wraps it for MCP.
"""

from __future__ import annotations

import re
from typing import Any

from connect_labs.semantic.compiler import (
    _CUBE_REF,
    _property_levels,
    _subst_constants,
    compile_indicator_sql,
    compile_measures,
)

_IDENT = re.compile(r"\b([a-z_][a-z0-9_]*)\b")
_CONST = re.compile(r"(?<!:):([A-Za-z][A-Za-z0-9_]*)")

PLACEHOLDER_VISIT_SQL = "SELECT * FROM pipeline_visit_rows /* Layer 1: the workflow's pipeline schema */"


class UnknownIndicator(KeyError):
    pass


def _measure_index(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["name"]: m for m in registry["measures"]}


def _resolve_indicator(registry: dict[str, Any], indicator: str) -> dict[str, Any]:
    """`N15` / `n15` / a measure name -> the top-level measure that carries it."""
    by_name = _measure_index(registry)
    key = indicator.strip()
    if key in by_name:
        return by_name[key]
    if key.lower() in by_name:
        return by_name[key.lower()]
    for m in registry["measures"]:
        if str((m.get("meta") or {}).get("indicator", "")).lower() == key.lower():
            return m
    raise UnknownIndicator(indicator)


def _referenced_measures(measure: dict[str, Any], by_name: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The component measures a `{name}` reference chain pulls in (numerator, denominator...)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(m: dict[str, Any]) -> None:
        for ref in re.findall(r"\{([a-z0-9_]+)\}", m.get("sql") or ""):
            if ref == "CUBE" or ref in seen or ref not in by_name:
                continue
            seen.add(ref)
            out.append(by_name[ref])
            walk(by_name[ref])

    walk(measure)
    return out


def _cube_refs(measures: list[dict[str, Any]]) -> set[str]:
    cols: set[str] = set()
    for m in measures:
        frags = [m.get("sql") or ""] + [f["sql"] for f in (m.get("filters") or [])]
        for frag in frags:
            cols.update(_CUBE_REF.findall(frag))
    return cols


def explain(
    props_doc: dict[str, Any],
    registry: dict[str, Any],
    indicator: str,
    *,
    scope: str = "programme",
    llo_map: dict[Any, str] | None = None,
    settings: dict[str, dict[Any, bool]] | None = None,
    as_of: str = "CURRENT_DATE",
) -> dict[str, Any]:
    top = _resolve_indicator(registry, indicator)
    by_name = _measure_index(registry)
    components = _referenced_measures(top, by_name)
    compiled = compile_measures(registry)

    constants = props_doc.get("constants") or {}
    props = {p["name"]: p for p in props_doc.get("properties") or []}
    aggs = {a["name"]: a for a in props_doc.get("aggregates") or []}
    derived = {d["name"]: d for d in (props_doc.get("weight_series") or {}).get("derived") or []}

    # Transitive closure from the measure's {CUBE}.x refs down through the
    # property chain into aggregates and weight-series derivations.
    want = set(_cube_refs([top] + components))
    prop_closure: set[str] = set()
    agg_used: set[str] = set()
    derived_used: set[str] = set()
    const_used: set[str] = set()
    frontier = list(want)
    while frontier:
        name = frontier.pop()
        if name in props:
            if name in prop_closure:
                continue
            prop_closure.add(name)
            sql = props[name]["sql"]
            const_used.update(_CONST.findall(sql))
            for ident in set(_IDENT.findall(sql.lower())):
                if ident != name and (ident in props or ident in aggs or ident in derived):
                    frontier.append(ident)
        elif name in aggs:
            agg_used.add(name)
            const_used.update(_CONST.findall(aggs[name]["sql"]))
        elif name in derived:
            derived_used.add(name)
            const_used.update(_CONST.findall(derived[name]["sql"]))

    # Properties in evaluation order, restricted to the closure.
    ordered: list[dict[str, Any]] = []
    for level in _property_levels(list(props.values())):
        for p in level:
            if p["name"] in prop_closure:
                ordered.append(
                    {
                        "name": p["name"],
                        "type": p.get("type"),
                        "sql": _subst_constants(p["sql"], {**constants, "as_of": as_of}).strip(),
                        "notes": p.get("notes"),
                    }
                )

    def _frag(m: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": m["name"],
            "type": m["type"],
            "sql": m.get("sql"),
            "filters": [f["sql"] for f in (m.get("filters") or [])],
            "compiled": compiled.get(m["name"]),
        }

    ws = props_doc.get("weight_series") or {}
    return {
        "indicator": (top.get("meta") or {}).get("indicator") or top["name"],
        "measure": top["name"],
        "title": top.get("title"),
        "meta": top.get("meta") or {},
        "expression": _frag(top),
        "components": [_frag(m) for m in components],
        "properties": ordered,
        "aggregates": [{"name": n, "sql": _subst_constants(aggs[n]["sql"], constants)} for n in sorted(agg_used)],
        "weight_series": {
            "day_collapse": ws.get("day_collapse"),
            "valid": _subst_constants(ws["valid"], constants) if ws.get("valid") else None,
            "seed_reading": ws.get("seed_reading"),
            "derived": [
                {"name": n, "sql": _subst_constants(derived[n]["sql"], constants).strip()}
                for n in sorted(derived_used)
            ],
        },
        "constants": {k: constants[k] for k in sorted(const_used) if k in constants},
        "scope": scope,
        "compiled_sql": compile_indicator_sql(
            props_doc,
            registry,
            PLACEHOLDER_VISIT_SQL,
            scope=scope,
            as_of=as_of,
            llo_map=llo_map,
            settings=settings,
        ),
        "layer1": (
            "Visit rows come from the workflow's pipelines (children + visits), whose schema is the "
            "Layer-1 definition: read it with pipeline_get on the workflow's pipeline_sources."
        ),
    }
