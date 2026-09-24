"""A second, non-KMC registry, end to end: the proof that KMC is one example.

`registry/visit_quality` counts BENEFICIARIES over the columns every Connect visit
carries (entity_id, username, visit_date, status, flagged) -- no weight series, no
LLO map, a Q-series. If anything about babies, weights, `children` pipelines, LLOs
or the C/N series were still assumed by the engine, one of these would fail:
validation, compilation at every scope it can have, EXECUTION against hand-computed
numbers, the English explanation, the series filter, Layer 1, the workflow
binding, and grading against its own declared denominator floor.
"""

from __future__ import annotations

import datetime as dt
import math
from unittest.mock import MagicMock

import pytest

from connect_labs.semantic import snapshot as snap
from connect_labs.semantic.compiler import RegistryError, available_scopes, compile_indicator_sql, compile_rollup_sql
from connect_labs.semantic.explain import english, explain, to_markdown
from connect_labs.semantic.layer1 import build_visit_sql, visit_columns_sql
from connect_labs.semantic.model import resolve_model, series_prefixes
from connect_labs.semantic.runtime import (
    SemanticRuntimeError,
    evaluate,
    filter_to_series,
    load_deployment_facts,
    load_registry,
    measure_catalog,
)
from connect_labs.semantic.seed import registry_payload
from connect_labs.semantic.tests.pg import connect_or_skip
from connect_labs.semantic.validation import validate_registry

AS_OF = "DATE '2026-03-31'"
NON_LLO_SCOPES = ["programme", "opportunity", "flw", "month", "opportunity_month", "flw_month", "case"]


@pytest.fixture(scope="module")
def registry():
    props, inds = load_registry("visit_quality")
    return props, inds


# ── the registry itself ─────────────────────────────────────────────────────


def test_it_validates_with_no_llo_map_and_its_model_is_its_own(registry):
    props, inds = registry
    facts = load_deployment_facts("visit_quality")
    assert facts["llo_map"] == {} and facts["settings"] == {}
    assert validate_registry(props, inds, {}) == []
    model = resolve_model(props, inds)
    assert model.shimmed == (), "a new-shape registry must never read legacy.py"
    assert (model.entity_name, model.entity_plural, model.key, model.row_id) == (
        "beneficiary",
        "beneficiaries",
        "entity_id",
        "beneficiary_id",
    )
    assert model.weight_series is None and model.extra_fields == {} and model.min_denominator == 5


def test_it_seeds_a_record_like_any_built_in(registry):
    payload = registry_payload("visit_quality")
    assert payload["properties"]["entity"]["name"] == "beneficiary"
    assert payload["deployment"]["llo_map"] == {}


def test_it_compiles_at_every_scope_it_can_have_and_refuses_the_llo_ones_by_name(registry):
    props, inds = registry
    assert available_scopes(None) == NON_LLO_SCOPES
    for scope in NON_LLO_SCOPES:
        sql = compile_indicator_sql(props, inds, "SELECT * FROM v", scope=scope, as_of=AS_OF)
        # No series CTEs at all: the registry declares none.
        assert "weight_readings" not in sql and "weight_agg" not in sql
        assert "beneficiary_id" in sql and "baby" not in sql.lower()
    for scope in ("llo", "llo_month"):
        with pytest.raises(RegistryError, match="declares no deployment.llo_map"):
            compile_indicator_sql(props, inds, "SELECT * FROM v", scope=scope, as_of=AS_OF)


def test_its_series_is_its_own(registry):
    _, inds = registry
    assert series_prefixes(inds) == ("Q",)
    q = filter_to_series(inds, "q")
    assert {m["meta"]["indicator"] for m in q["measures"] if m.get("meta")} == {"Q01", "Q02", "Q03", "Q04", "Q05"}
    with pytest.raises(SemanticRuntimeError, match="unknown indicator series 'C'"):
        filter_to_series(inds, "C")


def test_undeclared_series_are_read_off_the_indicator_ids(registry):
    _, inds = registry
    undeclared = {k: v for k, v in inds.items() if k != "series"}
    assert series_prefixes(undeclared) == ("Q",)


# ── Layer 1 and the workflow binding ─────────────────────────────────────────


def test_layer1_adds_its_visit_columns(registry):
    props, _ = registry
    extraction = {
        "visit_extraction_sql": (
            "SELECT\nvisit_id,\nusername,\nentity_id,\nvisit_date,\nstatus,\nflagged\n"
            "FROM labs_raw_visit_cache AS labs_raw_visit_cache\n"
            "WHERE opportunity_id = 101 AND pipeline_id = 9\nORDER BY visit_id"
        )
    }
    sql = build_visit_sql({}, [101, 102], generate_sql_preview=lambda s, o: extraction, props_doc=props)
    assert "(x.status ~* '\\yapproved\\y') AS is_approved" in sql
    assert "(x.status ~* '\\yrejected\\y') AS is_rejected" in sql
    assert "(COALESCE(x.flagged, FALSE)) AS is_flagged" in sql
    for kmc_column in ("child_alive_no", "ebf_recorded", "form_name"):
        assert kmc_column not in sql


