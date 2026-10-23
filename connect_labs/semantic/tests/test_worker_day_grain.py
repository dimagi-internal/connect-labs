"""`grain: worker_day`: a measure computed over worker-days, not over entity rows.

MBW's Daily Meter/Visit is the median, across an FLW's days, of that day's median
distance between home visits to different mothers. A day spans several mothers, so
no mother row can carry it, and the closest the entity grain could give -- a median
per mother, then across mothers -- gave a different colour for 8 of 81 FLWs over six
weeks of live data. The fixture below has known day medians, so every level is a
number that can be checked by hand:

    ada   1 Sep: 10, 30 -> 20     2 Sep: 100 -> 100     3 Sep: (no value)
    bola  1 Sep: 5, 7, 9 -> 7

    flw ada = median(20, 100) = 60     flw bola = 7
    opportunity / llo / programme = median(20, 100, 7) = 20

Everything here executes the compiled SQL against Postgres.
"""

from __future__ import annotations

import copy
import datetime as dt

import pytest

from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT, AnalysisPipelineConfig
from connect_labs.labs.analysis.config import FieldComputation as F
from connect_labs.semantic.compiler import compile_rollup_sql, validate

OPP = 977101
FUTURE = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)
LLO_MAP = {OPP: "Solina"}

PROPS = {
    "entity": {
        "name": "mother",
        "plural": "mothers",
        "key": "mother_case_id",
        "cohort_date": "first_visit",
        "worker": "last_visit",
    },
    "visit_columns": [{"name": "hop_m", "sql": "CAST(hop AS FLOAT)"}],
    "pipelines": {"entity": "visits"},
    "aggregates": [{"name": "first_visit", "sql": "MIN(visit_date)"}],
    "properties": [{"name": "is_mother", "type": "bool", "sql": "TRUE"}],
}

INDICATORS = {
    "version": 1,
    "cube": "mbw_mother",
    "defaults": {"min_denominator": 1},
    "series": ["M"],
    "measures": [
        {
            "name": "m03",
            "type": "number",
            "grain": "worker_day",
            "sql": "hop_m",
            "meta": {"indicator": "M03", "unit": "m", "direction": "higher"},
        },
        {"name": "m03_denominator", "type": "count", "grain": "worker_day", "sql": "hop_m"},
        {
            "name": "m01",
            "type": "number",
            "sql": "1.0 * {m01_mothers}",
            "meta": {"indicator": "M01", "unit": "mothers", "direction": "higher"},
        },
        {"name": "m01_mothers", "type": "count"},
    ],
}


def _config():
    config = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[F(name="mother_case_id", path="form.mother"), F(name="hop", path="form.hop")],
    )
    config.pipeline_id = 22143
    return config


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


@pytest.fixture
def day_cache(db):
    _visit("a1", "ada", "2026-09-01", "m1", 10)
    _visit("a2", "ada", "2026-09-01", "m2", 30)
    _visit("a3", "ada", "2026-09-02", "m1", 100)
    _visit("a4", "ada", "2026-09-03", "m3")
    _visit("b1", "bola", "2026-09-01", "m4", 5)
    _visit("b2", "bola", "2026-09-01", "m5", 7)
    _visit("b3", "bola", "2026-09-01", "m6", 9)
    return True


def _evaluate(scopes, *, as_of="DATE '2026-09-30'", llo_map=LLO_MAP, indicators=INDICATORS):
    from django.db import connection

    from connect_labs.semantic.runtime import evaluate

    return evaluate(
        _config(),
        [OPP],
        registry_documents=(PROPS, indicators),
        scopes=scopes,
        as_of=as_of,
        llo_map=llo_map,
        connection=connection,
    )


def _by_scope(rows):
    out = {}
    for r in rows:
        key = (r["scope"], r.get("username") if r["scope"] == "flw" else None)
        out[key] = r
    return out


def test_each_level_is_the_median_of_its_day_medians(day_cache):
    rows = _by_scope(_evaluate(["programme", "opportunity", "llo", "flw"]))
    assert rows[("flw", "ada")]["m03"] == pytest.approx(60)
    assert rows[("flw", "bola")]["m03"] == pytest.approx(7)
    for scope in ("programme", "opportunity", "llo"):
        assert rows[(scope, None)]["m03"] == pytest.approx(20), scope


