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
        "english": english(registry, props_doc, top["name"]),
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


# ── English ────────────────────────────────────────────────────────────────
# A definition a programme manager can read, rendered FROM the SQL so it cannot
# drift from the number. `meta.plain` on a measure is an authored override (the
# demo compute spec's own wording for the N-series); the mechanical rendering is
# always returned beside it so the two can be compared.

_WORDS = [
    (re.compile(r"\{CUBE\}\."), ""),
    (re.compile(r"\bIS NOT NULL\b"), "is recorded"),
    (re.compile(r"\bIS NULL\b"), "is not recorded"),
    (re.compile(r"\bNOT\b"), "not"),
    (re.compile(r"\bAND\b"), "and"),
    (re.compile(r"\bOR\b"), "or"),
    (re.compile(r"\s*=\s*'([^']+)'"), r" is \1"),
    (re.compile(r"\s*>=\s*"), " is at least "),
    (re.compile(r"\s*<=\s*"), " is at most "),
    (re.compile(r"\s*>\s*"), " is more than "),
    (re.compile(r"\s*<\s*"), " is less than "),
]


def _words(sql: str) -> str:
    out = sql.strip()
    for pat, rep in _WORDS:
        out = pat.sub(rep, out)
    return out.replace("_", " ").strip()


def _component_words(m: dict[str, Any]) -> str:
    """One aggregating measure -> 'number of babies where ...' and friends."""
    where = " and ".join(f"({_words(f['sql'])})" for f in (m.get("filters") or []))
    where = where.strip("()") if where.count("(") == 1 else where
    scope = f" where {where}" if where else ""
    mtype = m.get("type")
    inner = m.get("sql") or ""
    if mtype == "count":
        return f"the number of babies{scope}"
    if mtype == "count_distinct":
        return f"the number of distinct {_words(inner)}{scope}"
    if mtype == "sum":
        guarded = re.match(r"\s*CASE WHEN (.+?) THEN (.+?) ELSE 0 END\s*$", inner, re.S)
        if guarded and not scope:
            return f"the sum of {_words(guarded.group(2))} over babies where {_words(guarded.group(1))}"
        return f"the sum of {_words(inner)} over babies{scope}"
    if mtype == "avg":
        return f"the mean of {_words(inner)} over babies{scope}"
    if mtype == "min":
        return f"the smallest {_words(inner)} over babies{scope}"
    if mtype == "max":
        return f"the largest {_words(inner)} over babies{scope}"
    if "PERCENTILE_CONT(0.5)" in inner:
        col = re.search(r"ORDER BY \{CUBE\}\.([a-z0-9_]+)", inner)
        return f"the median {_words(col.group(1)) if col else 'value'} over babies{scope}"
    return f"{_words(inner)}{scope}"


def english(registry: dict[str, Any], props_doc: dict[str, Any], indicator: str) -> dict[str, Any]:
    """{plain, definition, reads}: the authored sentence if any, the mechanical one
    always, and one line per property the definition leans on."""
    top = _resolve_indicator(registry, indicator)
    by_name = _measure_index(registry)
    meta = top.get("meta") or {}
    unit = meta.get("unit") or ""
    expr = top.get("sql") or ""
    refs = [r for r in re.findall(r"\{([a-z0-9_]+)\}", expr) if r in by_name]
    parts = {r: _component_words(by_name[r]) for r in refs}

    if len(refs) == 2 and expr.startswith("100.0 *"):
        definition = f"{parts[refs[0]]}, as a percentage of {parts[refs[1]]}."
    elif len(refs) == 2 and "NULLIF" in expr:
        definition = f"{parts[refs[0]]}, divided by {parts[refs[1]]}."
    elif len(refs) == 1 and by_name[refs[0]].get("type") == "count":
        definition = f"{parts[refs[0]][0].upper()}{parts[refs[0]][1:]}."
    elif len(refs) == 1:
        definition = f"{parts[refs[0]][0].upper()}{parts[refs[0]][1:]}."
    else:
        definition = _words(expr)
    definition = definition[0].upper() + definition[1:]
    if unit and unit not in ("%", "n"):
        definition = definition.rstrip(".") + f" ({unit})."
    if meta.get("min_denominator"):
        definition += f" Shown only when the denominator is at least {meta['min_denominator']}."

    props = {p["name"]: p for p in props_doc.get("properties") or []}
    constants = props_doc.get("constants") or {}
    reads = []
    for name in sorted(_cube_refs([top] + _referenced_measures(top, by_name))):
        if name in props:
            p = props[name]
            reads.append(
                {
                    "name": name,
                    "means": (p.get("notes") or "").strip() or None,
                    "sql": _subst_constants(p["sql"], {**constants, "as_of": "CURRENT_DATE"}).strip(),
                }
            )
    return {
        "plain": meta.get("plain"),
        "definition": definition,
        "reads": reads,
    }


