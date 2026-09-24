"""Every SQL fragment a registry carries is checked before it can be compiled.

The indicators document already had an expression grammar; the PROPERTIES document
did not, and its fragments -- properties, aggregates, the weight series -- were
interpolated into the compiled statement raw. A registry is edited live through the
MCP tools and compiled on the next dashboard load, so anyone who could edit one
could run arbitrary SQL as the application's database user. Each vector below
passed ``validate_registry`` before this file existed.
"""

from __future__ import annotations

import copy

import pytest

from connect_labs.semantic.compiler import RegistryError, compile_indicator_sql, validate
from connect_labs.semantic.seed import registry_payload
from connect_labs.semantic.validation import validate_registry

VISIT_SQL = "SELECT * FROM fixture_visits"


@pytest.fixture(scope="module")
def shipped():
    return registry_payload("kmc")


def _errors(payload, *, props=None, inds=None, deployment=None):
    return validate_registry(
        props if props is not None else payload["properties"],
        inds if inds is not None else payload["indicators"],
        deployment if deployment is not None else payload["deployment"],
    )


def _with_property(payload, sql, name="probe_prop"):
    props = copy.deepcopy(payload["properties"])
    props["properties"].append({"name": name, "sql": sql})
    return props


def _with_aggregate(payload, sql, name="probe_agg"):
    props = copy.deepcopy(payload["properties"])
    props["aggregates"].append({"name": name, "sql": sql})
    return props


def test_the_shipped_registry_passes(shipped):
    """The grammar has to describe the registry we have, or it gets switched off."""
    assert _errors(shipped) == []


# ── properties / aggregates ──────────────────────────────────────────────────


def test_a_subquery_in_a_property_is_refused(shipped):
    errors = _errors(shipped, props=_with_property(shipped, "(SELECT count(*) FROM auth_user)"))
    assert any("probe_prop" in e and "subquery" in e for e in errors), errors


def test_pg_read_file_in_an_aggregate_is_refused(shipped):
    errors = _errors(shipped, props=_with_aggregate(shipped, "MIN(pg_read_file('/etc/passwd'))"))
    assert any("probe_agg" in e and "pg_read_file" in e for e in errors), errors


@pytest.mark.parametrize(
    "sql",
    [
        "pg_sleep(10)",
        "set_config('role', 'postgres', false)",
        "current_setting('server_version')",
        "dblink('host=x', 'select 1')",
        "lo_import('/etc/passwd')",
        "pg_catalog.pg_read_file('x')",
        "version()",
        "current_user",
        "user",
        "birth_weight_g::regclass",
        "num_visits IN (SELECT id FROM auth_user)",
    ],
)
def test_unlisted_functions_and_catalogue_reads_are_refused(shipped, sql):
    assert _errors(shipped, props=_with_property(shipped, sql)), sql


@pytest.mark.parametrize(
    "sql",
    [
        # A trailing comment would swallow the compiler's own `) AS name`.
        "num_visits -- ",
        # sqlglot drops comments silently, so a comment-opening fragment would pass
        # the parser while swallowing SQL up to a later fragment's `*/`.
        "num_visits /* ",
        "num_visits; DROP TABLE auth_user",
        # E'' strings honour backslash escapes and $$ opens a dollar-quote: both are
        # places the check's lexer and Postgres's could disagree about a string's end.
        "E'\\'' || num_visits",
        "$$x$$",
    ],
)
def test_lexer_desync_tricks_are_refused(shipped, sql):
    assert _errors(shipped, props=_with_property(shipped, sql)), sql


def test_a_property_must_reference_a_real_column(shipped):
    errors = _errors(shipped, props=_with_property(shipped, "not_a_real_column > 0"))
    assert any("unknown column not_a_real_column" in e for e in errors), errors


def test_a_property_may_not_qualify_a_column(shipped):
    errors = _errors(shipped, props=_with_property(shipped, "auth_user.password IS NULL"))
    assert any("qualify" in e for e in errors), errors


def test_a_name_that_is_not_an_identifier_is_refused(shipped):
    """Names are spliced in as `AS <name>`, so a name is an injection surface too."""
    props = _with_property(shipped, "num_visits", name="x FROM auth_user --")
    assert any("not a valid name" in e for e in _errors(shipped, props=props))
    inds = copy.deepcopy(shipped["indicators"])
    inds["measures"][0]["name"] = "c01, (SELECT 1) AS y"
    assert any("not a valid name" in e for e in _errors(shipped, inds=inds))


# ── the weight series ────────────────────────────────────────────────────────


