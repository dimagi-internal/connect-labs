"""Layer 1 must come from the pipeline, and the paraphrase must stay impossible.

The first parity run against real data was driven by a hand-written extraction
that carried 3 of 10 danger-sign paths, 3 of 6 referral paths and 3 of 4
kmc-hours paths. C19, C20 and C23 -- precisely the indicators over those fields --
were the ones that disagreed with the existing dashboard. These tests pin the
generated form so nobody re-types it.
"""

from __future__ import annotations

import pytest

from connect_labs.semantic.layer1 import MARKER_BOOLEANS, build_visit_sql

# A miniature stand-in for the engine: enough shape to exercise the rewrites,
# with a multi-path COALESCE so the "every path survives" test means something.
FAKE_EXTRACTION = {
    "visit_extraction_sql": (
        "SELECT\n"
        "visit_id,\n"
        "username,\n"
        "visit_date,\n"
        "COALESCE(a->>'p1', a->>'p2', a->>'p3') as danger_visits,\n"
        "COALESCE(a->>'r1', a->>'r2') as referral_visits,\n"
        "COALESCE(a->>'k1') as kmc_hours_mean,\n"
        "COALESCE(a->>'c1') as death_visits,\n"
        "COALESCE(a->>'s1') as self_referral_visits,\n"
        "COALESCE(a->>'e1') as ebf_visits,\n"
        "COALESCE(a->>'f1') as form_names\n"
        "FROM labs_raw_visit_cache AS labs_raw_visit_cache\n"
        "WHERE opportunity_id = 10042 AND pipeline_id = 5108\n"
        "ORDER BY visit_id"
    )
}


def _gen(schema, opportunity_id):
    return FAKE_EXTRACTION


def test_every_extraction_path_survives():
    """The whole point: nothing in the pipeline's expressions is dropped."""
    sql = build_visit_sql({}, [10042, 10016], generate_sql_preview=_gen)
    for path in ("'p1'", "'p2'", "'p3'", "'r1'", "'r2'", "'k1'"):
        assert path in sql, f"path {path} was lost in the rewrite"


def test_widens_to_every_requested_opportunity():
    sql = build_visit_sql({}, [10042, 10016, 10014], generate_sql_preview=_gen)
    assert "opportunity_id IN (10042,10016,10014)" in sql
    assert "opportunity_id = 10042 AND pipeline_id" not in sql


def test_dedupes_across_cache_partitions():
    """labs_raw_visit_cache is keyed by (opportunity, pipeline).

    The same visit is present once per pipeline that cached it; without the
    DISTINCT ON, opp 10042's rows were counted from two partitions and every
    denominator inflated.
    """
    sql = build_visit_sql({}, [10042], generate_sql_preview=_gen)
    assert "DISTINCT ON (opportunity_id, visit_id)" in sql
    # The freshest copy wins, not the lowest pipeline id -- see the freshness test below.
    assert "ORDER BY opportunity_id, visit_id, expires_at DESC, pipeline_id" in sql


def test_selects_opportunity_id_which_the_extraction_omits():
    sql = build_visit_sql({}, [10042], generate_sql_preview=_gen)
    assert "opportunity_id,\npipeline_id,\nvisit_id," in sql


def test_marker_booleans_use_the_pipelines_own_word_test():
    sql = build_visit_sql({}, [10042], generate_sql_preview=_gen)
    for name, (col, word) in MARKER_BOOLEANS.items():
        assert f"(x.{col} ~* '\\y{word}\\y') AS {name}" in sql


def test_no_opportunities_is_an_error():
    with pytest.raises(ValueError, match="at least one opportunity"):
        build_visit_sql({}, [], generate_sql_preview=_gen)


# ---------------------------------------------------------------------------
# A pipeline's row filters survive the rewrite.
#
# Layer 1 widened the extraction by cutting its WHERE at the pipeline-scope
# predicate and writing its own -- so every row filter the pipeline declared
# (`filters: {status: [...]}`, flagged, dates) was silently dropped for the
# metrics while still applying to the pipeline's own rows. Neal's compute spec
# rule 0 -- only approved and over_limit visits are valid -- could therefore be
# declared on the pipeline and have no effect on a single indicator.
# ---------------------------------------------------------------------------

