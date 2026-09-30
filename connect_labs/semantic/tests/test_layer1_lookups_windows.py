"""Layer 1 lookups (fields from another pipeline's own rows) and window visit columns.

MBW is the case that needed both. Its audit reads two CommCare HQ form pipelines
beside the Connect visits -- Register Mother (eligibility, visit schedules) and a
supervisor app's Gold Standard checklist (a score per worker) -- and its GPS metrics
compare each visit with the PREVIOUS visit to the same mother. None of that could
reach the semantic layer:

  * `extra_fields` evaluates another pipeline's field paths against the entity
    pipeline's OWN visit rows, so an HQ form's paths read NULL;
  * Layer 1 had no window, so "the previous visit" could not be expressed;
  * and Layer 1 read EVERY raw-cache slot of the opportunity, so the HQ forms cached
    for it arrived as visits.

Everything here executes the generated SQL against Postgres -- the bar is the rows,
not the text.
"""

from __future__ import annotations

import datetime as dt

import pytest

from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT, AnalysisPipelineConfig, DataSourceConfig
from connect_labs.labs.analysis.config import FieldComputation as F
from connect_labs.semantic.compiler import model_problems
from connect_labs.semantic.layer1 import LOOKUPS_KEY, build_visit_sql

OPP, OTHER_OPP = 977001, 977002
REG_PIPELINE, GS_PIPELINE = 5501, 5502
FUTURE = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)


def _visits_config():
    config = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[
            F(name="mother_case_id", path="form.parents.parent.case.@case_id"),
            F(name="latitude", path="form.lat"),
            F(name="longitude", path="form.lon"),
        ],
    )
    config.pipeline_id = 22143  # any id: a visits pipeline reads the shared slot
    return config


def _hq_config(pipeline_id, form_name, fields):
    config = AnalysisPipelineConfig(
        grouping_key="username",
        fields=fields,
        data_source=DataSourceConfig(type="cchq_forms", form_name=form_name, app_id_source="opportunity"),
    )
    config.pipeline_id = pipeline_id
    return config


def _registrations_config():
    return _hq_config(
        REG_PIPELINE,
        "Register Mother",
        [
            F(name="mother_case_id", path="form.mother_case_id"),
            F(name="eligible_full_intervention_bonus", path="form.eligible_full_intervention_bonus"),
        ],
    )


def _gs_config():
    return _hq_config(GS_PIPELINE, "Gold Standard Visit Checklist", [F(name="gs_score", path="form.gs_score")])


PROPS = {
    "entity": {"name": "mother", "plural": "mothers", "key": "mother_case_id", "cohort_date": "first_visit"},
    "visit_columns": [
        {
            "name": "prev_visit_date",
            "previous": {"column": "visit_date", "partition_by": ["mother_case_id"], "order_by": "visit_date"},
        },
        {
            "name": "metres_from_prev",
            "distance_from_previous": {
                "lat": "latitude",
                "lon": "longitude",
                "partition_by": ["mother_case_id"],
                "order_by": "visit_date",
            },
        },
        # A row-level column may read a window column and a lookup column.
        {"name": "far_from_prev", "sql": "metres_from_prev > 1000"},
        {"name": "is_eligible", "sql": "eligible_at_reg = 'yes'"},
    ],
    "pipelines": {
        "entity": "visits",
        "lookups": {
            "registration": {
                "pipeline": "registrations",
                "on": "mother_case_id",
                "key": "mother_case_id",
                "fields": {"eligible_at_reg": "eligible_full_intervention_bonus"},
                "pick": "latest",
            },
            "gs": {
                "pipeline": "gs_forms",
                "on": "username",
                "key": "username",
                "fields": {"gs_score": "gs_score"},
                "pick": "max",
            },
        },
    },
    "aggregates": [{"name": "first_visit", "sql": "MIN(visit_date)"}],
}


def _lookups():
    return {LOOKUPS_KEY: {"registration": _registrations_config(), "gs": _gs_config()}}