def test_a_subquery_in_a_weight_derivation_is_refused(shipped):
    props = copy.deepcopy(shipped["properties"])
    props["weight_series"]["derived"].append({"name": "probe_der", "sql": "(SELECT MAX(id) FROM auth_user)"})
    errors = _errors(shipped, props=props)
    assert any("derived.probe_der" in e and "subquery" in e for e in errors), errors


def test_a_weight_derivation_reads_only_weight_seq_columns(shipped):
    props = copy.deepcopy(shipped["properties"])
    props["weight_series"]["derived"].append({"name": "probe_der", "sql": "MAX(password)"})
    errors = _errors(shipped, props=props)
    assert any("unknown column password" in e for e in errors), errors


@pytest.mark.parametrize(
    "key,sql",
    [
        ("valid", "weight_g > (SELECT count(*) FROM auth_user)"),
        ("valid", "pg_sleep(5) IS NOT NULL"),
        ("day_collapse", "AVG((SELECT count(*) FROM auth_user))"),
        ("day_collapse", "MAX(pg_read_file('/etc/passwd'))"),
    ],
)
def test_the_weight_series_predicates_are_checked(shipped, key, sql):
    props = copy.deepcopy(shipped["properties"])
    props["weight_series"][key] = sql
    errors = _errors(shipped, props=props)
    assert any(f"weight_series.{key}" in e for e in errors), errors


def test_seed_reading_columns_must_be_plain_names(shipped):
    props = copy.deepcopy(shipped["properties"])
    props["weight_series"]["seed_reading"]["day"] = "reg_date) FROM auth_user --"
    assert any("seed_reading.day" in e for e in _errors(shipped, props=props))
    props = copy.deepcopy(shipped["properties"])
    props["weight_series"]["seed_reading"]["exclude"] = "(SELECT TRUE FROM auth_user LIMIT 1)"
    assert any("seed_reading.exclude" in e for e in _errors(shipped, props=props))


# ── constants and deployment facts ───────────────────────────────────────────


def test_a_constant_carrying_sql_is_refused(shipped):
    """`:WMIN` is substituted as text. A string constant is a string of SQL."""
    props = copy.deepcopy(shipped["properties"])
    props["constants"]["WMIN"] = "0 AND (SELECT count(*) FROM auth_user) > 0"
    errors = _errors(shipped, props=props)
    assert any("constants.WMIN" in e and "number" in e for e in errors), errors
    # ...and the fragment it lands in is refused on its own terms as well.
    assert any("weight_series.valid" in e and "subquery" in e for e in errors), errors


@pytest.mark.parametrize("value", ["28", None, [1], {"a": 1}, float("nan")])
def test_a_constant_must_be_a_number(shipped, value):
    props = copy.deepcopy(shipped["properties"])
    props["constants"]["MATURITY_OUTCOME_DAYS"] = value
    assert any("constants.MATURITY_OUTCOME_DAYS" in e for e in _errors(shipped, props=props))


def test_an_llo_name_is_quoted_as_a_single_literal(shipped):
    """LLO names reach SQL as literals in the `llo` CASE and the suppression lists.
    A quote in a name must stay inside the literal."""
    import sqlglot

    deployment = copy.deepcopy(shipped["deployment"])
    evil = "X' OR 1=1) OR (SELECT TRUE FROM auth_user LIMIT 1) OR ('"
    deployment["llo_map"] = {"10042": evil}
    deployment["settings"] = {k: {evil: True} for k in deployment.get("settings") or {}}
    assert _errors(shipped, deployment=deployment) == []
    sql = compile_indicator_sql(
        shipped["properties"],
        shipped["indicators"],
        VISIT_SQL,
        scope="llo",
        llo_map={10042: evil},
        settings=deployment["settings"],
    )
    tree = sqlglot.parse_one(sql, read="postgres")
    tables = {t.name for t in tree.find_all(sqlglot.exp.Table)}
    assert "auth_user" not in tables
    assert evil in {lit.this for lit in tree.find_all(sqlglot.exp.Literal) if lit.is_string}


@pytest.mark.parametrize("name", ["X\\' OR TRUE --", "line\nbreak"])
def test_an_llo_name_that_quoting_cannot_make_safe_is_refused(shipped, name):
    deployment = copy.deepcopy(shipped["deployment"])
    deployment["llo_map"] = {**deployment["llo_map"], "99999": name}
    assert any("deployment.llo_map.99999" in e for e in _errors(shipped, deployment=deployment))
    deployment = copy.deepcopy(shipped["deployment"])
    setting = next(iter(deployment["settings"]))
    deployment["settings"][setting][name] = True
    assert any(f"deployment.settings.{setting}" in e for e in _errors(shipped, deployment=deployment))


