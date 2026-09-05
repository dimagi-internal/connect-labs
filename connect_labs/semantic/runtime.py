"""Execute the semantic registry in-process — the consumer it never had.

WHY THIS MODULE EXISTS

The registry, the compiler, Layer 1 and the gates were all built and proven: five
parity tests execute the compiled SQL against real Postgres and agree with the
JavaScript dashboard on 5,698 checks at four scopes. And then nothing imported any
of it. A grep for ``connect_labs.semantic`` across the application returned the
package itself and its own tests, and nothing else — so the workflow that carries
the name "SQL semantic layer" was serving numbers frozen into a saved run, not
numbers this code produced.

A layer that is correct and unreachable is worth roughly what an unwritten one is,
and the gap was never a hard one: ``labs/analysis/backends/sql/backend.py`` says in
its first line that it "uses PostgreSQL tables for caching AND computation", and
``execute_entity_aggregation`` runs its query through a plain
``django.db.connection.cursor()``. The compiled SQL wants exactly that — the visit
cache is already in this database. All that was missing was something to hand one
to the other.

WHAT THIS DELIBERATELY DOES NOT DO

It does not replace the browser's Layer 2/3. The existing dashboard computes the
C-series in JavaScript and its parity guarantee is stated against that; swapping
the engine underneath a working dashboard is a separate decision with its own
risk. This is additive: callers ask for the indicators they want, and get rows.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from connect_labs.semantic.compiler import compile_indicator_sql, compile_rollup_sql
from connect_labs.semantic.layer1 import build_visit_sql

logger = logging.getLogger(__name__)

REGISTRY_ROOT = Path(__file__).resolve().parent / "registry"

# Indicator prefixes the registry carries. "C" is the workbook's original series,
# "N" is Neal Lesh's demo compute spec. A caller asking for one must not silently
# receive the other's columns: they answer different questions and disagree on
# maturity and growth bands by design.
SERIES_PREFIXES = ("C", "N")

# A measure references another as `{other_measure}` inside its sql. `{CUBE}` is the
# cube self-reference, not a measure, and must not be followed.
_MEASURE_REF = re.compile(r"\{([a-z][a-z0-9_]*)\}")


class SemanticRuntimeError(RuntimeError):
    """Raised when the registry cannot be loaded or the query cannot run."""


def load_registry(name: str = "kmc") -> tuple[dict[str, Any], dict[str, Any]]:
    """(properties_doc, indicators_doc) for a registry directory."""
    root = REGISTRY_ROOT / name
    if not root.is_dir():
        raise SemanticRuntimeError(f"no semantic registry named {name!r} at {root}")
    try:
        props = yaml.safe_load((root / "properties.yml").read_text())
        inds = yaml.safe_load((root / "indicators.yml").read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise SemanticRuntimeError(f"registry {name!r} did not load: {exc}") from exc
    return props, inds


def filter_to_series(registry: dict[str, Any], series: str) -> dict[str, Any]:
    """A copy of the registry carrying ONE indicator series and nothing else.

    An indicator is three measures — a numerator, a denominator, and the value over
    them — and only the value carries ``meta``. So "keep everything without meta"
    is the obvious rule and the wrong one: it retains every OTHER series' numerators
    and denominators too, which then compile into the result as columns nobody asked
    for. Measured on the first run of this function: the N-series result came back
    carrying c01_numerator and its siblings.

    Instead the parts are found by following references. A kept measure's ``sql``
    names the measures it is built from as ``{other_measure}``, so the reachable set
    is a transitive walk from the indicators that matched, and anything outside it
    belongs to a series that was not asked for.
    """
    series = series.upper()
    if series not in SERIES_PREFIXES:
        raise SemanticRuntimeError(f"unknown indicator series {series!r}; known: {SERIES_PREFIXES}")

    by_name = {m["name"]: m for m in registry.get("measures", []) if m.get("name")}

    roots = [
        m
        for m in registry.get("measures", [])
        if m.get("meta") and str(m["meta"].get("indicator", "")).upper().startswith(series)
    ]

    reachable: set[str] = set()
    queue = []
    for m in roots:
        name = m.get("name")
        if not name:
            continue
        queue.append(name)
        # An indicator OWNS its numerator and denominator by convention, even when
        # its value expression does not reference both -- a count indicator's sql is
        # just {x_numerator}. Following references alone would drop the denominator,
        # and the registry's whole no-bare-numbers rule is that a value is never
        # reportable without it.
        queue.append(name + "_numerator")
        queue.append(name + "_denominator")
    while queue:
        name = queue.pop()
        if name in reachable or name not in by_name:
            continue
        reachable.add(name)
        sql = str(by_name[name].get("sql") or "")
        for ref in _MEASURE_REF.findall(sql):
            if ref in by_name and ref not in reachable:
                queue.append(ref)

    out = dict(registry)
    out["measures"] = [m for m in registry.get("measures", []) if m.get("name") in reachable]
    return out


def _rows_from_cursor(cursor) -> list[dict[str, Any]]:
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def evaluate(
    pipeline_schema: dict[str, Any] | None,
    opportunity_ids: list[int],
    *,
    visit_sql: str | None = None,
    registry_name: str = "kmc",
    series: str | None = None,
    scope: str = "programme",
    scopes: list[str] | None = None,
    as_of: str = "CURRENT_DATE",
    llo_map: dict[Any, str] | None = None,
    settings: dict[str, dict[Any, bool]] | None = None,
    connection=None,
) -> list[dict[str, Any]]:
    """Compile the registry and RUN it, returning one dict per result row.

    Pass ``scopes`` for several scopes in one pass — that routes to
    ``compile_rollup_sql``, whose GROUPING SETS collapse exists precisely because
    calling the single-scope form per scope re-runs the whole Layer 1 extraction
    each time (28.2s + 31.2s + 27.3s for three scopes, measured on opp 10042).
    Asking for scopes one at a time is the slow path; it is available, not default.

    ``series`` restricts the result to one indicator family. Omit it and you get
    the registry as written, which is both.
    """
    if visit_sql is None and not opportunity_ids:
        raise SemanticRuntimeError("evaluate() needs at least one opportunity id")

    props_doc, registry = load_registry(registry_name)
    if series:
        registry = filter_to_series(registry, series)

    # Layer 1 is generated from the pipeline's OWN schema, never hand-written --
    # a paraphrase of it dropped 7 of 10 danger-sign paths once and the three
    # indicators over those paths were exactly the ones that disagreed. An explicit
    # visit_sql is for a caller that already holds one (the parity fixture does);
    # everything else goes through the generator.
    if visit_sql is None:
        if pipeline_schema is None:
            raise SemanticRuntimeError("evaluate() needs a pipeline_schema, or an explicit visit_sql")
        visit_sql = build_visit_sql(pipeline_schema, opportunity_ids)

    if scopes:
        sql = compile_rollup_sql(
            props_doc, registry, visit_sql, scopes=scopes, as_of=as_of, llo_map=llo_map, settings=settings
        )
    else:
        sql = compile_indicator_sql(
            props_doc, registry, visit_sql, scope=scope, as_of=as_of, llo_map=llo_map, settings=settings
        )

    if connection is None:
        from django.db import connection as django_connection

        connection = django_connection

    logger.info(
        "[semantic] evaluating registry=%s series=%s scopes=%s opps=%d",
        registry_name,
        series or "all",
        scopes or [scope],
        len(opportunity_ids),
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return _rows_from_cursor(cursor)
    except Exception as exc:
        # The compiled statement is long and the useful part is which column or
        # relation was missing, not the whole CTE chain. Log the SQL at debug and
        # keep the message short enough to read.
        logger.debug("[semantic] failing SQL:\n%s", sql)
        raise SemanticRuntimeError(f"semantic query failed: {exc}") from exc