FILTERED_EXTRACTION = {
    **FAKE_EXTRACTION,
    "visit_extraction_sql": FAKE_EXTRACTION["visit_extraction_sql"].replace(
        "WHERE opportunity_id = 10042 AND pipeline_id = 5108\n",
        "WHERE opportunity_id = 10042 AND pipeline_id = 5108 AND status IN ('approved', 'over_limit')\n",
    ),
    "visit_filter_predicates": ["status IN ('approved', 'over_limit')"],
}


def test_declared_row_filters_are_reapplied_to_the_widened_where():
    sql = build_visit_sql({}, [10042, 10016], generate_sql_preview=lambda s, o: FILTERED_EXTRACTION)
    assert "WHERE opportunity_id IN (10042,10016) AND visit_count > 0 AND status IN ('approved', 'over_limit')" in sql


def test_no_declared_filters_leaves_the_rewrite_unchanged():
    # Every KMC pipeline before this change declared none; their SQL must not move.
    sql = build_visit_sql({}, [10042, 10016], generate_sql_preview=_gen)
    assert "WHERE opportunity_id IN (10042,10016) AND visit_count > 0\nORDER BY" in sql


@pytest.mark.django_db
def test_rejected_and_pending_visits_never_reach_the_metrics():
    """Execute Layer 1's real SQL, generated by the real engine, over mixed statuses."""
    from django.db import connection
    from django.utils import timezone

    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
    from connect_labs.labs.analysis.config import AnalysisPipelineConfig, FieldComputation

    opp, pipeline = 976543, 88
    future = timezone.now() + timezone.timedelta(days=1)
    for i, status in enumerate(["approved", "over_limit", "rejected", "pending"]):
        RawVisitCache.objects.create(
            opportunity_id=opp,
            pipeline_id=pipeline,
            visit_count=4,
            expires_at=future,
            visit_id=str(70000 + i),
            username="flw",
            status=status,
            form_json={"form": {"@name": "Record Visit Details"}},
            visit_date="2026-09-01",
        )
    # Every column Layer 1's marker booleans read, so the SQL is the shape a KMC
    # pipeline produces -- values are irrelevant, only which rows survive.
    marker_cols = sorted({col for col, _word in MARKER_BOOLEANS.values()} | {"ebf_visits", "form_names"})
    config = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[FieldComputation(name=c, path="form.@name") for c in marker_cols],
        filters={"status": ["approved", "over_limit"]},
    )
    config.pipeline_id = pipeline
    sql = build_visit_sql(config, [opp])
    with connection.cursor() as cur:
        cur.execute(f"SELECT visit_id FROM ({sql}) q ORDER BY visit_id")
        got = [r[0] for r in cur.fetchall()]
    assert got == ["70000", "70001"], "only approved and over_limit visits are valid data"


@pytest.mark.django_db
def test_the_freshest_copy_of_a_visit_wins_and_half_written_copies_are_ignored():
    """One visit cached by three pipelines: an OLD copy under the lowest pipeline id
    (another workflow's, with a stale status), a FRESH copy, and an in-progress
    sentinel copy (negative visit_count). Layer 1 must read the fresh one."""
    from datetime import timedelta

    from django.db import connection
    from django.utils import timezone

    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
    from connect_labs.labs.analysis.config import AnalysisPipelineConfig, FieldComputation

    opp, now = 976544, timezone.now()
    rows = [
        # (pipeline_id, visit_count, expires_at, status) -- the same visit_id throughout
        (5, 1, now + timedelta(minutes=1), "pending"),  # old copy, lowest id
        (99, 1, now + timedelta(minutes=55), "approved"),  # fresh copy
        (3, -7, now + timedelta(minutes=58), "rejected"),  # in-progress sentinel
    ]
    for pid, count, expires, status in rows:
        RawVisitCache.objects.create(
            opportunity_id=opp,
            pipeline_id=pid,
            visit_count=count,
            expires_at=expires,
            visit_id="80000",
            username="flw",
            status=status,
            form_json={"form": {"@name": "Record Visit Details"}},
            visit_date="2026-09-01",
        )
    marker_cols = sorted({col for col, _word in MARKER_BOOLEANS.values()} | {"ebf_visits", "form_names"})
    config = AnalysisPipelineConfig(
        grouping_key="username", fields=[FieldComputation(name=c, path="form.@name") for c in marker_cols]
    )
    config.pipeline_id = 99
    sql = build_visit_sql(config, [opp])
    with connection.cursor() as cur:
        cur.execute(f"SELECT pipeline_id FROM ({sql}) q")
        got = [r[0] for r in cur.fetchall()]
    assert got == [99], "the most recently fetched finalized copy, not the lowest id or a half-written one"