def test_a_suppression_scope_must_be_a_scope_column(shipped):
    inds = copy.deepcopy(shipped["indicators"])
    inds["suppression"][0]["scope"] = "llo IS NULL OR (SELECT TRUE FROM auth_user LIMIT 1)"
    assert any("suppression: scope" in e for e in _errors(shipped, inds=inds))


# ── still usable ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "CASE WHEN num_visits > :MATURITY_OUTCOME_DAYS THEN 'many' ELSE 'few' END",
        "COALESCE(n_weight_days, 0) >= 2 AND NOT died",
        "FLOOR(EXTRACT(EPOCH FROM ((:as_of)::timestamp - first_visit::timestamp)) / 86400)::int",
        "DATE_TRUNC('month', first_visit)::date",
        "(last_visit::date - first_visit::date) > 7",
        "first_visit + INTERVAL '28 days' < CURRENT_DATE",
        "LEAST(num_visits, 10) + GREATEST(0, death_visits)",
        "ROUND(100.0 * death_visits / NULLIF(num_visits, 0))",
    ],
)
def test_legitimate_properties_still_pass(shipped, sql):
    assert _errors(shipped, props=_with_property(shipped, sql)) == [], sql


@pytest.mark.parametrize(
    "sql",
    [
        "COUNT(*) FILTER (WHERE form_name ~* 'regist')",
        "BOOL_OR(danger_sign_yes)",
        "MAX(weight_g) FILTER (WHERE weight_g IS NOT NULL AND MOD(weight_g::numeric, 100) = 0)",
        "(ARRAY_AGG(weight_g ORDER BY visit_date DESC) FILTER (WHERE weight_g IS NOT NULL))[1]",
        "SUM(CASE WHEN referred_yes THEN 1 ELSE 0 END)",
    ],
)
def test_legitimate_aggregates_still_pass(shipped, sql):
    assert _errors(shipped, props=_with_aggregate(shipped, sql)) == [], sql


def test_a_stored_record_that_predates_the_check_is_refused_at_compile(shipped):
    """validate() also runs inside the compiler, so a registry saved before this
    check existed is refused on its next dashboard load rather than executed."""
    props = _with_property(shipped, "(SELECT count(*) FROM auth_user)")
    assert validate(props, shipped["indicators"])
    with pytest.raises(RegistryError, match="subquery"):
        compile_indicator_sql(props, shipped["indicators"], VISIT_SQL)


# ── the model sections ───────────────────────────────────────────────────────
#
# The engine's KMC constants became registry data (entity, visit_columns,
# pipelines, weight_series.value_column, defaults). Every one of them reaches the
# compiled SQL or Layer 1, so each is a new place text could be smuggled in, and
# each is held to the same allow-list as the fragments above. Layer 1 checks them
# again itself, because it runs before the compiler's own validation.


def _with(payload, **sections):
    props = copy.deepcopy(payload["properties"])
    props.update(sections)
    return props


def _with_visit_column(payload, column):
    props = copy.deepcopy(payload["properties"])
    props["visit_columns"] = props["visit_columns"] + [column]
    return props


@pytest.mark.parametrize(
    "sql",
    [
        "(SELECT count(*) FROM auth_user) > 0",
        "pg_read_file('/etc/passwd') IS NOT NULL",
        "ebf_visits IS NOT NULL -- ",
        "ebf_visits IS NOT NULL; DROP TABLE auth_user",
        "current_user IS NOT NULL",
        "auth_user.password IS NULL",
        # Aggregates are safe but meaningless per row, and would fail at execution.
        "COUNT(*) > 0",
        "BOOL_OR(ebf_visits IS NOT NULL)",
    ],
)
def test_a_visit_column_sql_is_held_to_the_grammar(shipped, sql):
    errors = _errors(shipped, props=_with_visit_column(shipped, {"name": "probe_col", "sql": sql}))
    assert any("visit_columns.probe_col" in e for e in errors), (sql, errors)


@pytest.mark.parametrize(
    "word",
    [
        "no') OR (SELECT 1)=1 --",
        "no\\y|x",  # a backslash would let the author write their own regex escapes
        "n'o",
        "yes no",
        "",
        None,
        5,
    ],
)
def test_a_word_match_word_is_letters_digits_and_underscore_only(shipped, word):
    column = {"name": "probe_col", "word_match": {"column": "death_visits", "word": word}}
    errors = _errors(shipped, props=_with_visit_column(shipped, column))
    assert any("probe_col.word_match.word" in e for e in errors), (word, errors)


