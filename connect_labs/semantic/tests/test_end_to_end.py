"""The WHOLE chain, in CI: pipeline schema -> config -> layer 1 -> compile -> execute.

WHY THIS FILE EXISTS

Three bugs reached production in a row, each in code with a passing suite, and each
invisible for a DIFFERENT structural reason:

  1. The endpoint passed the raw schema dict where an AnalysisPipelineConfig was
     required. Every endpoint test patched `evaluate`, so the mock went straight
     through the offending line.
  2. The compiled SQL contained a bare `%` (SQL modulo), which psycopg2 reads as a
     parameter placeholder. The parity suite executes real SQL — but through RAW
     psycopg2, which only interpolates when params are passed. The endpoint uses
     Django's cursor, and that is the path that trips.
  3. Layer 1 was built from the entity pipeline alone, so `weight_g` — which lives in
     a separate weight-series pipeline and which properties.yml is written against —
     did not exist. The parity suite supplies a literal `SELECT * FROM fixture_visits`
     and therefore never exercised Layer 1 at all.

Every one of those was found by curling the deployed endpoint. The common thread is
that each suite's fake stood exactly where the defect was, so this test deliberately
fakes NOTHING between the pipeline schema and the rows: real RawVisitCache rows, the
real schema->config conversion, real Layer 1 generation from BOTH pipelines, the real
compiler, executed through DJANGO's cursor.

WHAT IT ACTUALLY COVERS — measured, not assumed

Bugs 1 and 3: yes. Bug 3 is asserted directly below (omit the weight series and the
query must fail on `weight_g`), and bug 1 cannot occur because this passes a real
config.

Bug 2: NO — and that is worth writing down rather than assuming. Reintroducing the
bare `%` was tried against this file and all three tests still PASSED. Whatever makes
production's cursor interpolate is not reproduced by the local test database, so this
file cannot stand in for that check. The static guard in test_compiler.py
(`test_no_compiled_sql_contains_a_bare_percent_operator`) is what covers it, and both
are needed. A comment claiming this file catches all three would be exactly the kind
of false confidence that produced the sequence in the first place.
"""

from __future__ import annotations

import datetime as dt

import pytest

from connect_labs.semantic.runtime import evaluate

pytestmark = pytest.mark.django_db

OPP = 999042
ENTITY_PIPELINE = 999108
VISIT_PIPELINE = 999109

# Shaped after the live KMC pipelines: the entity pipeline carries registration
# fields and visit markers; the weight series carries the per-visit scalar.
ENTITY_SCHEMA = {
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "linking_field": "entity_id",
    "terminal_stage": "entity",
    "fields": [
        {"name": "reg_date", "paths": ["form.reg_date"], "transform": "date", "aggregation": "first"},
        {"name": "birth_weight_g", "paths": ["form.birth_weight"], "transform": "kg_to_g", "aggregation": "first"},
        {
            "name": "enrollment_weight_g",
            "paths": ["form.enrol_weight"],
            "transform": "kg_to_g",
            "aggregation": "first",
        },
        {"name": "days_discharge_to_reg", "paths": ["form.days_to_reg"], "transform": "float", "aggregation": "first"},
        {
            "name": "death_visits",
            "paths": ["form.child_alive"],
            "filter_op": "contains_word",
            "aggregation": "count",
            "filter_paths": ["form.child_alive"],
            "filter_value": "no",
        },
        {
            "name": "danger_visits",
            "paths": ["form.danger"],
            "filter_op": "contains_word",
            "aggregation": "count",
            "filter_paths": ["form.danger"],
            "filter_value": "yes",
        },
        {
            "name": "referral_visits",
            "paths": ["form.referred"],
            "filter_op": "contains_word",
            "aggregation": "count",
            "filter_paths": ["form.referred"],
            "filter_value": "yes",
        },
        {
            "name": "self_referral_visits",
            "paths": ["form.self_ref"],
            "filter_op": "contains_word",
            "aggregation": "count",
            "filter_paths": ["form.self_ref"],
            "filter_value": "yes",
        },
        {"name": "ebf_visits", "paths": ["form.ebf"], "aggregation": "count"},
        {"name": "kmc_hours_mean", "paths": ["form.kmc_hours"], "transform": "float", "aggregation": "avg"},
        {
            "name": "hospital_discharge_date",
            "paths": ["form.discharge_date"],
            "transform": "date",
            "aggregation": "first",
        },
        {"name": "baby_case_id", "paths": ["form.case.@case_id", "entity_id"], "aggregation": "first"},
        {"name": "form_names", "path": "form.@name", "aggregation": "list"},
    ],
}

