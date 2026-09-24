"""Compile a Cube-syntax indicator registry into one SQL statement.

WHY
Indicator dashboards used to derive Layer 2 (per-entity properties) and aggregate
Layer 3 (indicators) in the BROWSER, in JavaScript, over one row per entity. That
forces the in-memory shape: indicators cannot be pushed into SQL because none of
the properties they reference exist in the database to GROUP BY. This compiler
removes that constraint -- properties become columns, indicators become a GROUP
BY, and the browser receives aggregates.

It knows nothing about any one programme. What it counts -- the entity, its key,
its cohort date, an optional per-entity reading series -- is the registry's MODEL
(`semantic/model.py`); KMC's babies and weights are one registry's answers.

CONTRACT
    compile_indicator_sql(props, registry, visit_sql, scope) -> str

`visit_sql` is the pipeline's own visit_extraction_sql, used verbatim as the inner
query. Layer 1 (JSON paths -> named columns) therefore stays where it already
works; this compiler only owns Layer 2 and Layer 3.

The emitted statement is:

    visits      -- the pipeline's extraction, cut at the as-of date
    weight_*    -- only when the registry declares a series: one reading per
                   (entity, day), then window functions over that series
    visit_agg   -- per-entity aggregates
    props       -- Layer 2, one row per entity
    SELECT <scope cols>, <indicator measures> FROM props GROUP BY <scope cols>
"""

from __future__ import annotations

import re
from typing import Any

from connect_labs.semantic import legacy
from connect_labs.semantic.model import RegistryModel, resolve_model

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
    # The monthly trend FOLLOWS THE DRILL. Picking an LLO, an opportunity or a
    # worker re-cohorts the trend to that scope, so a bare `month` -- which groups
    # by cohort_month alone -- answers only the undrilled case. Without these the
    # dashboard's Monthly trend tab cannot be served from the registry at all, and
    # the browser has to keep an indicator engine alive purely to compute them.
    #
    # Free, structurally: GROUPING SETS already takes an arbitrary column tuple per
    # scope, `all_cols` is the union, and each row is labelled back by GROUPING()
    # per column -- so these are three more sets in the SAME single pass, not three
    # more passes. Distinct column sets keep the labels unambiguous.
    "llo_month": ["llo", "cohort_month"],
    "opportunity_month": ["opportunity_id", "cohort_month"],
    "flw_month": ["opportunity_id", "username", "cohort_month"],
    # One row per entity. This is how a measure becomes a CONTRIBUTION: at case
    # scope a rate's denominator is 1 or 0 and its value 100 or 0, a median is the
    # entity's own value, a mean-per-case is the entity's count -- the same measure,
    # the same gates, read one grouping level further down. A worker's case table
    # is this scope filtered to that worker (see `visit_filter`), never a second
    # implementation of the registry in the browser.
    "case": ["opportunity_id", "username", "case_id"],
}

# Scope columns the CTE chain produces on its own. Anything else has to be
# supplied by the caller, and asking for a scope whose column cannot be produced
# is an error rather than SQL that fails at execution time.
#
# `llo` is the case in point: there is no LLO on a visit row -- the existing
# dashboard carries an org->LLO map in its render code -- so the compiler used to
# emit `props.llo` happily and fail with "column props.llo does not exist". It got
# past validate() because `llo` had been whitelisted in the known-columns set,
# which is the check defeating itself.
INTRINSIC_SCOPE_COLUMNS = frozenset({"opportunity_id", "username", "cohort_month", "case_id"})

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


# ── The expression grammar ───────────────────────────────────────────────────
#
# A measure's `sql` is interpolated RAW into the compiled query, so whatever it
# says, the database runs. Two of the three guardrails were already here -- measure
# types are restricted to Cube's vocabulary, and `validate()` resolves every
# {CUBE}.col against the properties the pipeline actually produces. The third was
# missing: nothing looked at the expression AROUND those references, and validate()
# only inspects references it can FIND, so a fragment containing none passed
# trivially. Measured before this existed:
#
#     {CUBE}.not_a_real_column          -> caught
#     (SELECT count(*) FROM auth_user)  -> PASSED
#     pg_read_file('/etc/passwd')       -> PASSED
#
# That is what kept the registry a file: "reviewed in git" was doing security work.
# With the grammar enforced the trust boundary moves from the file to the check,
# which is what lets an indicator set become editable data like a pipeline schema.
#
# Allowlist, not denylist. A new function is a deliberate addition here, and the
# failure mode of forgetting one is a rejected registry, not an accepted exploit.
ALLOWED_FUNCTIONS: frozenset[str] = frozenset(
    {
        # aggregates the registry's measure types compile into
        "Sum",
        "Count",
        "Avg",
        "Min",
        "Max",
        "ArrayAgg",
        # null handling
        "Nullif",
        "Coalesce",
        # conditionals
        "Case",
        "If",
        # numeric
        "Abs",
        "Round",
        "Floor",
        "Ceil",
        "Least",
        "Greatest",
        # ordering inside ARRAY_AGG(... ORDER BY ...)
        "Order",
        "Ordered",
        # C17 takes the median by indexing a sorted ARRAY_AGG; N06 uses an
        # ordered-set aggregate. Both are in the shipped registry, so the grammar
        # has to admit them or it is describing a registry we do not have.
        "PercentileCont",
        "PercentileDisc",
        # casts are needed for ::numeric and friends
        "Cast",
    }
)

# Node types that are structure rather than a function call: operators, literals,
# columns, boolean logic. Anything not in here and not an allowed function is
# refused.
_ALLOWED_NODES: tuple[str, ...] = (
    "Column",
    "Identifier",
    "Literal",
    "Boolean",
    "Null",
    "Star",
    "Add",
    "Sub",
    "Mul",
    "Div",
    "Mod",
    "Neg",
    "Paren",
    "EQ",
    "NEQ",
    "GT",
    "GTE",
    "LT",
    "LTE",
    "Is",
    "In",
    "Between",
    "And",
    "Or",
    "Not",
    "Distinct",
    "Filter",
    "Where",
    "DataType",
    "Bracket",  # array subscript, e.g. (ARRAY_AGG(x ORDER BY x))[n]
    "WithinGroup",  # percentile_cont(...) WITHIN GROUP (ORDER BY ...)
    "Alias",
    "Anonymous",  # Anonymous is inspected by name below
)

