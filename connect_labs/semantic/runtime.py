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


def load_deployment(name: str = "kmc") -> tuple[dict[Any, str], dict[str, dict[Any, bool]]]:
    """(llo_map, settings) for a registry directory; empty pair when undeclared.

    The compiler needs both and can derive neither. `llo` is not a column on a visit
    row, so the `llo` scope and every llo-scoped suppression rule are compiled from
    a CASE over this map; and the workbook's credibility gates are typed human
    judgements that exist in no table.

    Until this existed the only copy lived in the browser (`LLO_OF`,
    `MORTALITY_CREDIBLE`, `COMPLETION_CREDIBLE` in kmc_programme_metrics_render.js),
    which is why `semantic_indicators_api` could not serve the `llo` scope at all and
    -- silently -- emitted no suppression columns for any scope.
    """
    facts = load_deployment_facts(name)
    return facts["llo_map"], facts["settings"]


def load_deployment_facts(name: str = "kmc") -> dict[str, Any]:
    """Every deployment fact for an on-disk registry: llo_map, settings, app_asks, asks_as."""
    root = REGISTRY_ROOT / name / "deployment.yml"
    if not root.is_file():
        return normalise_deployment_facts(None)
    try:
        doc = yaml.safe_load(root.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise SemanticRuntimeError(f"deployment facts for {name!r} did not load: {exc}") from exc
    return normalise_deployment_facts(doc)


def normalise_deployment_facts(doc: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce deployment facts to the types their consumers compare against.

    Opportunity ids arrive from a query as ints. From YAML they are usually ints
    already; from a JSON record they are ALWAYS strings, because JSON object keys
    can only be strings. An llo_map keyed by "10021" silently matches no row and
    every LLO comes back NULL, so this is not defensive tidying -- it is the
    difference between the llo scope working and returning nothing.

    The two directions differ on purpose. `llo_map` is compared against a row's
    `opportunity_id`, so its keys are INTS. `app_asks` is looked up by an
    availability gate that has always keyed on `str(opportunity_id)`, so its keys
    are STRS -- and YAML reads `10021:` as an int, so without this a map moved out
    of Python would match nothing and every gate would fail open to "asks".

    Returns the whole fact set rather than a pair, because `app_asks` and `asks_as`
    used to be static dicts in `semantic/gates.py`: a workflow bound to a registry
    RECORD read its bands from the record and its availability gates from the repo.
    That is the same split-brain `settings` was unified here to end.
    """
    doc = doc or {}
    return {
        "llo_map": {int(k): str(v) for k, v in (doc.get("llo_map") or {}).items()},
        "settings": {
            str(setting): {str(llo): bool(v) for llo, v in (table or {}).items()}
            for setting, table in (doc.get("settings") or {}).items()
        },
        "app_asks": {
            str(opp): {str(field): bool(v) for field, v in (fields or {}).items()}
            for opp, fields in (doc.get("app_asks") or {}).items()
        },
        "asks_as": {str(k): str(v) for k, v in (doc.get("asks_as") or {}).items()},
    }


def _normalise_deployment(doc: dict[str, Any] | None) -> tuple[dict[Any, str], dict[str, dict[Any, bool]]]:
    """Back-compat pair for callers that only want the compiler's two inputs."""
    facts = normalise_deployment_facts(doc)
    return facts["llo_map"], facts["settings"]


def resolve_registry(
    source: dict[str, Any] | None = None,
    registry_access=None,
) -> tuple[dict[str, Any], dict[str, Any], dict[Any, str], dict[str, dict[Any, bool]], dict[str, Any]]:
    """Return (properties, indicators, llo_map, settings, deployment) for a source.

    `deployment` is the whole fact set (llo_map, settings, app_asks, asks_as). It is
    returned alongside the two the compiler needs because the availability gates read
    `app_asks`, and those used to be static dicts in `semantic/gates.py` -- so a
    workflow bound to a RECORD took its bands from the record and its gates from the
    repo, and the two could disagree with nothing to notice.

    One resolver for both worlds, because a caller should not have to care which
    one it got:

      ``None`` / ``{}``          the built-in on-disk registry ("kmc")
      ``{"name": "kmc"}``        a named on-disk registry
      ``{"registry_id": 41}``    a live record, edited without a deploy

    The on-disk registries stay, and stay the default. They are the seed a record
    is created FROM and the thing that still works when nothing is bound, so
    adding this took nothing away: a workflow that names no registry behaves
    exactly as it did.
    """
    source = source or {}
    registry_id = source.get("registry_id")

    if registry_id is None:
        name = source.get("name") or "kmc"
        props, inds = load_registry(name)
        facts = load_deployment_facts(name)
        return props, inds, facts["llo_map"], facts["settings"], facts

    if registry_access is None:
        raise SemanticRuntimeError(
            f"registry_source names registry_id {registry_id!r}, but no registry_access was "
            f"supplied to read it with. Falling back to the on-disk registry would serve "
            f"numbers from a DIFFERENT definition than the one the workflow asked for, so "
            f"this is an error rather than a default."
        )

    record = registry_access.get_registry(int(registry_id))
    if record is None:
        raise SemanticRuntimeError(f"no semantic registry with id {registry_id}")

    props = record.properties_doc
    inds = record.indicators_doc
    if not props or not inds:
        raise SemanticRuntimeError(
            f"registry {registry_id} is missing its " f"{'properties' if not props else 'indicators'} document"
        )
    facts = normalise_deployment_facts(record.deployment)
    return props, inds, facts["llo_map"], facts["settings"], facts


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

    # Gates are INFRASTRUCTURE, not part of any series, and the reachability walk
    # cannot find them: they carry no `meta`, so they are not roots, and no
    # indicator's sql references them -- they are read alongside a value, not inside
    # it. So filtering to one series dropped every `anyrec_*` column, and the caller
    # got indicators with no way to tell "the app never asked" from "the answer is
    # 0". Measured cost of that distinction when it was first ported: 268 of 5,302
    # per-FLW checks.
    out = dict(registry)
    out["measures"] = [m for m in registry.get("measures", []) if m.get("name") in reachable or m.get("gate")]
    return out


def measure_catalog(registry: dict[str, Any]) -> list[dict[str, Any]]:
    """The display contract for a series: one entry per INDICATOR, in registry order.

    The render needs `bands`, `direction` and `unit` to colour a value, and until now
    it had no way to get them — the C-series carries a hand-kept copy of its own
    registry in the JavaScript, which is exactly the duplication the semantic layer
    exists to end. Serving them alongside the rows keeps the YAML authoritative.

    `bands_source` rides along deliberately: several N-series ranges are DERIVED (from
    the workbook's counterpart, or from the spec's own expected-answers table) rather
    than stated by the spec, and a threshold whose provenance is invisible is one
    nobody can correct.
    """
    by_name = {m.get("name"): m for m in registry.get("measures", []) if m.get("name")}
    # How a value is FORMATTED, which `unit` alone does not decide. C06 and C24 are
    # unit 'n' like C01, but they are means, not counts: rendering them as integers
    # drops a real decimal and looks like a value, not a bug. Every indicator's own
    # measure is `type: number` (it divides two others), so the distinction lives on
    # its NUMERATOR -- count / avg / sum -- which is where the render's old `kind`
    # came from too.
    kinds = {"count": "count", "avg": "mean", "sum": "sumratio"}

    out = []
    for m in registry.get("measures", []):
        meta = m.get("meta")
        if not meta:
            continue
        # Only when the indicator IS its numerator. A rate's numerator is a
        # `count` too -- C09 counts cases -- but C09 is a percentage, and calling
        # it a count would format it as a whole number.
        numerator = {}
        if str(m.get("sql") or "").strip() == "{" + str(m["name"]) + "_numerator}":
            numerator = by_name.get(str(m["name"]) + "_numerator") or {}
        out.append(
            {
                "id": m["name"],
                "indicator": meta.get("indicator"),
                "title": m.get("title"),
                "category": meta.get("category"),
                # Which indicators are headline rather than supporting. The render
                # groups its tables on this; without it every measure reads as equal.
                "prominence": meta.get("prominence"),
                "unit": meta.get("unit"),
                "kind": kinds.get(str(numerator.get("type"))),
                "direction": meta.get("direction"),
                "bands": meta.get("bands"),
                "bands_source": meta.get("bands_source"),
                "min_denominator": meta.get("min_denominator"),
                # The derived inputs this indicator needs present in scope, and the
                # optional thin-coverage rule. Both were hardcoded Python -- a dict
                # in `gates.IND_INPUTS` and `if ind_id == "C16"` with a 0.45 literal
                # in the snapshot builder. Serving them here is what lets ONE generic
                # builder grade any registry, and what lets the render stop keeping
                # its own copy (see this function's docstring).
                "inputs": meta.get("inputs") or [],
                "min_input_coverage": meta.get("min_input_coverage"),
                "coverage_denominator": meta.get("coverage_denominator"),
                # Why a value is unbanded even though it computes. The render shows
                # it as a warning next to the name; dropping it would quietly turn
                # "we have no threshold for this yet" into "this looks fine".
                "tbd_input": meta.get("tbd_input"),
                # C14's table row pools every LLO while the headline card is gated to
                # the credible recorders, so the two legitimately differ. Its own
                # comment in the render put it best: an unlabelled pair reads as a bug.
                "scope_note": meta.get("scope_note"),
                "flw_applicable": meta.get("flw_applicable", False),
            }
        )
    return out


def _rows_from_cursor(cursor) -> list[dict[str, Any]]:
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def evaluate(
    pipeline_schema: dict[str, Any] | None,
    opportunity_ids: list[int],
    *,
    visit_sql: str | None = None,
    extra_fields: dict[str, Any] | None = None,
    registry_name: str = "kmc",
    registry_documents: tuple[dict[str, Any], dict[str, Any]] | None = None,
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

    ``extra_fields`` adds per-visit columns drawn from ANOTHER pipeline, keyed by the
    column name. KMC needs it: the entity pipeline carries the registration fields and
    the visit markers, but the per-visit WEIGHT lives in a separate weight-series
    pipeline, and properties.yml is written against a `weight_g` column. Without it
    the compiled SQL fails with `column "weight_g" does not exist`, hinting at the
    entity pipeline's list-valued `weights` — a different thing entirely.
    """
    if visit_sql is None and not opportunity_ids:
        raise SemanticRuntimeError("evaluate() needs at least one opportunity id")

    # `registry_documents` is how a DB-backed registry reaches the compiler. Without
    # it this function could only ever read the files on disk, so binding a workflow
    # to a live registry would have changed the LABELS the endpoint returns and none
    # of the numbers underneath them -- the worst possible half-fix, because the two
    # would disagree silently.
    if registry_documents is not None:
        props_doc, registry = registry_documents
    else:
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
        # Layer 1 generation reaches into the pipeline engine's own query builder, so
        # it can fail for reasons that have nothing to do with this module. Naming the
        # stage is the whole diagnostic: an opaque 500 from the endpoint says only
        # that something in a five-stage chain broke.
        # build_visit_sql delegates to the pipeline engine's query builder, which
        # wants an AnalysisPipelineConfig OBJECT — not the raw schema dict stored on
        # the definition. Passing the dict fails five frames down as
        # `'dict' object has no attribute 'terminal_stage'`, naming neither the
        # argument nor the caller. Caught here, where the fix is obvious.
        if isinstance(pipeline_schema, dict):
            raise SemanticRuntimeError(
                "evaluate() needs an AnalysisPipelineConfig, not the raw schema dict — "
                "convert it first (PipelineDataAccess._schema_to_config), or pass an "
                "explicit visit_sql"
            )
        try:
            visit_sql = build_visit_sql(pipeline_schema, opportunity_ids, extra_fields=extra_fields)
        except SemanticRuntimeError:
            raise
        except Exception as exc:
            raise SemanticRuntimeError(f"layer 1 generation failed ({type(exc).__name__}): {exc}") from exc

    # Compilation is the second stage that can fail on its own terms — an unknown
    # scope, a measure referencing a column the properties do not define.
    try:
        if scopes:
            sql = compile_rollup_sql(
                props_doc, registry, visit_sql, scopes=scopes, as_of=as_of, llo_map=llo_map, settings=settings
            )
        else:
            sql = compile_indicator_sql(
                props_doc, registry, visit_sql, scope=scope, as_of=as_of, llo_map=llo_map, settings=settings
            )
    except SemanticRuntimeError:
        raise
    except Exception as exc:
        raise SemanticRuntimeError(f"compilation failed ({type(exc).__name__}): {exc}") from exc

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