@pytest.mark.parametrize(
    "column",
    [
        {"name": "probe_col", "word_match": {"column": "death_visits) OR (1=1", "word": "no"}},
        {"name": "probe_col", "column": "form_names FROM auth_user --"},
        {"name": "probe_col", "column": "current_user"},
        {"name": "x FROM auth_user --", "column": "form_names"},
        {"name": "probe_col", "column": "form_names", "sql": "TRUE"},  # ambiguous kind
        {"name": "probe_col"},  # no kind at all
        {"name": "form_name", "column": "form_names"},  # declared twice
    ],
)
def test_visit_column_names_and_sources_are_identifiers(shipped, column):
    assert _errors(shipped, props=_with_visit_column(shipped, column)), column


@pytest.mark.parametrize(
    "entity_patch",
    [
        {"key": "baby_case_id IS NOT NULL OR TRUE --"},
        {"key": "current_user"},
        {"name": "baby; DROP TABLE auth_user"},
        {"cohort_date": "(SELECT MIN(date_joined) FROM auth_user)"},
        {"cohort_date": "pg_read_file('x')::timestamp"},
        {"cohort_date": "MIN(first_visit)"},  # an aggregate, in a per-row position
        {"cohort_date": "not_a_visit_agg_column"},  # only visit_agg's columns exist there
        {"plural": "babies\n"},
    ],
)
def test_the_entity_is_held_to_identifiers_and_the_grammar(shipped, entity_patch):
    entity = {**shipped["properties"]["entity"], **entity_patch}
    assert _errors(shipped, props=_with(shipped, entity=entity)), entity_patch


@pytest.mark.parametrize(
    "pipelines",
    [
        {"entity": "children' OR '1'='1"},
        {"entity": "children", "extra_fields": {"weight_g FROM x": "visits"}},
        {"entity": "children", "extra_fields": {"weight_g": "visits; --"}},
        {"entity": "children", "extra_fields": ["weight_g"]},
        "children",
    ],
)
def test_pipeline_aliases_are_identifiers(shipped, pipelines):
    errors = _errors(shipped, props=_with(shipped, pipelines=pipelines))
    assert any(e.startswith("pipelines") for e in errors), (pipelines, errors)


@pytest.mark.parametrize("value_column", ["weight_g FROM auth_user --", "current_user", 5])
def test_the_series_value_column_is_an_identifier(shipped, value_column):
    props = copy.deepcopy(shipped["properties"])
    props["weight_series"]["value_column"] = value_column
    errors = _errors(shipped, props=props)
    assert any("value_column" in e for e in errors), (value_column, errors)


def test_a_new_shape_series_must_name_its_value_column(shipped):
    """Only a record that PREDATES the model gets `weight_g` from the legacy shim."""
    props = copy.deepcopy(shipped["properties"])
    del props["weight_series"]["value_column"]
    assert any("weight_series.value_column: required" in e for e in _errors(shipped, props=props))


@pytest.mark.parametrize(
    "defaults",
    [{"min_denominator": "25; DROP"}, {"min_denominator": 0}, {"min_denominator": True}, {"floor": 3}, [25]],
)
def test_indicator_defaults_are_checked(shipped, defaults):
    inds = copy.deepcopy(shipped["indicators"])
    inds["defaults"] = defaults
    assert _errors(shipped, inds=inds), defaults


@pytest.mark.parametrize("series", [["C", "N;"], "C", [1]])
def test_a_declared_series_list_is_letters_only(shipped, series):
    inds = copy.deepcopy(shipped["indicators"])
    inds["series"] = series
    assert _errors(shipped, inds=inds), series


def test_the_shim_values_are_checked_too(shipped):
    """A record that predates the model runs the legacy visit columns and cohort
    date; they pass the same grammar, so the shim is never the way round it."""
    props = copy.deepcopy(shipped["properties"])
    for section in ("visit_columns", "pipelines"):
        props.pop(section)
    props["entity"] = "baby"
    del props["weight_series"]["value_column"]
    assert _errors(shipped, props=props) == []


def test_layer1_refuses_an_unsafe_visit_column_before_the_compiler_sees_it(shipped):
    from connect_labs.semantic.layer1 import build_visit_sql

    props = _with_visit_column(shipped, {"name": "probe_col", "sql": "(SELECT 1) = 1"})
    extraction = {
        "visit_extraction_sql": "SELECT\nvisit_id,\nvisit_date\nFROM labs_raw_visit_cache AS labs_raw_visit_cache\n"
        "WHERE opportunity_id = 1 AND pipeline_id = 2"
    }
    with pytest.raises(ValueError, match="subquery"):
        build_visit_sql({}, [1], generate_sql_preview=lambda s, o: extraction, props_doc=props)