# Never allowed, whatever else is true of the fragment. Named explicitly so the
# error can say WHY rather than "unsupported node".
_FORBIDDEN_NODES: dict[str, str] = {
    "Select": "a subquery",
    "Subquery": "a subquery",
    "From": "a FROM clause",
    "Join": "a join",
    "Union": "a set operation",
    "Command": "a statement",
    "Semicolon": "a statement separator",
}


# Characters that change where Postgres thinks a token ENDS without changing where
# sqlglot thinks it does. The grammar check parses a fragment on its own, but the
# compiler splices the RAW text into a much larger statement -- so anything the two
# lexers disagree about is a way to smuggle SQL past the check. sqlglot drops
# comments silently (`a /* c */ + 1` parses as `a + 1`), so a fragment that opens a
# comment can swallow the text between it and a later fragment's `*/`, and that
# later fragment can hide a subquery inside what the check saw as a string literal.
# Backslashes (E'' escapes) and `$` (dollar quoting, positional parameters) are the
# other two ways the lexers can drift. None of them is needed to express a registry.
_LEXICAL_FORBIDDEN: tuple[tuple[str, str], ...] = (
    (";", "a statement separator"),
    ("--", "a comment"),
    ("/*", "a comment"),
    ("*/", "a comment"),
    ("\\", "a backslash"),
    ("$", "a dollar sign"),
)

# Casts are how the registry moves between dates, timestamps and numbers. The type
# is on a list too: `x::regclass` / `::regproc` are catalogue lookups, not arithmetic.
_ALLOWED_CAST_TYPES = frozenset(
    {
        "INT",
        "BIGINT",
        "SMALLINT",
        "DECIMAL",
        "DOUBLE",
        "FLOAT",
        "DATE",
        "TIMESTAMP",
        "TIMESTAMPTZ",
        "TEXT",
        "VARCHAR",
        "BOOLEAN",
    }
)

# Bare words Postgres evaluates as session functions rather than column names. A
# column reference cannot reach another table, but `user` would still read the
# database role into a dashboard, so these are refused wherever a column may appear.
_SESSION_WORDS = frozenset(
    {
        "user",
        "current_user",
        "session_user",
        "current_role",
        "current_catalog",
        "current_schema",
        "system_user",
    }
)

# A date part is a bare word too (`EXTRACT(EPOCH FROM ...)`, `DATE_TRUNC('month', ...)`,
# `INTERVAL '1 day'`); sqlglot calls it a Var. It is only legal as that argument.
_DATE_PART_PARENTS = frozenset({"Extract", "TimestampTrunc", "DateTrunc", "Interval"})
_DATE_PARTS = frozenset(
    {
        "EPOCH",
        "YEAR",
        "QUARTER",
        "MONTH",
        "WEEK",
        "DAY",
        "DOW",
        "ISODOW",
        "DOY",
        "HOUR",
        "MINUTE",
        "SECOND",
        "DAYS",
        "MONTHS",
        "WEEKS",
        "YEARS",
    }
)

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _lexical_problems(text: str, label: str) -> list[str]:
    return [f"{label}: sql may not contain {why} ({token!r})" for token, why in _LEXICAL_FORBIDDEN if token in text]


def _parse_fragment(text: str, label: str, original: str):
    """(tree, problems). A fragment that does not parse is a problem, not a raise."""
    import sqlglot

    try:
        tree = sqlglot.parse_one(text, read="postgres")
    except Exception as parse_error:
        return None, [f"{label}: sql does not parse ({type(parse_error).__name__}): {original!r}"]
    if tree is None:
        return None, [f"{label}: sql is empty"]
    return tree, []


def _grammar_problems(
    tree,
    label: str,
    *,
    functions: frozenset[str],
    nodes: frozenset[str],
    anonymous_ok: bool,
    columns: frozenset[str] | None = None,
    qualifiers: frozenset[str] = frozenset(),
) -> list[str]:
    """Walk a parsed fragment and refuse anything outside `functions` / `nodes`.

    `columns`, when given, is the complete set of names a bare column may resolve
    to at the point the fragment runs. When it is None the fragment reads Layer-1
    columns whose names depend on the pipeline, so only their SHAPE is checked.
    """
    from sqlglot import exp

    problems: list[str] = []
    for node in tree.walk():
        kind = type(node).__name__
        if kind in _FORBIDDEN_NODES:
            problems.append(f"{label}: sql may not contain {_FORBIDDEN_NODES[kind]}")
            continue
        if kind not in functions and kind not in nodes:
            if kind == "Anonymous":
                problems.append(f"{label}: sql calls {node.this!s}(), which is not an allowed function")
            elif isinstance(node, exp.Func):
                problems.append(f"{label}: sql calls {kind}, which is not an allowed function")
            else:
                problems.append(f"{label}: sql uses {kind}, which the expression grammar does not allow")
            continue
        if kind == "Anonymous":
            # An Anonymous node is a function sqlglot has no class for -- i.e. one
            # nobody put on the list. That is exactly the case to refuse.
            name = str(getattr(node, "this", "") or "")
            if not anonymous_ok or (name and name.capitalize() not in functions):
                problems.append(f"{label}: sql calls {name}(), which is not an allowed function")
        elif kind == "DataType":
            type_name = getattr(node.this, "name", str(node.this))
            if type_name not in _ALLOWED_CAST_TYPES:
                problems.append(f"{label}: sql casts to {node.sql('postgres')}, which is not an allowed type")
        elif kind == "Var":
            parent = type(node.parent).__name__ if node.parent is not None else ""
            if parent not in _DATE_PART_PARENTS or str(node.this).upper() not in _DATE_PARTS:
                problems.append(f"{label}: sql uses the bare word {node.this!s}, which is not a column or date part")
        elif kind == "Column":
            name = node.name
            if node.args.get("db") or node.args.get("catalog") or (node.table and node.table not in qualifiers):
                problems.append(f"{label}: sql may not qualify a column ({node.sql('postgres')})")
                continue
            if not _IDENTIFIER.match(name or "") or name.lower() in _SESSION_WORDS:
                problems.append(f"{label}: {name!r} is not a column name this registry may reference")
            elif columns is not None and name not in columns:
                problems.append(f"{label}: unknown column {name}")
    return problems