def test_a_worker_filter_is_applied_in_the_scan_and_a_computed_key_is_not():
    sql = build_visit_sql(
        {},
        [10042, 10016],
        generate_sql_preview=_gen,
        visit_filter={"opportunity_id": 10042, "username": "o'brien", "baby_case_id": "B1"},
    )
    where = sql[sql.index("WHERE opportunity_id IN") : sql.index("ORDER BY")]
    assert "opportunity_id = 10042" in where and "username = 'o''brien'" in where, "escaped, in the scan"
    assert "baby_case_id" not in where, "a computed key is not a column of the raw cache; the compiler applies it"


@pytest.mark.django_db
def test_one_workers_evaluation_scans_only_that_workers_visits():
    """The worker review's case table is the case scope for ONE worker.

    The filter used to be applied after Layer 1's DISTINCT ON, which Postgres cannot
    push a `username` predicate below -- so every visit of the opportunity was
    extracted and de-duplicated to keep one worker's rows, and a real worker's case
    table ran past a minute. The plan must show the predicate AT the scan of the
    raw cache, and the rows must be exactly that worker's.
    """
    import json

    from django.db import connection
    from django.utils import timezone

    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
    from connect_labs.labs.analysis.config import AnalysisPipelineConfig, FieldComputation

    opp, future = 976545, timezone.now() + timezone.timedelta(days=1)
    for i in range(6):
        for pid in (1, 2):  # two cached copies of every visit
            RawVisitCache.objects.create(
                opportunity_id=opp,
                pipeline_id=pid,
                visit_count=6,
                expires_at=future,
                visit_id=str(90000 + i),
                username="flw_a" if i < 2 else "flw_b",
                status="approved",
                form_json={"form": {"@name": "Record Visit Details"}},
                visit_date="2026-09-01",
            )
    marker_cols = sorted({col for col, _word in MARKER_BOOLEANS.values()} | {"ebf_visits", "form_names"})
    config = AnalysisPipelineConfig(
        grouping_key="username", fields=[FieldComputation(name=c, path="form.@name") for c in marker_cols]
    )
    config.pipeline_id = 1
    sql = build_visit_sql(config, [opp], visit_filter={"opportunity_id": opp, "username": "flw_a"})
    with connection.cursor() as cur:
        cur.execute(f"SELECT visit_id, username FROM ({sql}) q ORDER BY visit_id")
        assert cur.fetchall() == [("90000", "flw_a"), ("90001", "flw_a")]
        cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
        plan = cur.fetchone()[0]
        plan = json.loads(plan) if isinstance(plan, str) else plan

    def scans(node):
        if node.get("Relation Name") == "labs_raw_visit_cache":
            yield node
        for child in node.get("Plans", []):
            yield from scans(child)

    found = list(scans(plan[0]["Plan"]))
    assert found, "the raw cache is scanned"
    for node in found:
        conds = " ".join(str(node.get(k, "")) for k in ("Filter", "Index Cond", "Recheck Cond"))
        assert "flw_a" in conds, f"the worker predicate must be applied at the scan, got: {conds}"
