"""`grain: worker_day` sums, and a ratio of them, credit the worker who MADE each visit.

MBW's photo approval rate is a per-worker figure over the photos each worker TOOK.
MBW's registry is a mother registry crediting each mother to her latest visitor
(`entity.worker: last_visit`), so an entity-grain ratio puts a photo on whoever has
the mother now. Worker-day rows are the visits' own workers, so a ratio of two
worker-day SUMS is the visit maker's figure, at every scope.

The photo results come from a lookup on visit_id against a pipeline of one row per
audited visit -- the shape of the `labs_audits` source (PR #2800); here the rows are
written straight into a non-visit pipeline's own raw-cache slot. Invented fixture:

    visit  worker  mother  passed/decided  hop   mother's worker (last_visit)
    1      ada     m1      2 / 2           10    m1 -> bola (bola visited last)
    2      ada     m2      1 / 2           30    m2 -> ada
    3      bola    m1      0 / 2            5
    4      bola    m3      (not audited)    7    m3 -> bola
    5      ada     m2      0 / 0 (pending)

    photo approval, by visit maker:   ada 3/4 = 75%   bola 0/2 = 0%   programme 3/6 = 50%
    the same ratio over mother rows:  ada 1/2 = 50%   bola 2/4 = 50%
    hop median (worker_day number):   ada median(10, 30) = 20   bola median(5, 7) = 6
                                      programme median(10, 30, 5, 7) = 8.5

Everything here executes the compiled SQL against Postgres, except the validation
and compile-shape tests at the bottom.
"""

from __future__ import annotations

import copy
import datetime as dt

import pytest

from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT, AnalysisPipelineConfig, DataSourceConfig
from connect_labs.labs.analysis.config import FieldComputation as F
from connect_labs.semantic.compiler import (
    compile_indicator_sql,
    compile_measures,
    compile_rollup_sql,
    scope_dependent_measures,
    validate,
)
from connect_labs.semantic.layer1 import LOOKUPS_KEY

OPP = 977401
PHOTO_PIPELINE = 5701
AS_OF = "DATE '2026-09-30'"
FUTURE = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)
LLO_MAP = {OPP: "Partner A"}


def _int(x):
    return int(float(x)) if x else None


def _visits_config():
    config = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[F(name="mother_case_id", path="form.mother"), F(name="hop", path="form.hop")],
    )
    config.pipeline_id = 22143
    return config


def _photo_config():
    """Stands in for the `labs_audits` pipeline (#2800): same fields, own raw slot."""
    config = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[
            F(name="n_pass", path="audit.n_pass", transform=_int, aggregation="sum"),
            F(name="n_decided", path="audit.n_decided", transform=_int, aggregation="sum"),
        ],
        data_source=DataSourceConfig(type="connect_export", endpoint="photo_audits"),
    )
    config.pipeline_id = PHOTO_PIPELINE
    return config


PROPS = {
    "entity": {
        "name": "mother",
        "plural": "mothers",
        "key": "mother_case_id",
        "cohort_date": "first_visit",
        "worker": "last_visit",
    },
    "visit_columns": [
        # An unaudited visit has no lookup row: it adds nothing.
        {"name": "photos_passed_v", "sql": "COALESCE(photos_pass, 0)"},
        {"name": "photos_decided_v", "sql": "COALESCE(photos_decided, 0)"},
        {"name": "hop_m", "sql": "CAST(hop AS FLOAT)"},
    ],
    "pipelines": {
        "entity": "visits",
        "lookups": {
            "photo_audit": {
                "pipeline": "photo_audits",
                "on": "visit_id",
                "key": "visit_id",
                "pick": "latest",
                "fields": {"photos_pass": "n_pass", "photos_decided": "n_decided"},
            }
        },
    },
    "aggregates": [
        {"name": "first_visit", "sql": "MIN(visit_date)"},
        {"name": "photos_passed_n", "sql": "SUM(photos_passed_v)"},
        {"name": "photos_decided_n", "sql": "SUM(photos_decided_v)"},
    ],
    "properties": [{"name": "is_mother", "type": "bool", "sql": "TRUE"}],
}

PHOTO_MEASURES = [
    {
        "name": "photo_approval",
        "title": "Photo approval rate",
        "type": "number",
        "sql": "100.0 * {photo_approval_numerator} / NULLIF({photo_approval_denominator}, 0)",
        "meta": {
            "indicator": "P01",
            "label": "Photo approval rate",
            "category": "Photo quality",
            "unit": "%",
            "direction": "higher",
            "flw_applicable": True,
        },
    },
    {"name": "photo_approval_numerator", "type": "sum", "grain": "worker_day", "sql": "photos_passed_v"},
    {"name": "photo_approval_denominator", "type": "sum", "grain": "worker_day", "sql": "photos_decided_v"},
]