def test_a_count_counts_the_values_over_the_days(day_cache):
    rows = _by_scope(_evaluate(["programme", "flw"]))
    assert rows[("flw", "ada")]["m03_denominator"] == 3
    assert rows[("flw", "bola")]["m03_denominator"] == 3
    assert rows[("programme", None)]["m03_denominator"] == 6


def test_entity_measures_are_unchanged_beside_it(day_cache):
    rows = _by_scope(_evaluate(["programme", "flw"]))
    assert rows[("flw", "ada")]["m01_mothers"] == 3
    assert rows[("flw", "bola")]["m01_mothers"] == 3
    assert rows[("programme", None)]["m01_mothers"] == 6


def test_a_scope_without_a_worker_day_level_reads_null(day_cache):
    rows = _evaluate(["month"])
    assert rows and all(r["m03"] is None for r in rows)


def test_the_as_of_cut_applies(day_cache):
    """Only 1 Sep exists as of 1 Sep: ada 20, bola 7, programme median(20, 7)."""
    rows = _by_scope(_evaluate(["programme", "flw"], as_of="DATE '2026-09-01'"))
    assert rows[("flw", "ada")]["m03"] == pytest.approx(20)
    assert rows[("programme", None)]["m03"] == pytest.approx(13.5)


def test_one_scope_at_a_time_agrees_with_the_rollup(day_cache):
    from django.db import connection

    from connect_labs.semantic.runtime import evaluate

    single = evaluate(
        _config(),
        [OPP],
        registry_documents=(PROPS, INDICATORS),
        scope="flw",
        as_of="DATE '2026-09-30'",
        llo_map=LLO_MAP,
        connection=connection,
    )
    assert {r["username"]: r["m03"] for r in single} == pytest.approx({"ada": 60, "bola": 7})


def test_a_materialised_props_table_reads_the_same(day_cache):
    """A saved run evaluates over a temporary props TABLE, not the CTE chain."""
    from django.db import connection

    from connect_labs.semantic.runtime import evaluate_with_cases

    rows, cases, _dropped = evaluate_with_cases(
        _config(),
        [OPP],
        scopes=["programme", "flw"],
        case_fields={},
        registry_documents=(PROPS, INDICATORS),
        as_of="DATE '2026-09-30'",
        llo_map=LLO_MAP,
        connection=connection,
    )
    rows = _by_scope(rows)
    assert rows[("flw", "ada")]["m03"] == pytest.approx(60)
    assert rows[("programme", None)]["m03"] == pytest.approx(20)
    assert len(cases) == 6


def test_no_llo_map_still_compiles_every_other_level(day_cache):
    rows = _by_scope(_evaluate(["programme", "flw"], llo_map=None))
    assert rows[("programme", None)]["m03"] == pytest.approx(20)


# ── validation ─────────────────────────────────────────────────────────────────


def _problems(**changes):
    indicators = copy.deepcopy(INDICATORS)
    indicators["measures"][0].update(changes)
    return validate(PROPS, indicators)


def test_the_fixture_validates():
    assert validate(PROPS, INDICATORS) == []


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"grain": "worker_week"}, "is not a grain"),
        ({"type": "sum"}, "type number or count"),
        ({"sql": "not_a_visit_column"}, "must name one of the registry's visit_columns"),
        ({"sql": "hop_m + 1"}, "must name one of the registry's visit_columns"),
        ({"filters": [{"sql": "{CUBE}.is_mother"}]}, "takes no filters"),
    ],
)
def test_a_malformed_worker_day_measure_is_refused(changes, expected):
    assert any(expected in p for p in _problems(**changes))


def test_no_measure_may_reference_a_worker_day_measure():
    indicators = copy.deepcopy(INDICATORS)
    indicators["measures"].append({"name": "m04", "type": "number", "sql": "{m03} * 2"})
    assert any("cannot reference m03" in p for p in validate(PROPS, indicators))


def test_the_definition_says_what_it_computes():
    from connect_labs.semantic.explain import english

    definition = english(INDICATORS, PROPS, "M03")["definition"]
    assert definition.startswith("The median, across each worker's days, of that day's median")


def test_a_registry_without_a_grain_compiles_no_worker_day_sql():
    indicators = copy.deepcopy(INDICATORS)
    indicators["measures"] = indicators["measures"][2:]
    sql = compile_rollup_sql(PROPS, indicators, "SELECT 1", scopes=["programme", "flw"], llo_map=LLO_MAP)
    assert "worker_day" not in sql and "wd_" not in sql