def _check_expression(fragment: str, measure_name: str) -> list[str]:
    """Refuse anything outside the grammar. Returns problems, never raises."""
    lexical = _lexical_problems(fragment, measure_name)
    if lexical:
        return lexical
    # `{other_measure}` references are resolved by compile_measures later; here they
    # only need to parse, so stand each one up as a plain identifier. Without this
    # sqlglot reads `{c01_numerator}` as a brace struct literal and every real
    # measure in the registry fails its own grammar.
    text = _MEASURE_REF.sub(lambda m: m.group(1), _cube_to_props(fragment))
    tree, problems = _parse_fragment(text, measure_name, fragment)
    if tree is None:
        return problems
    return _grammar_problems(
        tree,
        measure_name,
        functions=ALLOWED_FUNCTIONS,
        nodes=frozenset(_ALLOWED_NODES),
        anonymous_ok=True,
        qualifiers=frozenset({"props"}),  # what {CUBE} becomes
    )


# ── The Layer 2 grammar ──────────────────────────────────────────────────────
#
# The measure grammar above guarded only the INDICATORS document. Everything in the
# PROPERTIES document -- properties, aggregates and every part of the weight series
# -- was interpolated into the compiled statement with nothing looking at it, so
# the hole the measure grammar closed was still open one document over. Measured
# before this existed, each of these passed validate_registry:
#
#     properties[].sql:   (SELECT count(*) FROM auth_user)
#     aggregates[].sql:   MIN(pg_read_file('/etc/passwd'))
#     weight_series.derived[].sql / valid / day_collapse: any subquery
#     constants:          {"WMIN": "0 AND (SELECT ...) IS NOT NULL"}
#
# Layer 2 legitimately needs more than a measure does -- date arithmetic, EXTRACT,
# BOOL_AND/BOOL_OR, regex matches on form names -- and this list is exactly what the
# shipped and live KMC registries use, plus the date helpers the notation implies.
# Deny by default: a function that is not named here is refused, including every
# Anonymous one, so pg_read_file / dblink / pg_sleep / lo_* / set_config /
# current_setting cannot be reached however they are spelled.
LAYER2_FUNCTIONS: frozenset[str] = ALLOWED_FUNCTIONS | frozenset(
    {
        "LogicalAnd",  # BOOL_AND
        "LogicalOr",  # BOOL_OR
        "Extract",  # EXTRACT(EPOCH FROM ...)
        "TimestampTrunc",  # DATE_TRUNC('month', ...)
        "DateTrunc",
        "CurrentDate",  # the default :as_of
        "RegexpLike",  # form_name ~ '...'
        "RegexpILike",  # form_name ~* '...'
    }
)
_LAYER2_NODES: frozenset[str] = (frozenset(_ALLOWED_NODES) - {"Alias", "Anonymous"}) | {"Var", "Interval"}

# Aggregates cannot run where a fragment is evaluated once per ROW -- a Layer-1
# visit column, or the cohort date in base_m. The grammar would admit them (they
# are safe), and the statement would then fail at execution with a grouping error;
# refusing them here keeps that a validation message instead.
_AGGREGATE_FUNCTIONS = frozenset(
    {"Sum", "Count", "Avg", "Min", "Max", "ArrayAgg", "LogicalAnd", "LogicalOr", "PercentileCont", "PercentileDisc"}
)
ROW_FUNCTIONS: frozenset[str] = LAYER2_FUNCTIONS - _AGGREGATE_FUNCTIONS

# A `word_match` visit column compiles to `(x.<column> ~* '\y<word>\y')`. The word
# is structured data rather than SQL because the lexical guard refuses every
# backslash in a fragment (see _LEXICAL_FORBIDDEN) -- so the engine writes the `\y`
# itself, around a word that can hold nothing a regex or a string literal could
# misread.
_WORD = re.compile(r"[A-Za-z0-9_]+\Z")


# Columns each series fragment can see. These CTEs are the compiler's own, so the
# set is exact; see _build_ctes. The internal names (day, w, prev_w, ...) are a
# contract with the registries that exist, and the entity key and value column are
# the registry's own.
def _day_collapse_columns(model: RegistryModel) -> frozenset[str]:  # weight_readings
    return frozenset({model.row_id, "day", model.value_column or "", "is_seed"}) - {""}


def _derived_columns(model: RegistryModel) -> frozenset[str]:  # weight_seq
    return frozenset({model.row_id, "day", "w", "is_seed", "prev_w", "prev_day", "series_day", "age_days"})


# What base_m adds beyond the aggregates and series derivations (visit_agg's keys).
def _base_columns(model: RegistryModel) -> frozenset[str]:
    return frozenset({model.row_id, "opportunity_id", "username", "case_id", "cohort_month"})


# What visit_agg carries, and so what the cohort date may read.
def _visit_agg_columns(model: RegistryModel, aggregates: list[dict[str, Any]]) -> frozenset[str]:
    return frozenset({model.row_id, "opportunity_id", "username"} | {a["name"] for a in aggregates})


def _check_layer2_fragment(
    fragment: Any,
    label: str,
    constants: dict[str, Any],
    columns: frozenset[str] | None = None,
    functions: frozenset[str] = LAYER2_FUNCTIONS,
) -> list[str]:
    """One properties-document fragment, checked AS THE COMPILER WILL EMIT IT.

    Constants are substituted first, so a constant that carries SQL is parsed as
    SQL -- the check reads the text the database would, not the text the author wrote.
    """
    if not isinstance(fragment, str) or not fragment.strip():
        return [f"{label}: sql must be a non-empty string"]
    lexical = _lexical_problems(fragment, label)
    if lexical:
        return lexical
    try:
        text = _subst_constants(fragment, constants)
    except RegistryError as exc:
        return [f"{label}: {exc}"]
    lexical = _lexical_problems(text, label)
    if lexical:
        return lexical
    tree, problems = _parse_fragment(text, label, fragment)
    if tree is None:
        return problems
    return _grammar_problems(
        tree, label, functions=functions, nodes=_LAYER2_NODES, anonymous_ok=False, columns=columns
    )


def fragment_columns(fragment: str) -> set[str]:
    """The bare column names a (grammar-checked) fragment reads."""
    import sqlglot
    from sqlglot import exp

    tree = sqlglot.parse_one(fragment, read="postgres")
    return {c.name for c in tree.find_all(exp.Column) if not c.table}


