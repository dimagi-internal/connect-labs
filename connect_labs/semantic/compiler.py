"""Compile a Cube-syntax indicator registry into one SQL statement.

WHY
The kmc_programme_metrics dashboard derives Layer 2 (case properties) and
aggregates Layer 3 (indicators) in the BROWSER, in JavaScript, over one row per
baby carrying a weights array. That forces the in-memory shape: indicators cannot
be pushed into SQL because none of the properties they reference exist in the
database to GROUP BY. This compiler removes that constraint -- properties become
columns, indicators become a GROUP BY, and the browser receives aggregates.

CONTRACT
    compile_indicator_sql(props, registry, visit_sql, scope) -> str

`visit_sql` is the pipeline's own visit_extraction_sql, used verbatim as the inner
query. Layer 1 (JSON paths -> named columns) therefore stays where it already
works; this compiler only owns Layer 2 and Layer 3.

The emitted statement is:

    visits      -- the pipeline's extraction, unchanged
    weight_days -- one weight per (baby, day), implausible readings dropped
    weight_agg  -- window functions over that series (swing check, growth window)
    visit_agg   -- per-baby aggregates
    props       -- Layer 2, one row per baby
    SELECT <scope cols>, <indicator measures> FROM props GROUP BY <scope cols>
"""

from __future__ import annotations

import re
from typing import Any

# Cube's real measure types. Anything outside this set is rejected at load: the
# whole point of borrowing Cube's notation is that we do not invent dialect.
CUBE_MEASURE_TYPES = frozenset(
    {
        "count",
        "count_distinct",
        "count_distinct_approx",
        "sum",
        "avg",
        "min",
        "max",
        "number",
        "string",
        "time",
        "boolean",
    }
)

# Aggregating measure types -- these become an aggregate over `props`.
_AGGREGATING = {
    "count": "COUNT",
    "count_distinct": "COUNT(DISTINCT ",
    "sum": "SUM",
    "avg": "AVG",
    "min": "MIN",
    "max": "MAX",
}

SCOPES: dict[str, list[str]] = {
    "programme": [],
    "opportunity": ["opportunity_id"],
    "llo": ["llo"],
    "flw": ["opportunity_id", "username"],
    "month": ["cohort_month"],
}

_CUBE_REF = re.compile(r"\{CUBE\}\.([a-zA-Z_][a-zA-Z0-9_]*)")
# `{CUBE}` is the column namespace, not a measure -- exclude it or a measure sql
# that references a column (C17 does) tries to resolve a measure named CUBE.
_MEASURE_REF = re.compile(r"\{(?!CUBE\b)([a-zA-Z_][a-zA-Z0-9_]*)\}")


class RegistryError(ValueError):
    """A registry that cannot be compiled. Raised loudly rather than guessed around."""


def _subst_constants(sql: str, constants: dict[str, Any]) -> str:
    """Replace :NAME placeholders with literal constants."""

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in constants:
            raise RegistryError(f"unknown constant :{key} in SQL fragment: {sql!r}")
        return str(constants[key])

    # (?<!:) so a Postgres cast (`::date`) is not mistaken for a placeholder.
    return re.sub(r"(?<!:):([A-Za-z][A-Za-z0-9_]*)", repl, sql)


def _cube_to_props(sql: str) -> str:
    """`{CUBE}.col` -> `props.col`."""
    return _CUBE_REF.sub(r"props.\1", sql)


def _filter_clause(measure: dict[str, Any]) -> str | None:
    filters = measure.get("filters") or []
    if not filters:
        return None
    parts = [f"({_cube_to_props(f['sql'])})" for f in filters]
    return " AND ".join(parts)


def _compile_aggregate(measure: dict[str, Any]) -> str:
    """One aggregating measure -> a SQL aggregate expression over `props`."""
    mtype = measure["type"]
    inner = measure.get("sql")
    where = _filter_clause(measure)

    if mtype == "count":
        expr = "COUNT(*)"
    elif mtype == "count_distinct":
        if not inner:
            raise RegistryError(f"{measure['name']}: count_distinct needs sql")
        expr = f"COUNT(DISTINCT {_cube_to_props(inner)})"
    else:
        fn = _AGGREGATING.get(mtype)
        if fn is None:
            raise RegistryError(f"{measure['name']}: {mtype} is not an aggregating type")
        if not inner:
            raise RegistryError(f"{measure['name']}: {mtype} needs sql")
        expr = f"{fn}({_cube_to_props(inner)})"

    if where:
        expr = f"{expr} FILTER (WHERE {where})"
    return expr