def test_the_binding_reads_the_pipeline_the_registry_names(registry):
    from connect_labs.semantic.workflow_binding import SemanticBindingError, build_evaluate_inputs

    props, _ = registry
    access = MagicMock()
    access.get_definition.return_value = MagicMock(schema={"fields": []})
    access._schema_to_config.return_value = "CONFIG"
    definition = MagicMock(pipeline_sources=[{"alias": "visits", "pipeline_id": 77}])
    assert build_evaluate_inputs(definition, lambda: access, props_doc=props) == ("CONFIG", None)
    access.get_definition.assert_called_once_with(77)

    kmc_shaped = MagicMock(pipeline_sources=[{"alias": "children", "pipeline_id": 1}])
    with pytest.raises(SemanticBindingError, match="alias 'visits'"):
        build_evaluate_inputs(kmc_shaped, lambda: access, props_doc=props)


# ── execution ────────────────────────────────────────────────────────────────

psycopg2 = pytest.importorskip("psycopg2")

# (opportunity, worker, entity, date, status, flagged)
VISITS = [
    (101, "u1", "e1", "2026-01-05", "approved", False),
    (101, "u1", "e1", "2026-01-20", "approved", True),
    (101, "u1", "e2", "2026-01-10", "approved", False),
    (101, "u1", "e2", "2026-04-10", "approved", False),  # after as-of: does not exist
    (101, "u2", "e3", "2026-02-01", "approved", False),
    (101, "u2", "e3", "2026-03-15", "rejected", False),
    (101, "u2", "e3", "2026-03-20", "approved", None),
    (102, "u1", "e1", "2026-02-10", "pending", False),  # same entity id, other opp: another beneficiary
    (102, "u1", "e1", "2026-03-25", "approved", True),
    (102, "u3", "e4", "2026-03-01", "over_limit", False),
    (102, "u3", None, "2026-03-02", "approved", True),  # no entity: counted nowhere
]

# Hand-computed from VISITS, as of 2026-03-31:
#   beneficiary  visits  flagged  all approved  last visit (days before as-of)
#   101|e1       2       yes      yes           01-20 (70)
#   101|e2       1       no       yes           01-10 (80)
#   101|e3       3       no       no (rejected) 03-20 (11)  <- seen recently
#   102|e1       2       yes      no (pending)  03-25 (6)   <- seen recently
#   102|e4       1       no       no (over_limit) 03-01 (30) <- seen recently (<= 30)
EXPECTED = {
    ("programme", None, None): {"n_cases": 5, "q01": 5, "q02": 60.0, "q03": 40.0, "q04": 40.0, "q05": 2.0},
    ("opportunity", 101, None): {"n_cases": 3, "q02": 200 / 3, "q03": 100 / 3, "q04": 200 / 3, "q05": 3.0},
    ("opportunity", 102, None): {"n_cases": 2, "q02": 50.0, "q03": 50.0, "q04": 0.0, "q05": 1.5},
    ("flw", 101, "u1"): {"n_cases": 2, "q02": 50.0, "q04": 100.0, "q05": None},
    ("flw", 101, "u2"): {"n_cases": 1, "q02": 100.0, "q04": 0.0, "q05": 3.0},
    ("flw", 102, "u1"): {"n_cases": 1, "q03": 100.0, "q05": 2.0},
    ("flw", 102, "u3"): {"n_cases": 1, "q02": 0.0, "q05": 1.0},
}
EXPECTED_MONTHS = {"2026-01-01": 2, "2026-02-01": 2, "2026-03-01": 1}


@pytest.fixture(scope="module")
def conn():
    c = connect_or_skip("the visit_quality execution test")
    yield c
    c.close()


@pytest.fixture(scope="module")
def visit_sql(conn, registry):
    """The fixture as a TEMP table, with Layer 1's own visit columns on top of it."""
    props, _ = registry
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS vq_visits")
    cur.execute(
        "CREATE TEMP TABLE vq_visits (opportunity_id int, username text, entity_id text,"
        " visit_date timestamp, status text, flagged boolean) ON COMMIT PRESERVE ROWS"
    )
    cur.executemany(
        "INSERT INTO vq_visits VALUES (%s, %s, %s, %s, %s, %s)",
        [(o, u, e, dt.datetime.fromisoformat(d), s, f) for o, u, e, d, s, f in VISITS],
    )
    conn.commit()
    return f"SELECT\n  x.*{visit_columns_sql(resolve_model(props).visit_columns)}\nFROM vq_visits x"


def _close(got, want):
    if want is None or got is None:
        return want is got
    return math.isclose(float(got), float(want), rel_tol=1e-9, abs_tol=1e-9)