def qualify_columns(fragment: str, alias: str, columns: set[str] | frozenset[str]) -> str:
    """`reg_date` -> `v.reg_date` for each named column, outside string literals.

    Used on fragments that have already passed the grammar, so the only quoting is
    single-quoted literals. A name after `::` is a type, and a name before `(` is a
    function, so neither is touched.
    """
    if not columns:
        return fragment
    names = "|".join(sorted((re.escape(c) for c in columns), key=len, reverse=True))
    pattern = re.compile(rf"(?<![\w.:])({names})\b(?!\s*\()")
    parts = re.split(r"('(?:[^']|'')*')", fragment)
    return "".join(part if part.startswith("'") else pattern.sub(rf"{alias}.\1", part) for part in parts)


def _identifier_problem(value: Any, label: str) -> list[str]:
    if not isinstance(value, str) or not _IDENTIFIER.match(value) or value.lower() in _SESSION_WORDS:
        return [f"{label}: {value!r} must be a column name (letters, digits and _ only)"]
    return []


def _model_problems(props_doc: dict[str, Any], constants: dict[str, Any]) -> list[str]:
    """The model sections: entity, visit_columns, pipelines, weight_series.value_column.

    For a document that predates the model the legacy values are checked too, so a
    shim value can never be the one thing that skips the grammar.
    """
    old = legacy.predates_model(props_doc)
    problems: list[str] = []

    entity = legacy.ENTITY if old else props_doc.get("entity")
    if not isinstance(entity, dict):
        return ["entity: must be a mapping with name, plural, key and (optionally) cohort_date"]
    problems += _identifier_problem(entity.get("name"), "entity.name")
    problems += _identifier_problem(entity.get("key"), "entity.key")
    plural = entity.get("plural")
    if plural is not None and (
        not isinstance(plural, str) or not plural.strip() or len(plural) > 64 or not plural.isprintable()
    ):
        problems.append("entity.plural: must be a short printable noun")
    if problems:
        return problems  # every check below is built from the name and key

    model = resolve_model(props_doc)
    aggregates = [a for a in (props_doc.get("aggregates") or []) if isinstance(a, dict) and a.get("name")]
    cohort_cols = _visit_agg_columns(model, aggregates)
    cohort = _check_layer2_fragment(
        model.cohort_date, "entity.cohort_date", constants, columns=cohort_cols, functions=ROW_FUNCTIONS
    )
    if cohort and "cohort_date" not in entity:
        cohort.append(
            "entity.cohort_date: not declared, so it defaults to `first_visit` -- declare an aggregate named "
            "first_visit, or set entity.cohort_date"
        )
    problems += cohort

    columns = (
        legacy.VISIT_COLUMNS if old and props_doc.get("visit_columns") is None else props_doc.get("visit_columns")
    )
    if columns is not None:
        if not isinstance(columns, list):
            problems.append("visit_columns: must be a list")
            columns = []
        seen: set[str] = set()
        for i, col in enumerate(columns):
            name = col.get("name") if isinstance(col, dict) else None
            label = f"visit_columns.{name if isinstance(name, str) else i}"
            if not isinstance(name, str) or not _IDENTIFIER.match(name):
                problems.append(f"{label}: {name!r} is not a valid name (letters, digits and _ only)")
                continue
            if name in seen:
                problems.append(f"{label}: declared twice")
            seen.add(name)
            kinds = [k for k in ("word_match", "sql", "column") if k in col]
            if len(kinds) != 1:
                problems.append(f"{label}: needs exactly one of word_match, sql or column")
                continue
            if kinds == ["word_match"]:
                wm = col["word_match"]
                if not isinstance(wm, dict):
                    problems.append(f"{label}.word_match: must be a mapping of column and word")
                    continue
                problems += _identifier_problem(wm.get("column"), f"{label}.word_match.column")
                word = wm.get("word")
                if not isinstance(word, str) or not _WORD.match(word):
                    problems.append(f"{label}.word_match.word: {word!r} must match ^[A-Za-z0-9_]+$")
            elif kinds == ["column"]:
                problems += _identifier_problem(col["column"], f"{label}.column")
            else:
                # Layer-1 columns are named by the pipeline, so only the shape can be
                # checked; and there are no constants at Layer 1.
                problems += _check_layer2_fragment(col["sql"], f"{label}.sql", {}, functions=ROW_FUNCTIONS)

    pipelines = legacy.PIPELINES if old and props_doc.get("pipelines") is None else props_doc.get("pipelines")
    if pipelines is not None:
        if not isinstance(pipelines, dict):
            problems.append("pipelines: must be a mapping of entity and extra_fields")
        else:
            if pipelines.get("entity") is not None:
                problems += _identifier_problem(pipelines["entity"], "pipelines.entity")
            extra = pipelines.get("extra_fields") or {}
            if not isinstance(extra, dict):
                problems.append("pipelines.extra_fields: must be a mapping of column -> pipeline alias")
            else:
                for col, alias in extra.items():
                    problems += _identifier_problem(col, "pipelines.extra_fields")
                    problems += _identifier_problem(alias, f"pipelines.extra_fields.{col}")

    ws = props_doc.get("weight_series")
    if ws is not None and not isinstance(ws, dict):
        problems.append("weight_series: must be a mapping")
    elif ws:
        if model.value_column is None:
            problems.append("weight_series.value_column: required -- the Layer-1 column the series reads")
        else:
            problems += _identifier_problem(model.value_column, "weight_series.value_column")
    return problems


def model_problems(props_doc: dict[str, Any]) -> list[str]:
    """The model sections alone, with the document's constants -- for Layer 1."""
    raw = props_doc.get("constants") or {}
    constants = dict(raw) if isinstance(raw, dict) else {}
    constants["as_of"] = "CURRENT_DATE"
    return _model_problems(props_doc, constants)


def _constant_problems(constants: Any) -> list[str]:
    """A constant is spliced in as text, so it may only be a number or a boolean."""
    import math

    if not isinstance(constants, dict):
        return ["constants: must be a mapping of NAME -> number"]
    problems = []
    for key, value in constants.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
            problems.append(f"constants: {key!r} is not a valid constant name")
        if isinstance(value, bool):
            continue
        if isinstance(value, int) or (isinstance(value, float) and math.isfinite(value)):
            continue
        problems.append(f"constants.{key}: must be a number, got {value!r}")
    return problems


def _name_problems(kind: str, items: Any) -> list[str]:
    """Every name becomes `AS <name>` in the compiled SQL -- an identifier, nothing more."""
    if not isinstance(items, list):
        return [f"{kind}: must be a list"]
    problems = []
    for item in items:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not _IDENTIFIER.match(name):
            problems.append(f"{kind}: {name!r} is not a valid name (letters, digits and _ only)")
    return problems