def compile_measures(registry: dict[str, Any]) -> dict[str, str]:
    """Every measure -> its SQL expression, with {measure} references resolved."""
    by_name = {m["name"]: m for m in registry["measures"]}

    for m in registry["measures"]:
        if m["type"] not in CUBE_MEASURE_TYPES:
            raise RegistryError(
                f"{m['name']}: type {m['type']!r} is outside Cube's measure "
                f"vocabulary. Borrowing the notation means not inventing dialect."
            )

    compiled: dict[str, str] = {}
    resolving: set[str] = set()

    def resolve(name: str) -> str:
        if name in compiled:
            return compiled[name]
        if name in resolving:
            raise RegistryError(f"circular measure reference at {name!r}")
        measure = by_name.get(name)
        if measure is None:
            raise RegistryError(f"measure {name!r} referenced but not defined")
        resolving.add(name)

        if measure["type"] == "number":
            sql = measure.get("sql")
            if not sql:
                raise RegistryError(f"{name}: type number needs sql")
            # Resolve sibling measure references first, then {CUBE} columns.
            sql = _MEASURE_REF.sub(lambda m: f"({resolve(m.group(1))})", sql)
            expr = _cube_to_props(sql)
        else:
            expr = _compile_aggregate(measure)

        resolving.discard(name)
        compiled[name] = expr
        return expr

    for m in registry["measures"]:
        resolve(m["name"])
    return compiled


def validate(props_doc: dict[str, Any], registry: dict[str, Any]) -> list[str]:
    """Every {CUBE}.col must resolve to a real property or aggregate.

    This is the check a Cube runtime would do for us and will not, because we do
    not run one. Without it a typo silently produces a NULL column.
    """
    known = {p["name"] for p in props_doc["properties"]}
    known |= {a["name"] for a in props_doc["aggregates"]}
    known |= {d["name"] for d in props_doc["weight_series"]["derived"]}
    known |= {"opportunity_id", "username", "llo", "baby_id", "cohort_month", "num_visits"}

    problems: list[str] = []
    for m in registry["measures"]:
        frags = [m.get("sql") or ""] + [f["sql"] for f in (m.get("filters") or [])]
        for frag in frags:
            for col in _CUBE_REF.findall(frag):
                if col not in known:
                    problems.append(f"{m['name']}: unknown column {{CUBE}}.{col}")
    return problems