def _row(opp, slot, visit_id, username, date, form, count=1):
    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

    RawVisitCache.objects.create(
        opportunity_id=opp,
        pipeline_id=slot,
        visit_count=count,
        expires_at=FUTURE,
        visit_id=visit_id,
        username=username,
        status="approved",
        visit_date=dt.date.fromisoformat(date),
        form_json={"form": form},
    )


def _visit(opp, visit_id, username, date, mother, lat=None, lon=None):
    form = {"parents": {"parent": {"case": {"@case_id": mother}}}}
    if lat is not None:
        form.update(lat=str(lat), lon=str(lon))
    _row(opp, USER_VISITS_RAW_SLOT, visit_id, username, date, form)


@pytest.fixture
def mbw_cache(db):
    # Mother m1: three visits, the middle one with no GPS reading, the last by a
    # DIFFERENT worker. Mother m2: one visit. Roughly 1.1 km between m1's two fixes.
    _visit(OPP, "v1", "ada", "2026-09-01", "m1", 9.0000, 7.0000)
    _visit(OPP, "v2", "ada", "2026-09-05", "m1")
    _visit(OPP, "v3", "bola", "2026-09-10", "m1", 9.0100, 7.0000)
    _visit(OPP, "v4", "bola", "2026-09-02", "m2", 9.5000, 7.5000)
    # The same mother id on ANOTHER opportunity: must never pair with OPP's visits.
    _visit(OTHER_OPP, "w1", "ada", "2026-09-03", "m1", 10.0, 8.0)

    # Register Mother (HQ), in its own slot. m1 registered twice: the later form wins.
    _row(
        OPP,
        REG_PIPELINE,
        "r1",
        "ada",
        "2026-08-01",
        {"mother_case_id": "m1", "eligible_full_intervention_bonus": "no"},
    )
    _row(
        OPP,
        REG_PIPELINE,
        "r2",
        "ada",
        "2026-08-15",
        {"mother_case_id": "m1", "eligible_full_intervention_bonus": "yes"},
    )
    # m2 has no registration form at all.
    # Gold Standard (HQ, separate app): text scores, where "100" must beat "90".
    _row(OPP, GS_PIPELINE, "g1", "ada", "2026-08-20", {"gs_score": "90"})
    _row(OPP, GS_PIPELINE, "g2", "ada", "2026-08-25", {"gs_score": "100"})
    _row(OPP, GS_PIPELINE, "g3", "bola", "2026-08-25", {"gs_score": "n/a"})
    return True


def _rows(sql, *cols):
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(f"SELECT visit_id, {', '.join(cols)} FROM ({sql}) q ORDER BY opportunity_id, visit_id")
        return {r[0]: r[1:] for r in cur.fetchall()}


def _sql(opps=(OPP,), **kw):
    return build_visit_sql(_visits_config(), list(opps), extra_fields=_lookups(), props_doc=PROPS, **kw)


# ── the slot leak ──────────────────────────────────────────────────────────────


def test_another_sources_rows_never_arrive_as_visits(mbw_cache):
    """Register Mother and Gold Standard forms are cached for the SAME opportunity,
    in their own slots. They are not visits and must not be counted as visits."""
    got = _rows(_sql(), "username")
    assert sorted(got) == ["v1", "v2", "v3", "v4"]


# ── lookups ────────────────────────────────────────────────────────────────────


def test_a_lookup_joins_the_latest_registration_and_leaves_the_unmatched_null(mbw_cache):
    got = _rows(_sql(), "eligible_at_reg", "is_eligible")
    assert got["v1"] == ("yes", True), "the later of m1's two registrations"
    assert got["v3"] == ("yes", True)
    assert got["v4"] == (None, None), "m2 has no registration: the visit stays, with NULLs"


def test_a_lookup_never_multiplies_visits(mbw_cache):
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT visit_id) FROM ({_sql()}) q")
        assert cur.fetchone() == (4, 4)


def test_max_compares_numbers_not_text_and_ignores_non_numbers(mbw_cache):
    got = _rows(_sql(), "gs_score")
    assert got["v1"][0] == 100, "'100' beats '90' as a number"
    assert got["v3"][0] is None, "bola's only score is 'n/a'"