def validate_properties_doc(props_doc: dict[str, Any], llo_map: dict[Any, str] | None = None) -> list[str]:
    """Every SQL fragment in the properties document, against the Layer 2 grammar."""
    problems: list[str] = []
    raw_constants = props_doc.get("constants") or {}
    problems.extend(_constant_problems(raw_constants))
    constants = dict(raw_constants) if isinstance(raw_constants, dict) else {}
    # Checked against the default the compiler uses; callers only ever pass
    # CURRENT_DATE or an ISO-validated DATE '...' literal (workflow views, snapshot
    # builders), never registry-controlled text.
    constants["as_of"] = "CURRENT_DATE"

    properties = props_doc.get("properties") or []
    aggregates = props_doc.get("aggregates") or []
    ws = props_doc.get("weight_series") or {}
    derived = ws.get("derived") or [] if isinstance(ws, dict) else []
    name_problems = [
        p
        for kind, items in (
            ("properties", properties),
            ("aggregates", aggregates),
            ("weight_series.derived", derived),
        )
        for p in _name_problems(kind, items)
    ]
    if name_problems:
        return problems + name_problems  # every label below is built from a name

    model_problems = _model_problems(props_doc, constants)
    if model_problems:
        return problems + model_problems  # the column sets below come from the model
    model = resolve_model(props_doc)

    # Aggregates and the `valid` predicate read Layer-1 columns, named by the
    # pipeline rather than the registry -- only their shape can be checked here.
    for a in aggregates:
        problems.extend(_check_layer2_fragment(a.get("sql"), f"aggregates.{a['name']}", constants))
    if ws:
        problems.extend(_check_layer2_fragment(ws.get("valid"), "weight_series.valid", constants))
        problems.extend(
            _check_layer2_fragment(
                ws.get("day_collapse"),
                "weight_series.day_collapse",
                constants,
                columns=_day_collapse_columns(model),
            )
        )
        for d in derived:
            problems.extend(
                _check_layer2_fragment(
                    d.get("sql"), f"weight_series.derived.{d['name']}", constants, columns=_derived_columns(model)
                )
            )

        seed = ws.get("seed_reading")
        if seed:
            if not isinstance(seed, dict):
                problems.append("weight_series.seed_reading: must be a mapping")
            else:
                for key in ("day", "value"):
                    if not isinstance(seed.get(key), str) or not _IDENTIFIER.match(seed[key]):
                        problems.append(f"weight_series.seed_reading.{key}: must be a single column name")
                if seed.get("exclude"):
                    problems.extend(
                        _check_layer2_fragment(seed["exclude"], "weight_series.seed_reading.exclude", constants)
                    )

    # A property runs over base_m and the property levels before it: the entity's
    # keys, every aggregate, every series derivation, and the other properties
    # (ordering and cycles are _property_levels' job).
    visible = (
        set(_base_columns(model))
        | {a["name"] for a in aggregates}
        | {d["name"] for d in derived}
        | {p["name"] for p in properties}
    )
    if llo_map:
        visible.add("llo")
    for p in properties:
        problems.extend(
            _check_layer2_fragment(p.get("sql"), f"properties.{p['name']}", constants, columns=frozenset(visible))
        )
    return problems


def _sql_string_literal(value: Any) -> str:
    """A deployment fact (an LLO name, a settings key) as a quoted SQL literal.

    Doubling the quote is the whole escape under standard_conforming_strings, which
    is Postgres's default. A backslash or a control character is the one thing that
    could make the escape depend on a server setting, and no LLO is named with one,
    so refuse rather than reason about it.
    """
    text = str(value)
    if "\\" in text or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise RegistryError(f"{text!r} contains a backslash or control character and cannot be quoted safely")
    return "'" + text.replace("'", "''") + "'"


def deployment_literal_problems(
    llo_map: dict[Any, str] | None,
    settings: dict[str, dict[Any, bool]] | None,
) -> list[str]:
    """Deployment values the compiler quotes into SQL, checked before it has to."""
    problems: list[str] = []
    for opp, name in (llo_map or {}).items():
        try:
            _sql_string_literal(name)
        except RegistryError as exc:
            problems.append(f"deployment.llo_map.{opp}: {exc}")
    for setting, table in (settings or {}).items():
        if not isinstance(table, dict):
            problems.append(f"deployment.settings.{setting}: must be a mapping of LLO -> true/false")
            continue
        for key in table:
            try:
                _sql_string_literal(key)
            except RegistryError as exc:
                problems.append(f"deployment.settings.{setting}: {exc}")
    return problems


def validate(
    props_doc: dict[str, Any],
    registry: dict[str, Any],
    llo_map: dict[Any, str] | None = None,
) -> list[str]:
    """Every {CUBE}.col must resolve to a real property or aggregate, and every SQL
    fragment in BOTH documents must sit inside its grammar.

    This is the check a Cube runtime would do for us and will not, because we do
    not run one. Without it a typo silently produces a NULL column. It runs on the
    write path (validation.validate_registry) AND on every compile (_build_ctes),
    so a record saved before a rule existed is refused when it is next run rather
    than executed.
    """
    ws = props_doc.get("weight_series") or {}
    known = {p["name"] for p in props_doc["properties"]}
    known |= {a["name"] for a in props_doc.get("aggregates") or []}
    known |= {d["name"] for d in (ws.get("derived") or [] if isinstance(ws, dict) else [])}
    known.add(resolve_model(props_doc).row_id)
    known |= set(INTRINSIC_SCOPE_COLUMNS)
    if llo_map:
        known.add("llo")

    problems: list[str] = []
    # Names and the properties document first: both are spliced into the compiled
    # statement as raw text, and neither was looked at before.
    problems.extend(_name_problems("measures", registry["measures"]))
    problems.extend(validate_properties_doc(props_doc, llo_map))
    problems.extend(indicator_model_problems(registry))
    for rule in registry.get("suppression") or []:
        scope_col = rule.get("scope", "llo") if isinstance(rule, dict) else None
        if scope_col not in INTRINSIC_SCOPE_COLUMNS | {"llo"}:
            problems.append(
                f"suppression: scope {scope_col!r} is not a scope column; expected one of "
                f"{sorted(INTRINSIC_SCOPE_COLUMNS | {'llo'})}"
            )

    for m in registry["measures"]:
        frags = [m.get("sql") or ""] + [f["sql"] for f in (m.get("filters") or [])]
        for frag in frags:
            for col in _CUBE_REF.findall(frag):
                if col not in known:
                    problems.append(f"{m['name']}: unknown column {{CUBE}}.{col}")
            # The column check above only inspects references it can FIND, so a
            # fragment with no {CUBE} refs at all used to pass untouched -- which is
            # how `(SELECT count(*) FROM auth_user)` validated cleanly. The grammar
            # check reads the whole expression.
            if frag.strip():
                problems.extend(_check_expression(frag, m["name"]))
    return problems