VISIT_SCHEMA = {
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "linking_field": "entity_id",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "weight_g", "paths": ["form.weight"], "transform": "kg_to_g", "aggregation": "first"},
        {"name": "baby_case_id", "paths": ["form.case.@case_id", "entity_id"], "aggregation": "first"},
    ],
}


def _config(schema, pipeline_id):
    """The SAME conversion the endpoint uses — not a stand-in for it."""
    from connect_labs.workflow.data_access import PipelineDataAccess

    # A token is required at construction even though _schema_to_config is a pure
    # transformation that never calls the API. Passing one keeps this the REAL
    # conversion rather than a reimplementation of it, which is the whole point.
    return PipelineDataAccess(access_token="test-token")._schema_to_config(schema, pipeline_id)


@pytest.fixture
def visit_cache(db):
    """Two babies across the growth window, written as real cache rows."""
    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

    expires = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)
    rows = [
        ("b1", "asha", "2026-01-01", 1.500, "Registration"),
        ("b1", "asha", "2026-01-15", 1.700, "Follow-up"),
        ("b1", "asha", "2026-02-20", 2.100, "Follow-up"),
        ("b2", "ravi", "2026-01-02", 1.400, "Registration"),
        ("b2", "ravi", "2026-02-10", 1.800, "Follow-up"),
    ]
    for pid in (ENTITY_PIPELINE, VISIT_PIPELINE):
        for i, (case, user, date, wkg, form) in enumerate(rows):
            RawVisitCache.objects.create(
                opportunity_id=OPP,
                pipeline_id=pid,
                visit_count=len(rows),
                expires_at=expires,
                visit_id=f"{pid}-{i}",
                username=user,
                entity_id=case,
                visit_date=dt.date.fromisoformat(date),
                status="approved",
                form_json={
                    "form": {
                        "@name": form,
                        "case": {"@case_id": case},
                        "weight": str(wkg),
                        "birth_weight": "1.5",
                        "enrol_weight": "1.5",
                        "reg_date": date,
                        "discharge_date": date,
                        "days_to_reg": "1",
                        "child_alive": "yes",
                        "danger": "no",
                        "referred": "no",
                        "self_ref": "no",
                        "ebf": "yes",
                        "kmc_hours": "4",
                    }
                },
            )
    return True


def test_the_whole_chain_runs_and_returns_banded_numbers(visit_cache):
    """No fake anywhere between the pipeline schema and the rows.

    Converts the schema the way the endpoint does, generates Layer 1 from BOTH
    pipelines, compiles the real registry, and executes through Django's cursor —
    the combination none of the existing suites covered.
    """
    rows = evaluate(
        _config(ENTITY_SCHEMA, ENTITY_PIPELINE),
        [OPP],
        extra_fields={"weight_g": _config(VISIT_SCHEMA, VISIT_PIPELINE)},
        series="N",
        scopes=["programme", "opportunity", "flw"],
        as_of="'2026-04-01'",
    )

    assert rows, "the chain produced no rows at all"
    assert {r["scope"] for r in rows} == {"programme", "opportunity", "flw"}

    programme = [r for r in rows if r["scope"] == "programme"][0]
    assert programme["n_cases"] == 2
    # value AND denominator, the registry's structural rule
    assert "n03" in programme and "n03_denominator" in programme
    # the weight column resolved — this is bug 3's assertion
    assert "n14" in programme, "the weight-rounding metric needs the weight series"


def test_the_single_scope_path_executes_too(visit_cache):
    """compile_indicator_sql and compile_rollup_sql are different code paths, and the
    endpoint can reach either. (Note: this does NOT catch a bare `%` — that was tried
    and passed; see the module docstring.)"""
    rows = evaluate(
        _config(ENTITY_SCHEMA, ENTITY_PIPELINE),
        [OPP],
        extra_fields={"weight_g": _config(VISIT_SCHEMA, VISIT_PIPELINE)},
        series="N",
        scope="programme",
        as_of="'2026-04-01'",
    )
    assert len(rows) == 1


def test_layer1_is_built_from_BOTH_pipelines(visit_cache):
    """Without the weight series the compiled SQL fails with
    `column "weight_g" does not exist`. Asserting the failure keeps the
    two-pipeline requirement from silently regressing to one."""
    from connect_labs.semantic.runtime import SemanticRuntimeError

    with pytest.raises(SemanticRuntimeError, match="weight_g"):
        evaluate(
            _config(ENTITY_SCHEMA, ENTITY_PIPELINE),
            [OPP],
            series="N",
            scope="programme",
            as_of="'2026-04-01'",
        )