def _property_levels(properties: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group properties into dependency levels.

    A property may reference other properties (`eligible` uses `started` and
    `days_since_first_visit`). A single SELECT cannot self-reference, so each level
    becomes its own CTE that can see everything the levels before it defined. The
    JavaScript gets this for free from sequential assignment; SQL has to be told.
    """
    names = {p["name"] for p in properties}
    deps: dict[str, set[str]] = {}
    for p in properties:
        refs = set(re.findall(r"\b([a-z_][a-z0-9_]*)\b", p["sql"]))
        deps[p["name"]] = (refs & names) - {p["name"]}

    levels: list[list[dict[str, Any]]] = []
    placed: set[str] = set()
    remaining = list(properties)
    while remaining:
        level = [p for p in remaining if deps[p["name"]] <= placed]
        if not level:
            stuck = ", ".join(sorted(p["name"] for p in remaining))
            raise RegistryError(f"circular property dependency among: {stuck}")
        levels.append(level)
        placed |= {p["name"] for p in level}
        remaining = [p for p in remaining if p["name"] not in placed]
    return levels


def _build_ctes(
    props_doc: dict[str, Any],
    registry: dict[str, Any],
    visit_sql: str,
    as_of: str,
) -> tuple[str, dict[str, str]]:
    """The Layer 1 -> Layer 2 CTE chain, plus every measure's compiled expression.

    Shared by both entry points so a multi-scope rollup reuses ONE extraction
    instead of re-running the whole chain per scope.
    """
    problems = validate(props_doc, registry)
    if problems:
        raise RegistryError("registry does not validate:\n  " + "\n  ".join(problems))

    consts = dict(props_doc["constants"])
    consts["as_of"] = as_of

    def C(sql: str) -> str:
        return _subst_constants(sql, consts)

    ws = props_doc["weight_series"]
    agg_cols = ",\n    ".join(f"{C(a['sql'])} AS {a['name']}" for a in props_doc["aggregates"])
    wderived = ",\n    ".join(f"{C(d['sql'])} AS {d['name']}" for d in ws["derived"])

    levels = _property_levels(props_doc["properties"])
    prop_ctes = []
    prev = "base_m"
    for i, level in enumerate(levels):
        cols = ",\n           ".join(f"({C(p['sql'])}) AS {p['name']}" for p in level)
        name = f"props_{i}"
        prop_ctes.append(f"{name} AS (\n    SELECT {prev}.*,\n           {cols}\n    FROM {prev}\n)")
        prev = name
    prop_cte_sql = ",\n".join(prop_ctes)
    final_props = prev

    compiled = compile_measures(registry)

    ctes = f"""WITH visits AS (
{visit_sql}
),
weight_days AS (
    SELECT baby_case_id AS baby_id,
           visit_date::date AS day,
           {ws['day_collapse'].replace('weight_g', 'weights')} AS w
    FROM visits
    WHERE baby_case_id IS NOT NULL
      AND {C(ws['valid']).replace('weight_g', 'weights')}
    GROUP BY 1, 2
),
baby_first AS (
    SELECT baby_case_id AS baby_id, MIN(visit_date)::date AS first_visit_day
    FROM visits
    WHERE baby_case_id IS NOT NULL
    GROUP BY 1
),
weight_seq AS (
    -- age_days is measured from the baby's FIRST VISIT, not its first weight
    -- reading. The render code uses `(p.day - fv) / DAY` where fv is the first
    -- visit; anchoring on the first weighing instead shifts the growth window for
    -- every baby whose first visit carried no weight, and silently changes
    -- C09-C13. Caught only on real data.
    SELECT wd.baby_id, wd.day, wd.w,
           LAG(wd.w) OVER (PARTITION BY wd.baby_id ORDER BY wd.day) AS prev_w,
           (wd.day - bf.first_visit_day)::int AS age_days
    FROM weight_days wd
    JOIN baby_first bf USING (baby_id)
),
weight_agg AS (
    SELECT baby_id,
    {wderived}
    FROM weight_seq
    GROUP BY baby_id
),
visit_agg AS (
    SELECT baby_case_id AS baby_id,
           MIN(opportunity_id) AS opportunity_id,
           MIN(username) AS username,
    {agg_cols}
    FROM visits
    WHERE baby_case_id IS NOT NULL
    GROUP BY baby_case_id
),
base_m AS (
    SELECT v.*, w.*, DATE_TRUNC('month', v.reg_date)::date AS cohort_month
    FROM visit_agg v
    LEFT JOIN weight_agg w USING (baby_id)
),
{prop_cte_sql},
props AS (SELECT * FROM {final_props})"""
    return ctes.strip(), compiled


def _measure_cols(registry: dict[str, Any], compiled: dict[str, str]) -> str:
    return ",\n    ".join(f"{compiled[m['name']]} AS {m['name']}" for m in registry["measures"])


def compile_indicator_sql(
    props_doc: dict[str, Any],
    registry: dict[str, Any],
    visit_sql: str,
    scope: str = "programme",
    as_of: str = "CURRENT_DATE",
) -> str:
    """One statement for ONE scope. Prefer compile_rollup_sql for several."""
    if scope not in SCOPES:
        raise RegistryError(f"unknown scope {scope!r}; expected one of {sorted(SCOPES)}")
    ctes, compiled = _build_ctes(props_doc, registry, visit_sql, as_of)
    scope_cols = SCOPES[scope]
    scope_select = "".join(f"props.{c},\n    " for c in scope_cols)
    group_by = ("GROUP BY " + ", ".join(f"props.{c}" for c in scope_cols)) if scope_cols else ""
    return f"""{ctes}
SELECT
    {scope_select}COUNT(*) AS n_cases,
    {_measure_cols(registry, compiled)}
FROM props
{group_by}
""".strip()


def compile_rollup_sql(
    props_doc: dict[str, Any],
    registry: dict[str, Any],
    visit_sql: str,
    scopes: list[str] | None = None,
    as_of: str = "CURRENT_DATE",
) -> str:
    """EVERY scope from ONE pass over props, via GROUPING SETS.

    Calling compile_indicator_sql once per scope re-runs the entire Layer 1
    extraction each time -- the JSONB COALESCE chain over every visit, the weight
    window functions, the whole property chain -- for results that all derive from
    the same `props` rows. Measured on opportunity 10042: 28.2s + 31.2s + 27.3s for
    three scopes, against a browser implementation that fetches once and slices in
    memory. GROUPING SETS collapses that to a single pass, which is the only
    version where pushing this into SQL is an improvement rather than a regression.

    Each output row carries a `scope` label naming the grouping set that produced it.
    """
    scopes = scopes or ["programme", "opportunity", "flw", "month"]
    unknown = [s for s in scopes if s not in SCOPES]
    if unknown:
        raise RegistryError(f"unknown scope(s) {unknown}; expected from {sorted(SCOPES)}")

    ctes, compiled = _build_ctes(props_doc, registry, visit_sql, as_of)

    all_cols: list[str] = []
    for sc in scopes:
        for c in SCOPES[sc]:
            if c not in all_cols:
                all_cols.append(c)

    sets = ", ".join(
        ("(" + ", ".join(f"props.{c}" for c in SCOPES[sc]) + ")") if SCOPES[sc] else "()" for sc in scopes
    )
    # GROUPING() reports 0 when a column participated in the row's grouping set,
    # which is how each row is labelled back to the scope that produced it.
    label_cases = "\n        ".join(
        "WHEN "
        + (" AND ".join(f"GROUPING(props.{c}) = {0 if c in SCOPES[sc] else 1}" for c in all_cols) or "TRUE")
        + f" THEN '{sc}'"
        for sc in scopes
    )
    col_select = "".join(f"props.{c},\n    " for c in all_cols)

    return f"""{ctes}
SELECT
    CASE
        {label_cases}
        ELSE 'other'
    END AS scope,
    {col_select}COUNT(*) AS n_cases,
    {_measure_cols(registry, compiled)}
FROM props
GROUP BY GROUPING SETS ({sets})
""".strip()