def test_every_scope_executes_and_the_numbers_are_the_hand_computed_ones(conn, registry, visit_sql):
    props, inds = registry
    cur = conn.cursor()
    cur.execute(compile_rollup_sql(props, inds, visit_sql, scopes=NON_LLO_SCOPES, as_of=AS_OF))
    cols = [c.name for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.commit()

    assert {r["scope"] for r in rows} == set(NON_LLO_SCOPES)
    by_key = {(r["scope"], r.get("opportunity_id"), r.get("username")): r for r in rows if r["scope"] != "month"}
    for key, want in EXPECTED.items():
        row = by_key[key]
        for col, value in want.items():
            assert _close(row[col], value), f"{key} {col}: got {row[col]!r}, want {value!r}"
    assert len([r for r in rows if r["scope"] == "flw"]) == 4, "(opp, worker) is the worker: u1 is two people"

    months = {str(r["cohort_month"]): r["n_cases"] for r in rows if r["scope"] == "month"}
    assert months == EXPECTED_MONTHS, "cohorted on each beneficiary's first visit"
    cases = [r for r in rows if r["scope"] == "case"]
    assert sorted(r["case_id"] for r in cases) == ["101|e1", "101|e2", "101|e3", "102|e1", "102|e4"]
    for r in cases:  # at case scope a rate is the beneficiary's own contribution
        assert r["q02_denominator"] == 1 and float(r["q02"]) in (0.0, 100.0)


def test_runtime_evaluate_runs_it_by_name_filtered_to_its_series(conn, registry, visit_sql):
    rows = evaluate(
        None,
        [101, 102],
        visit_sql=visit_sql,
        registry_name="visit_quality",
        series="Q",
        scopes=["programme", "opportunity"],
        as_of=AS_OF,
        connection=conn,
    )
    conn.commit()
    programme = next(r for r in rows if r["scope"] == "programme")
    assert programme["q01"] == 5 and _close(programme["q02"], 60.0)
    assert not any(k.startswith(("c0", "n0")) for k in programme)


def test_the_entity_key_is_a_visit_filter(conn, registry, visit_sql):
    """The per-entity drill filters on the REGISTRY's key, not a hardcoded case id."""
    props, inds = registry
    rows = evaluate(
        None,
        [101, 102],
        visit_sql=visit_sql,
        registry_documents=(props, inds),
        scopes=["case"],
        as_of=AS_OF,
        visit_filter={"entity_id": "e1"},
        connection=conn,
    )
    conn.commit()
    assert sorted(r["case_id"] for r in rows) == ["101|e1", "102|e1"]
    with pytest.raises(SemanticRuntimeError, match="baby_case_id"):
        evaluate(
            None,
            [101],
            visit_sql=visit_sql,
            registry_documents=(props, inds),
            scopes=["case"],
            as_of=AS_OF,
            visit_filter={"baby_case_id": "e1"},
            connection=conn,
        )


def test_grading_uses_its_own_denominator_floor(conn, registry, visit_sql):
    """defaults.min_denominator: 5 -- the programme (5 beneficiaries) is graded, an
    opportunity of 3 is `insufficient`. KMC's 25 appears nowhere."""
    props, inds = registry
    rows = evaluate(
        None,
        [101, 102],
        visit_sql=visit_sql,
        registry_documents=(props, inds),
        scopes=["programme", "opportunity", "flw"],
        as_of=AS_OF,
        connection=conn,
    )
    conn.commit()
    payload = snap.build(
        spec={},
        rows=rows,
        measures=measure_catalog(filter_to_series(inds, "Q")),
        deployment={},
        registry_min_denominator=resolve_model(props, inds).min_denominator,
    )
    assert payload["programInd"]["Q02"]["band"] == "green"  # 60% against bands [60, 40]
    assert payload["programInd"]["Q02"]["value"] == pytest.approx(0.6)
    assert payload["programInd"]["Q03"]["band"] == "red"  # 40% flagged against [10, 25], lower is better
    assert {o["opp"]: o["ind"]["Q02"]["band"] for o in payload["byOpp"]} == {101: "insufficient", 102: "insufficient"}
    assert payload["byLLO"] == [] and payload["credibility"] == {}


# ── explanation ──────────────────────────────────────────────────────────────


def test_the_explanation_speaks_of_beneficiaries_not_babies(registry):
    props, inds = registry
    texts = []
    for ind in ("Q01", "Q02", "Q03", "Q04", "Q05"):
        e = explain(props, inds, ind)
        assert e["entity"] == {"name": "beneficiary", "plural": "beneficiaries"}
        assert "(visits)" in e["layer1"]
        texts.append(str(e["english"]))
    blob = " ".join(texts)
    assert "bab" not in blob.lower()
    # The floor is the registry's `defaults.min_denominator`: the definition names the
    # same one the grader withholds below, though Q02 declares none of its own.
    assert english(inds, props, "Q02")["definition"] == (
        "The number of beneficiaries where visited twice, as a percentage of the number of beneficiaries."
        " Shown only when the denominator is at least 5."
    )
    how = english(inds, props, "Q05")["how"]
    assert how["base"]["what"] == "beneficiaries" and how["value"] == "average visits per beneficiary"
    md = to_markdown([explain(props, inds, "Q02")])
    assert "one row per beneficiary" in md and "bab" not in md.lower()