def indicator_model_problems(registry: dict[str, Any]) -> list[str]:
    """The indicators document's model sections: `defaults` and `series`."""
    problems: list[str] = []
    defaults = registry.get("defaults")
    if defaults is not None:
        if not isinstance(defaults, dict):
            problems.append("defaults: must be a mapping (e.g. {min_denominator: 25})")
        else:
            unknown = sorted(set(defaults) - {"min_denominator"})
            if unknown:
                problems.append(f"defaults: unknown key(s) {unknown}; expected min_denominator")
            md = defaults.get("min_denominator")
            if md is not None and (isinstance(md, bool) or not isinstance(md, int) or md < 1):
                problems.append(f"defaults.min_denominator: must be a positive integer, got {md!r}")
    series = registry.get("series")
    if series is not None:
        if not isinstance(series, list) or not all(
            isinstance(x, str) and re.fullmatch(r"[A-Za-z]+", x) for x in series
        ):
            problems.append("series: must be a list of indicator prefixes (letters only), e.g. [C, N]")
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


def _llo_case_sql(llo_map: dict[Any, str]) -> str:
    """opportunity_id -> LLO as a CASE, so `llo` is a real column to GROUP BY."""
    whens = " ".join(
        f"WHEN {int(opp)} THEN {_sql_string_literal(name)}"
        for opp, name in sorted(llo_map.items(), key=lambda kv: int(kv[0]))
    )
    return f"CASE v.opportunity_id {whens} ELSE NULL END"


_SQL_WORDS = frozenset(
    {
        "abs",
        "and",
        "or",
        "not",
        "is",
        "null",
        "true",
        "false",
        "coalesce",
        "between",
        "case",
        "when",
        "then",
        "else",
        "end",
        "day",
    }
)


def _seed_reading_sql(ws: dict[str, Any], C, model: RegistryModel) -> str:
    """The optional per-entity SEED reading a registry may declare on its series.

    KMC's case: the demo compute spec's weight series is the ENROLMENT weight at the
    registration date plus every visit weight, with an enrolment weight within 1 g
    of birth weight dropped as a re-entry. The visits pipeline carries only visit
    weights, so without this every "measured weigh-days" rule (thin) and the
    window anchor run one reading short. Declared as data::

        weight_series:
          value_column: weight_g
          seed_reading:
            day: reg_date                  # a Layer-1 column, MIN() per entity
            value: enrollment_weight_g     # a Layer-1 column, MIN() per entity
            exclude: 'ABS(weight_g - birth_weight_g) < 1'   # optional predicate

    Any other column the predicate names is also MIN()'d per entity, so the rule
    can read registration fields. A registry without `seed_reading` compiles to the
    same SQL it always did: every reading is is_seed = FALSE.
    """
    seed = ws.get("seed_reading")
    if not seed:
        return ""
    rid, key, vcol = model.row_id, model.key, model.value_column
    day, value = seed["day"], seed["value"]
    exclude = seed.get("exclude") or "FALSE"
    refs = set(re.findall(r"\b([a-z_][a-z0-9_]*)\b", exclude.lower()))
    extra = sorted(c for c in refs if c not in _SQL_WORDS and c not in {day, value, vcol})
    extra_cols = "".join(f",\n               MIN({c}) AS {c}" for c in extra)
    return f"""
    UNION ALL
    SELECT {rid}, day, {vcol}, TRUE AS is_seed
    FROM (
        SELECT opportunity_id || '|' || {key} AS {rid},
               MIN({day})::date AS day,
               MIN({value}) AS {vcol}{extra_cols}
        FROM visits
        WHERE {key} IS NOT NULL
        GROUP BY 1
    ) seed
    WHERE day IS NOT NULL
      AND {vcol} IS NOT NULL
      AND {C(ws['valid'])}
      AND NOT COALESCE(({C(exclude)}), FALSE)"""


# The visit-level predicate a caller may push down. Keys are the only columns a
# filter may name -- the two every visit row carries, plus the registry's own entity
# key -- and values are escaped here, never interpolated by the caller.
_FILTER_COLUMNS = {"opportunity_id": int, "username": str}


def visit_filter_sql(visit_filter: dict[str, Any] | None, entity_key: str | None = None) -> str:
    """AND-clauses restricting the visit set BEFORE Layer 2 runs, or ''.

    A per-worker case table needs the case scope for ONE worker. Filtering the
    grouped output would still pay for the whole cohort's extraction (the 28-30 s
    measured on opp 10042); filtering the visits makes the same query take the
    time of one worker's visits. Values are escaped, keys are whitelisted, and an
    unknown key is an error rather than a clause that silently matches nothing.
    """
    if not visit_filter:
        return ""
    allowed = dict(_FILTER_COLUMNS)
    if entity_key and _IDENTIFIER.match(entity_key):
        allowed[entity_key] = str
    parts = []
    for key, value in visit_filter.items():
        if key not in allowed:
            raise RegistryError(f"visit_filter key {key!r} is not filterable; expected one of {sorted(allowed)}")
        if value is None:
            continue
        if allowed[key] is int:
            parts.append(f"{key} = {int(value)}")
        else:
            escaped = str(value).replace("'", "''")
            parts.append(f"{key} = '{escaped}'")
    return "".join(f"\n      AND {p}" for p in parts)