def test_earliest_count_and_min_picks(mbw_cache):
    props = {**PROPS, "pipelines": {**PROPS["pipelines"], "lookups": {}}}
    props["visit_columns"] = []
    for pick, expected in (("earliest", "no"), ("count", 2), ("min", None)):
        props["pipelines"]["lookups"] = {
            "registration": {
                "pipeline": "registrations",
                "on": "mother_case_id",
                "key": "mother_case_id",
                "fields": {"reg_value": "eligible_full_intervention_bonus"},
                "pick": pick,
            }
        }
        sql = build_visit_sql(
            _visits_config(),
            [OPP],
            extra_fields={LOOKUPS_KEY: {"registration": _registrations_config()}},
            props_doc=props,
        )
        assert _rows(sql, "reg_value")["v1"][0] == expected, pick


def test_a_lookup_does_not_cross_opportunities(mbw_cache):
    # OTHER_OPP has a visit to a mother also called m1, and no registration.
    got = _rows(_sql(opps=(OPP, OTHER_OPP)), "eligible_at_reg")
    assert got["w1"] == (None,)


def test_a_lookup_whose_pipeline_the_workflow_lacks_is_refused_by_name(mbw_cache):
    with pytest.raises(ValueError, match="registration.*registrations"):
        build_visit_sql(_visits_config(), [OPP], extra_fields={LOOKUPS_KEY: {"gs": _gs_config()}}, props_doc=PROPS)


def test_a_python_computed_field_cannot_be_looked_up(mbw_cache):
    reg = _registrations_config()
    reg.fields.append(F(name="schedules", path="form.x", extractor=lambda v: v))
    props = {**PROPS, "pipelines": {**PROPS["pipelines"]}}
    props["pipelines"]["lookups"] = {
        "registration": {**PROPS["pipelines"]["lookups"]["registration"], "fields": {"s": "schedules"}}
    }
    props["visit_columns"] = []
    with pytest.raises(ValueError, match="computed in Python"):
        build_visit_sql(_visits_config(), [OPP], extra_fields={LOOKUPS_KEY: {"registration": reg}}, props_doc=props)


# ── windows ────────────────────────────────────────────────────────────────────


def test_previous_reads_the_same_mothers_earlier_visit(mbw_cache):
    got = _rows(_sql(opps=(OPP, OTHER_OPP)), "prev_visit_date")
    assert got["v1"] == (None,)
    assert got["v2"] == (dt.date(2026, 9, 1),)
    assert got["v3"] == (dt.date(2026, 9, 5),)
    assert got["v4"] == (None,), "m2's first visit"
    assert got["w1"] == (None,), "the same case id on another opportunity is another mother"


def test_distance_skips_a_visit_without_gps_and_pairs_with_the_last_fix(mbw_cache):
    got = _rows(_sql(), "metres_from_prev", "far_from_prev")
    assert got["v1"] == (None, None)
    assert got["v2"] == (None, None), "no reading of its own"
    metres, far = got["v3"]
    assert 1100 < metres < 1125, "0.01 deg of latitude from v1, v2 skipped"
    assert far is True


def test_a_workers_evaluation_keeps_other_workers_previous_visits(mbw_cache):
    """v3 is bola's; the visit it pairs with is ada's. Filtering the scan to bola
    would make v3 its mother's first fixed visit and its distance NULL."""
    sql = _sql(visit_filter={"opportunity_id": OPP, "username": "bola"})
    assert _rows(sql, "metres_from_prev")["v3"][0] > 1100
    where = sql[sql.index("WHERE opportunity_id IN") : sql.index("ORDER BY opportunity_id, visit_id")]
    assert "username" not in where, "with windows the compiler applies the worker filter, after Layer 1"


# ── validation ─────────────────────────────────────────────────────────────────