INDICATORS = {
    "version": 1,
    "cube": "mbw_mother",
    "defaults": {"min_denominator": 1},
    "series": ["P", "M"],
    "measures": [
        *PHOTO_MEASURES,
        # The same ratio over MOTHER rows: what an entity-grain measure gives.
        {"name": "p02", "type": "number", "sql": "100.0 * {p02_numerator} / NULLIF({p02_denominator}, 0)"},
        {"name": "p02_numerator", "type": "sum", "sql": "{CUBE}.photos_passed_n"},
        {"name": "p02_denominator", "type": "sum", "sql": "{CUBE}.photos_decided_n"},
        # A worker_day median beside them, unchanged.
        {"name": "m03", "type": "number", "grain": "worker_day", "sql": "hop_m", "meta": {"indicator": "M03"}},
    ],
}


def _visit(visit_id, username, date, mother, hop=None):
    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

    form = {"mother": mother}
    if hop is not None:
        form["hop"] = str(hop)
    RawVisitCache.objects.create(
        opportunity_id=OPP,
        pipeline_id=USER_VISITS_RAW_SLOT,
        visit_count=1,
        expires_at=FUTURE,
        visit_id=visit_id,
        username=username,
        status="approved",
        visit_date=dt.date.fromisoformat(date),
        form_json={"form": form},
    )


def _audited(visit_id, username, n_pass, n_decided):
    """One row of the photo pipeline, in the labs_audits row shape."""
    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

    RawVisitCache.objects.create(
        opportunity_id=OPP,
        pipeline_id=PHOTO_PIPELINE,
        visit_count=4,
        expires_at=FUTURE,
        visit_id=visit_id,
        username=username,
        status="",
        form_json={"audit": {"visit_id": visit_id, "n_pass": n_pass, "n_decided": n_decided}},
    )


@pytest.fixture
def photo_cache(db):
    _visit("1", "ada", "2026-09-01", "m1", 10)
    _visit("2", "ada", "2026-09-02", "m2", 30)
    _visit("3", "bola", "2026-09-03", "m1", 5)
    _visit("4", "bola", "2026-09-04", "m3", 7)
    _visit("5", "ada", "2026-09-05", "m2")
    _audited("1", "ada", 2, 2)
    _audited("2", "ada", 1, 2)
    _audited("3", "bola", 0, 2)
    _audited("5", "ada", 0, 0)
    return True


def _evaluate(
    scopes=None, *, scope="programme", props=PROPS, indicators=INDICATORS, visit_filter=None, llo_map=LLO_MAP
):
    from django.db import connection

    from connect_labs.semantic.runtime import evaluate

    return evaluate(
        _visits_config(),
        [OPP],
        extra_fields={LOOKUPS_KEY: {"photo_audit": _photo_config()}},
        registry_documents=(props, indicators),
        scope=scope,
        scopes=scopes,
        as_of=AS_OF,
        llo_map=llo_map,
        visit_filter=visit_filter,
        connection=connection,
    )


def _by_scope(rows):
    return {(r["scope"], r.get("username") if r["scope"] == "flw" else None): r for r in rows}


# ── execution ──────────────────────────────────────────────────────────────────


def test_the_ratio_credits_the_worker_who_made_each_visit(photo_cache):
    rows = _by_scope(_evaluate(["programme", "opportunity", "llo", "flw"]))
    assert rows[("flw", "ada")]["photo_approval"] == pytest.approx(75.0)
    assert rows[("flw", "bola")]["photo_approval"] == pytest.approx(0.0)
    assert rows[("flw", "ada")]["photo_approval_denominator"] == 4
    assert rows[("flw", "bola")]["photo_approval_denominator"] == 2
    for scope in ("programme", "opportunity", "llo"):
        assert rows[(scope, None)]["photo_approval"] == pytest.approx(50.0), scope
        assert rows[(scope, None)]["photo_approval_numerator"] == 3, scope
        assert rows[(scope, None)]["photo_approval_denominator"] == 6, scope


def test_the_entity_grain_ratio_credits_the_mothers_worker_instead(photo_cache):
    """Why the grain is needed: over mother rows, v1's two passes count for bola."""
    rows = _by_scope(_evaluate(["programme", "flw"]))
    assert rows[("flw", "ada")]["p02"] == pytest.approx(50.0)
    assert rows[("flw", "bola")]["p02"] == pytest.approx(50.0)
    assert rows[("programme", None)]["p02"] == pytest.approx(rows[("programme", None)]["photo_approval"])


def test_a_worker_day_median_beside_it_is_unchanged(photo_cache):
    rows = _by_scope(_evaluate(["programme", "flw"]))
    assert rows[("flw", "ada")]["m03"] == pytest.approx(20)
    assert rows[("flw", "bola")]["m03"] == pytest.approx(6)
    assert rows[("programme", None)]["m03"] == pytest.approx(8.5)


def test_a_scope_without_a_worker_day_level_reads_null(photo_cache):
    rows = _evaluate(["month"])
    assert rows and all(r["photo_approval"] is None for r in rows)


def test_one_scope_at_a_time_agrees_with_the_rollup(photo_cache):
    """compile_indicator_sql (one scope) resolves the ratio's references at that scope."""
    flw = {r["username"]: r["photo_approval"] for r in _evaluate(scope="flw")}
    assert flw == pytest.approx({"ada": 75.0, "bola": 0.0})
    (prog,) = _evaluate(scope="programme")
    assert prog["photo_approval"] == pytest.approx(50.0)
    (opp,) = _evaluate(scope="opportunity")
    assert opp["photo_approval"] == pytest.approx(50.0)