def _series_ctes(ws: dict[str, Any], C, model: RegistryModel) -> str:
    """The per-entity reading series: readings -> one per day -> windowed -> aggregated.

    Emitted only when the registry declares `weight_series`. The internal column
    names (day, w, prev_w, prev_day, series_day, age_days, is_seed) are what every
    registry's `derived` and `day_collapse` fragments are written against.

    Why each piece is the way it is, all learned on KMC's real data:

    * age_days is measured from the entity's FIRST VISIT, not its first reading.
      Anchoring on the first weighing shifted KMC's growth window for every baby
      whose first visit carried no weight, and silently changed C09-C13.
    * prev_* are partitioned by is_seed as well as entity: a measured reading's
      predecessor is the previous MEASURED reading, so a seed reading never forms
      a pair (the spec excludes the enrolment->visit-1 rebound), and a registry
      with no seed reading compiles to exactly what it did before.
    * series_day counts from the first MEASURED (non-seed) reading. Anchoring on
      the seed pulled KMC's velocity window back to the registration date, where
      it held too few visit weighings to score (measured 2026-09-10: PIPN
      incomplete 45 to 54 percent, EHA 39 to 66).
    * A measured reading wins over a seed reading on the same day.
    """
    rid, key, vcol = model.row_id, model.key, model.value_column
    first = f"{model.entity_name}_first"
    seed_union = _seed_reading_sql(ws, C, model)
    wderived = ",\n    ".join(f"{C(d['sql'])} AS {d['name']}" for d in ws["derived"])
    return f"""
weight_readings AS (
    SELECT opportunity_id || '|' || {key} AS {rid},
           visit_date::date AS day,
           {vcol},
           FALSE AS is_seed
    FROM visits
    WHERE {key} IS NOT NULL
      AND {C(ws['valid'])}{seed_union}
),
weight_days AS (
    -- One reading per (entity, day); a measured reading wins over a seed reading.
    SELECT {rid}, day,
           COALESCE({ws['day_collapse']} FILTER (WHERE NOT is_seed),
                    MAX({vcol}) FILTER (WHERE is_seed)) AS w,
           BOOL_AND(is_seed) AS is_seed
    FROM weight_readings
    GROUP BY 1, 2
),
{first} AS (
    SELECT opportunity_id || '|' || {key} AS {rid},
           MIN(visit_date)::date AS first_visit_day
    FROM visits
    WHERE {key} IS NOT NULL
    GROUP BY 1
),
weight_seq AS (
    -- age_days: from the FIRST VISIT. prev_*: the previous reading of the same
    -- kind (seed or measured). series_day: from the first MEASURED reading.
    SELECT wd.{rid}, wd.day, wd.w, wd.is_seed,
           LAG(wd.w) OVER (PARTITION BY wd.{rid}, wd.is_seed ORDER BY wd.day) AS prev_w,
           LAG(wd.day) OVER (PARTITION BY wd.{rid}, wd.is_seed ORDER BY wd.day) AS prev_day,
           (wd.day - MIN(wd.day) FILTER (WHERE NOT wd.is_seed) OVER (PARTITION BY wd.{rid}))::int AS series_day,
           (wd.day - bf.first_visit_day)::int AS age_days
    FROM weight_days wd
    JOIN {first} bf USING ({rid})
),
weight_agg AS (
    SELECT {rid},
    {wderived}
    FROM weight_seq
    GROUP BY {rid}
),"""


def _build_ctes(
    props_doc: dict[str, Any],
    registry: dict[str, Any],
    visit_sql: str,
    as_of: str,
    llo_map: dict[Any, str] | None = None,
    visit_filter: dict[str, Any] | None = None,
) -> tuple[str, dict[str, str]]:
    """The Layer 1 -> Layer 2 CTE chain, plus every measure's compiled expression.

    Shared by both entry points so a multi-scope rollup reuses ONE extraction
    instead of re-running the whole chain per scope.

    The entity is keyed on (opportunity, key), NOT the key alone: 829 case ids in
    the KMC cohort appear in more than one opportunity, and grouping on the id by
    itself merged them -- 7,889 cases instead of 8,718, silently changing every
    denominator.

    The cohort month truncates the registry's `entity.cohort_date`. KMC's is its
    registration date falling back to the first visit, because reg_date is a
    FILTERed MIN that is NULL for a baby whose rows never carried one; truncating
    reg_date alone dropped those cases out of every month.
    """
    problems = validate(props_doc, registry, llo_map=llo_map)
    if problems:
        raise RegistryError("registry does not validate:\n  " + "\n  ".join(problems))

    model = resolve_model(props_doc)
    rid, key = model.row_id, model.key

    consts = dict(props_doc.get("constants") or {})
    consts["as_of"] = as_of

    def C(sql: str) -> str:
        return _subst_constants(sql, consts)

    aggregates = props_doc.get("aggregates") or []
    agg_cols = ",\n    ".join(f"{C(a['sql'])} AS {a['name']}" for a in aggregates)
    ws = props_doc.get("weight_series") or None
    series_ctes = _series_ctes(ws, C, model) if ws else ""
    cohort = qualify_columns(C(model.cohort_date), "v", _visit_agg_columns(model, aggregates))

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
    llo_col = f",\n           {_llo_case_sql(llo_map)} AS llo" if llo_map else ""
    series_select, series_join = (", w.*", f"\n    LEFT JOIN weight_agg w USING ({rid})") if ws else ("", "")

    ctes = f"""WITH visits_all AS (
{visit_sql}
),
visits AS (
    -- AS-OF: nothing after the report date exists. Every maturity gate already
    -- measures against :as_of, but the visit SET still ran to today -- so a run
    -- for a past week counted visits that had not happened yet, and "as of
    -- 6 Sep" quietly meant "eligibility as of 6 Sep, activity as of now".
    -- `< date + 1` keeps the whole of the as-of day, midnight included.
    SELECT * FROM visits_all
    WHERE visit_date < ((({as_of}))::date + 1)::timestamp{visit_filter_sql(visit_filter, key)}
),{series_ctes}
visit_agg AS (
    -- One row per entity, keyed (opportunity, key) -- see _build_ctes.
    SELECT opportunity_id || '|' || {key} AS {rid},
           MIN(opportunity_id) AS opportunity_id,
           MIN(username) AS username,
    {agg_cols}
    FROM visits
    WHERE {key} IS NOT NULL
    GROUP BY opportunity_id, {key}
),
base_m AS (
    SELECT v.*{series_select},
           -- The entity's key under a scope-safe name: `{rid}` is on both sides
           -- of the series join (USING keeps both). The `case` scope groups by it.
           v.{rid} AS case_id,
           -- The registry's entity.cohort_date, truncated to its month.
           DATE_TRUNC(
               'month',
               {cohort}
           )::date AS cohort_month{llo_col}
    FROM visit_agg v{series_join}
),
{prop_cte_sql},
props AS (SELECT * FROM {final_props})"""
    return ctes.strip(), compiled