def test_the_mbw_shaped_model_validates():
    assert model_problems(PROPS) == []


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda p: p["pipelines"]["lookups"]["gs"].update(pick="avg"), "pick"),
        (lambda p: p["pipelines"]["lookups"]["gs"].update(on="username; DROP"), "on"),
        (lambda p: p["pipelines"]["lookups"]["gs"].update(fields={}), "fields"),
        (lambda p: p["pipelines"]["lookups"]["gs"].update(fields={"prev_visit_date": "gs_score"}), "already"),
        (lambda p: p["visit_columns"][0]["previous"].pop("partition_by"), "partition_by"),
        (lambda p: p["visit_columns"][1]["distance_from_previous"].update(lat="lat)"), "lat"),
        (lambda p: p["visit_columns"][1]["distance_from_previous"].update(skip_null=True), "skip_null"),
        (lambda p: p["visit_columns"][0]["previous"].update(skip_null="yes"), "skip_null"),
    ],
)
def test_a_malformed_lookup_or_window_is_refused(mutate, message):
    import copy

    props = copy.deepcopy(PROPS)
    mutate(props)
    problems = model_problems(props)
    assert any(message in p for p in problems), problems


# ── end to end: the compiler reads lookup and window columns ───────────────────


def test_indicators_over_lookup_and_window_columns_run(mbw_cache):
    """The whole chain: Layer 1 with lookups and windows, compiled and executed."""
    from django.db import connection

    from connect_labs.semantic.runtime import evaluate

    props = {
        **PROPS,
        "aggregates": [
            {"name": "first_visit", "sql": "MIN(visit_date)"},
            {"name": "any_far", "sql": "BOOL_OR(far_from_prev)"},
            {"name": "eligible", "sql": "BOOL_OR(is_eligible)"},
        ],
        "properties": [
            {"name": "was_far", "type": "bool", "sql": "COALESCE(any_far, FALSE)"},
            {"name": "was_eligible", "type": "bool", "sql": "COALESCE(eligible, FALSE)"},
        ],
    }
    indicators = {
        "version": 1,
        "cube": "mbw_mother",
        "defaults": {"min_denominator": 1},
        "series": ["M"],
        "measures": [
            {
                "name": "m01",
                "type": "number",
                "sql": "100.0 * {m01_numerator} / NULLIF({m01_denominator}, 0)",
                "meta": {"indicator": "M01", "unit": "%", "direction": "higher"},
            },
            {"name": "m01_numerator", "type": "count", "filters": [{"sql": "{CUBE}.was_eligible"}]},
            {"name": "m01_denominator", "type": "count"},
            {
                "name": "m02",
                "type": "number",
                "sql": "100.0 * {m02_numerator} / NULLIF({m02_denominator}, 0)",
                "meta": {"indicator": "M02", "unit": "%", "direction": "lower"},
            },
            {"name": "m02_numerator", "type": "count", "filters": [{"sql": "{CUBE}.was_far"}]},
            {"name": "m02_denominator", "type": "count"},
        ],
    }
    rows = evaluate(
        _visits_config(),
        [OPP],
        extra_fields=_lookups(),
        registry_documents=(props, indicators),
        scope="programme",
        as_of="DATE '2026-09-30'",
        connection=connection,
    )
    assert len(rows) == 1
    row = rows[0]
    # Two mothers: m1 eligible and far-moved, m2 neither.
    assert float(row["m01"]) == pytest.approx(50.0)
    assert float(row["m02"]) == pytest.approx(50.0)


def test_an_ordinary_transform_is_looked_up_in_sql(mbw_cache):
    """A float-cast transform is the engine's SQL, not Python: only an extractor or a
    whole-visit transform is refused."""
    gs = _gs_config()
    gs.fields = [F(name="gs_score", path="form.gs_score", transform=lambda x: float(x) if x else None)]
    sql = build_visit_sql(
        _visits_config(),
        [OPP],
        extra_fields={LOOKUPS_KEY: {"registration": _registrations_config(), "gs": gs}},
        props_doc=PROPS,
    )
    assert _rows(sql, "gs_score")["v1"][0] == 100