def to_markdown(explanations: list[dict[str, Any]], *, registry_label: str = "") -> str:
    """Every indicator as a Markdown section: English first, then the SQL chain."""
    lines = [f"# Indicator definitions{(' — ' + registry_label) if registry_label else ''}", ""]
    lines.append(
        "Each indicator: the plain-English definition (authored where one exists, otherwise "
        "rendered from the SQL), the properties it reads, the measure as compiled, and the "
        "constants substituted. `props` is one row per baby; Layer 1 (the visit rows) is the "
        "workflow's pipeline schema."
    )
    lines.append("")
    for e in explanations:
        en = e.get("english") or {}
        lines.append(f"## {e['indicator']} · {e.get('title') or e['measure']}")
        lines.append("")
        if en.get("plain"):
            lines.append(en["plain"])
            lines.append("")
        lines.append(f"**Definition (from the SQL):** {en.get('definition', '')}")
        lines.append("")
        meta = e.get("meta") or {}
        bits = [
            f"unit `{meta['unit']}`" if meta.get("unit") else "",
            f"direction `{meta['direction']}`" if meta.get("direction") else "",
            f"bands `{meta['bands']}`" if meta.get("bands") else "",
        ]
        bits = [b for b in bits if b]
        if bits:
            lines.append("_" + " · ".join(bits) + "_")
            lines.append("")
        lines.append("**Measure:**")
        lines.append("```sql")
        lines.append(f"-- {e['measure']}")
        lines.append(str((e.get("expression") or {}).get("compiled") or (e.get("expression") or {}).get("sql") or ""))
        for c in e.get("components") or []:
            lines.append(f"-- {c['name']}")
            lines.append(str(c.get("compiled") or c.get("sql") or ""))
        lines.append("```")
        if e.get("properties"):
            lines.append("")
            lines.append("**Properties it reads (one row per baby, in evaluation order):**")
            lines.append("```sql")
            for p in e["properties"]:
                if p.get("notes"):
                    lines.append(f"-- {p['name']}: {p['notes']}")
                lines.append(f"{p['name']} = {p['sql']}")
            lines.append("```")
        ws = e.get("weight_series") or {}
        if ws.get("derived"):
            lines.append("")
            lines.append("**Weight-series derivations (window over each baby's weighings):**")
            lines.append("```sql")
            for d in ws["derived"]:
                lines.append(f"{d['name']} = {d['sql']}")
            lines.append("```")
        if e.get("constants"):
            lines.append("")
            lines.append("**Constants:** " + ", ".join(f"`{k}` = {v}" for k, v in e["constants"].items()))
        lines.append("")
    return "\n".join(lines)


def to_sql(explanations: list[dict[str, Any]], *, registry_label: str = "") -> str:
    """One .sql file: a commented header per indicator, then the compiled statement."""
    out = [f"-- Indicator definitions{(' -- ' + registry_label) if registry_label else ''}", "--"]
    for e in explanations:
        en = e.get("english") or {}
        out.append(f"-- {e['indicator']} {e.get('title') or ''}")
        if en.get("plain"):
            out.append(f"--   {en['plain']}")
        out.append(f"--   {en.get('definition', '')}")
        out.append(f"--   measure: {(e.get('expression') or {}).get('compiled') or ''}")
        for c in e.get("components") or []:
            out.append(f"--   {c['name']}: {c.get('compiled') or c.get('sql') or ''}")
        out.append("--")
    if explanations:
        out.append("")
        out.append(f"-- Full statement, scope = {explanations[0].get('scope')}; replace pipeline_visit_rows with the")
        out.append("-- workflow's Layer-1 pipeline query (pipeline_get on its pipeline_sources).")
        out.append(explanations[0]["compiled_sql"])
    return "\n".join(out) + "\n"