def _check_scopes(scopes: list[str], llo_map: dict[Any, str] | None) -> None:
    """Refuse a scope whose column cannot be produced, loudly and early.

    LLO grouping is OPTIONAL. `llo` is not on a visit row: it is a CASE over the
    deployment's llo_map, so a registry that declares no map has no llo scopes --
    every other scope compiles, and asking for an llo one is refused by name here
    rather than emitted as SQL that references a column nothing defines.
    """
    unknown = [sc for sc in scopes if sc not in SCOPES]
    if unknown:
        raise RegistryError(f"unknown scope(s) {unknown}; expected from {sorted(SCOPES)}")
    available = available_scope_columns(llo_map)
    for sc in scopes:
        missing = [c for c in SCOPES[sc] if c not in available]
        if missing:
            raise RegistryError(
                f"scope {sc!r} groups by {missing}, which this registry cannot produce: it declares "
                f"no deployment.llo_map, and `llo` is not on a visit row -- it is materialised from "
                f"that map ({{opportunity_id: 'LLO'}}). Declare one to use the llo scopes; every "
                f"other scope works without it."
            )


def available_scope_columns(llo_map: dict[Any, str] | None) -> frozenset[str]:
    return frozenset(INTRINSIC_SCOPE_COLUMNS | ({"llo"} if llo_map else set()))


def available_scopes(llo_map: dict[Any, str] | None) -> list[str]:
    """The scopes a registry with (or without) an llo_map can compile, in SCOPES order."""
    cols = available_scope_columns(llo_map)
    return [sc for sc, needs in SCOPES.items() if set(needs) <= cols]


def _suppression_columns(
    registry: dict[str, Any],
    settings: dict[str, dict[Any, bool]] | None,
    llo_map: dict[Any, str] | None,
) -> str:
    """Emit `<measure>_suppressed` for every declared suppression rule.

    These are NOT bands. The workbook's Targets & settings rows say whether an
    LLO records a thing credibly at all, and an indicator that fails the gate must
    not be published even though it computes cleanly. The registry declared these
    rules from the start and the compiler ignored them, which is the same gap this
    project flagged in the other implementation -- so C14 would have shipped a
    mortality figure for an LLO the workbook says does not record deaths credibly.
    """
    rules = registry.get("suppression") or []
    if not settings or not rules:
        return ""
    by_indicator = {m["meta"]["indicator"]: m["name"] for m in registry["measures"] if m.get("meta")}
    cols: list[str] = []
    for rule in rules:
        ind = rule["indicator"]
        name = by_indicator.get(ind)
        if name is None:
            continue  # declared for an indicator this registry does not compute
        table = settings.get(rule["setting"])
        if table is None:
            continue  # no values supplied for this setting
        scope_col = rule.get("scope", "llo")
        if scope_col == "llo" and not llo_map:
            raise RegistryError(
                f"suppression rule for {ind} is scoped by llo, but no llo_map was "
                f"given. Silently not suppressing is the failure this rule exists to "
                f"prevent, so this is an error rather than a skipped gate."
            )
        credible = [k for k, v in table.items() if v]
        if credible:
            lits = ", ".join(_sql_string_literal(k) for k in credible)
            # BOOL_OR, not a bare predicate. The rule is scoped by llo but the QUERY
            # may be grouped by something else, and `props.llo` is only a legal bare
            # reference where llo is a grouping column -- so `scopes=programme`,
            # `opportunity` and `flw` each came back as a raw Postgres "must appear in
            # the GROUP BY clause" 400 rather than a number. As an aggregate it is
            # valid at every scope, and at the llo scope every row in a group shares
            # one llo, so it reduces to exactly the per-llo predicate it replaces.
            #
            # The reading it gives elsewhere is the conservative one: a pooled figure
            # is flagged when ANY contributing LLO does not record the thing credibly.
            # Pooling a non-credible LLO into a programme number does not launder it.
            cols.append(
                f"BOOL_OR(props.{scope_col} IS NULL OR props.{scope_col} NOT IN ({lits})) " f"AS {name}_suppressed"
            )
        else:
            cols.append(f"TRUE AS {name}_suppressed")
    return ("".join(f"{c},\n    " for c in cols)) if cols else ""


def _measure_cols(registry: dict[str, Any], compiled: dict[str, str]) -> str:
    return ",\n    ".join(f"{compiled[m['name']]} AS {m['name']}" for m in registry["measures"])


def compile_indicator_sql(
    props_doc: dict[str, Any],
    registry: dict[str, Any],
    visit_sql: str,
    scope: str = "programme",
    as_of: str = "CURRENT_DATE",
    llo_map: dict[Any, str] | None = None,
    settings: dict[str, dict[Any, bool]] | None = None,
    visit_filter: dict[str, Any] | None = None,
) -> str:
    """One statement for ONE scope. Prefer compile_rollup_sql for several."""
    _check_scopes([scope], llo_map)
    ctes, compiled = _build_ctes(props_doc, registry, visit_sql, as_of, llo_map=llo_map, visit_filter=visit_filter)
    supp = _suppression_columns(registry, settings, llo_map)
    scope_cols = SCOPES[scope]
    scope_select = "".join(f"props.{c},\n    " for c in scope_cols)
    group_by = ("GROUP BY " + ", ".join(f"props.{c}" for c in scope_cols)) if scope_cols else ""
    return f"""{ctes}
SELECT
    {scope_select}COUNT(*) AS n_cases,
    {supp}{_measure_cols(registry, compiled)}
FROM props
{group_by}
""".strip()


def compile_rollup_sql(
    props_doc: dict[str, Any],
    registry: dict[str, Any],
    visit_sql: str,
    scopes: list[str] | None = None,
    as_of: str = "CURRENT_DATE",
    llo_map: dict[Any, str] | None = None,
    settings: dict[str, dict[Any, bool]] | None = None,
    visit_filter: dict[str, Any] | None = None,
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
    _check_scopes(scopes, llo_map)

    ctes, compiled = _build_ctes(props_doc, registry, visit_sql, as_of, llo_map=llo_map, visit_filter=visit_filter)
    supp = _suppression_columns(registry, settings, llo_map)

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
    {supp}{_measure_cols(registry, compiled)}
FROM props
GROUP BY GROUPING SETS ({sets})
""".strip()