def test_a_one_worker_evaluation_is_that_workers_own_photos(photo_cache):
    rows = _by_scope(_evaluate(["programme", "flw"], visit_filter={"username": "ada"}))
    assert rows[("flw", "ada")]["photo_approval"] == pytest.approx(75.0)
    assert rows[("programme", None)]["photo_approval"] == pytest.approx(75.0)


def test_a_materialised_props_table_reads_the_same(photo_cache):
    """A saved run (snapshot) evaluates over a temporary props TABLE, not the CTE chain."""
    from django.db import connection

    from connect_labs.semantic.runtime import evaluate_with_cases

    rows, _cases, _dropped = evaluate_with_cases(
        _visits_config(),
        [OPP],
        extra_fields={LOOKUPS_KEY: {"photo_audit": _photo_config()}},
        scopes=["programme", "llo", "flw"],
        case_fields={},
        registry_documents=(PROPS, INDICATORS),
        as_of=AS_OF,
        llo_map=LLO_MAP,
        connection=connection,
    )
    rows = _by_scope(rows)
    assert rows[("flw", "ada")]["photo_approval"] == pytest.approx(75.0)
    assert rows[("flw", "bola")]["photo_approval"] == pytest.approx(0.0)
    assert rows[("llo", None)]["photo_approval"] == pytest.approx(50.0)


# ── validation and compilation (no database) ───────────────────────────────────


def test_the_fixture_validates():
    assert validate(PROPS, INDICATORS, llo_map=LLO_MAP) == []


def test_the_ratio_and_its_parts_are_scope_dependent():
    assert scope_dependent_measures(INDICATORS) == [
        "photo_approval",
        "photo_approval_numerator",
        "photo_approval_denominator",
        "m03",
    ]


def test_a_reference_resolves_to_each_scopes_level():
    flw = compile_measures(INDICATORS, "flw", LLO_MAP)["photo_approval"]
    assert "photo_approval_numerator__flw" in flw and "__prog" not in flw
    month = compile_measures(INDICATORS, "month", LLO_MAP)["photo_approval"]
    assert "__" not in month and "NULL" in month
    # Default: the programme level, as before.
    assert "photo_approval_numerator__prog" in compile_measures(INDICATORS)["photo_approval"]


def test_the_rollup_reads_each_scopes_level_for_the_ratio():
    sql = compile_rollup_sql(PROPS, INDICATORS, "SELECT 1", scopes=["programme", "flw"], llo_map=LLO_MAP)
    assert "photo_approval_numerator__flw" in sql and "photo_approval_numerator__prog" in sql
    single = compile_indicator_sql(PROPS, INDICATORS, "SELECT 1", scope="flw", llo_map=LLO_MAP)
    assert "MAX(props.photo_approval_denominator__flw)" in single
    assert "MAX(props.photo_approval_denominator__prog)" not in single


def test_a_worker_day_sum_sums_its_days():
    sql = compile_rollup_sql(PROPS, INDICATORS, "SELECT 1", scopes=["programme"], llo_map=LLO_MAP)
    assert "SUM(photos_passed_v) AS photo_approval_numerator" in sql


def _with(*measures):
    indicators = copy.deepcopy(INDICATORS)
    indicators["measures"] = [*indicators["measures"], *measures]
    return validate(PROPS, indicators, llo_map=LLO_MAP)


def test_a_median_may_still_not_be_referenced():
    problems = _with({"name": "bad", "type": "number", "sql": "{m03} * 2"})
    assert any("cannot reference m03, a worker_day median" in p for p in problems)


def test_only_a_plain_number_measure_may_reference_a_worker_day_sum():
    problems = _with(
        {"name": "bad", "type": "sum", "sql": "{photo_approval_numerator}"},
    )
    assert any("only a plain type: number measure may reference photo_approval_numerator" in p for p in problems)


def test_a_number_may_reference_a_ratio_of_worker_day_sums_transitively():
    indicators = copy.deepcopy(INDICATORS)
    indicators["measures"].append({"name": "photo_rejection", "type": "number", "sql": "100.0 - {photo_approval}"})
    assert validate(PROPS, indicators, llo_map=LLO_MAP) == []
    assert "photo_rejection" in scope_dependent_measures(indicators)
    assert "__flw" in compile_measures(indicators, "flw", LLO_MAP)["photo_rejection"]


def test_a_worker_day_sum_must_name_a_visit_column():
    problems = _with({"name": "bad_sum", "type": "sum", "grain": "worker_day", "sql": "photos_pass"})
    assert any("must name one of the registry's visit_columns" in p for p in problems)


def test_the_definitions_say_who_is_credited():
    from connect_labs.semantic.explain import english

    definition = english(INDICATORS, PROPS, "P01")["definition"]
    assert "credited to the worker who made each visit" in definition
