"""Layer 1 must come from the pipeline, and the paraphrase must stay impossible.

The first parity run against real data was driven by a hand-written extraction
that carried 3 of 10 danger-sign paths, 3 of 6 referral paths and 3 of 4
kmc-hours paths. C19, C20 and C23 -- precisely the indicators over those fields --
were the ones that disagreed with the existing dashboard. These tests pin the
generated form so nobody re-types it.
"""

from __future__ import annotations

import pytest

from connect_labs.semantic.layer1 import build_visit_sql
from connect_labs.semantic.runtime import load_registry

# Layer 1's derived columns are the REGISTRY's (`visit_columns`); these tests run
# the shipped KMC registry's, which carry the marker booleans.
KMC_PROPS = load_registry("kmc")[0]
WORD_MATCHES = {
    c["name"]: (c["word_match"]["column"], c["word_match"]["word"])
    for c in KMC_PROPS["visit_columns"]
    if "word_match" in c
}
# Every Layer-1 column the KMC visit columns read.
KMC_VISIT_INPUTS = sorted({col for col, _word in WORD_MATCHES.values()} | {"ebf_visits", "form_names"})


# A real pipeline config -- the engine builds Layer 1's extraction from it, so the
# tests read the SQL the engine actually emits, not a stand-in for it. Multi-path
# fields make the "every path survives" test mean something.
def _kmc_like_config(filters=None):
    from connect_labs.labs.analysis.config import AnalysisPipelineConfig, FieldComputation

    paths = {
        "danger_visits": ["form.p1", "form.p2", "form.p3"],
        "referral_visits": ["form.r1", "form.r2"],
        "kmc_hours_mean": ["form.k1"],
    }
    fields = [FieldComputation(name=n, paths=p) for n, p in paths.items()]
    fields += [FieldComputation(name=c, path="form.@name") for c in KMC_VISIT_INPUTS if c not in paths]
    config = AnalysisPipelineConfig(grouping_key="username", fields=fields, filters=filters or {})
    config.pipeline_id = 5108
    return config


def _where(sql):
    return sql[sql.index("WHERE opportunity_id IN") : sql.index("ORDER BY opportunity_id")]


def test_every_extraction_path_survives():
    """The whole point: nothing in the pipeline's expressions is dropped."""
    sql = build_visit_sql(_kmc_like_config(), [10042, 10016], props_doc=KMC_PROPS)
    for path in ("'p1'", "'p2'", "'p3'", "'r1'", "'r2'", "'k1'"):
        assert path in sql, f"path {path} was lost"


def test_reads_every_requested_opportunity_in_its_own_slot():
    from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT

    where = _where(build_visit_sql(_kmc_like_config(), [10042, 10016, 10014], props_doc=KMC_PROPS))
    assert "opportunity_id IN (10042,10016,10014)" in where
    assert f"pipeline_id = {USER_VISITS_RAW_SLOT}" in where, "the visits slot, never every slot"
    assert "visit_count > 0" in where, "a half-written download is never read (#1684)"


def test_dedupes_to_the_freshest_copy():
    sql = build_visit_sql(_kmc_like_config(), [10042], props_doc=KMC_PROPS)
    assert "DISTINCT ON (opportunity_id, visit_id)" in sql
    assert "ORDER BY opportunity_id, visit_id, expires_at DESC, pipeline_id" in sql


def test_selects_opportunity_id():
    sql = build_visit_sql(_kmc_like_config(), [10042], props_doc=KMC_PROPS)
    assert "SELECT DISTINCT ON (opportunity_id, visit_id)\n    opportunity_id,\n    pipeline_id," in sql


def test_marker_booleans_use_the_pipelines_own_word_test():
    sql = build_visit_sql(_kmc_like_config(), [10042], props_doc=KMC_PROPS)
    for name, (col, word) in WORD_MATCHES.items():
        assert f"(x.{col} ~* '\\y{word}\\y') AS {name}" in sql


def test_no_opportunities_is_an_error():
    with pytest.raises(ValueError, match="at least one opportunity"):
        build_visit_sql(_kmc_like_config(), [], props_doc=KMC_PROPS)


# ---------------------------------------------------------------------------
# A pipeline's row filters reach Layer 1.
#
# Layer 1 used to widen the extraction by cutting its WHERE at the pipeline-scope
# predicate and writing its own -- so every row filter the pipeline declared
# (`filters: {status: [...]}`, flagged, dates) was silently dropped for the
# metrics while still applying to the pipeline's own rows. The engine now builds
# the multi-opportunity extraction itself, filters included.
# ---------------------------------------------------------------------------


def test_declared_row_filters_apply():
    where = _where(
        build_visit_sql(
            _kmc_like_config(filters={"status": ["approved", "over_limit"]}), [10042, 10016], props_doc=KMC_PROPS
        )
    )
    assert "status IN ('approved', 'over_limit')" in where


def test_no_declared_filters_adds_none():
    assert "status IN" not in _where(build_visit_sql(_kmc_like_config(), [10042, 10016], props_doc=KMC_PROPS))


def test_a_worker_filter_is_applied_in_the_scan_and_a_computed_key_is_not():
    where = _where(
        build_visit_sql(
            _kmc_like_config(),
            [10042, 10016],
            visit_filter={"opportunity_id": 10042, "username": "o'brien", "baby_case_id": "B1"},
            props_doc=KMC_PROPS,
        )
    )
    assert "opportunity_id = 10042" in where and "username = 'o''brien'" in where, "escaped, in the scan"
    assert "baby_case_id" not in where, "a computed key is not a column of the raw cache; the compiler applies it"


@pytest.mark.django_db
def test_rejected_and_pending_visits_never_reach_the_metrics():
    """Execute Layer 1's real SQL, generated by the real engine, over mixed statuses."""
    from django.db import connection
    from django.utils import timezone

    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
    from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT, AnalysisPipelineConfig, FieldComputation

    opp, pipeline = 976543, 88
    future = timezone.now() + timezone.timedelta(days=1)
    for i, status in enumerate(["approved", "over_limit", "rejected", "pending"]):
        RawVisitCache.objects.create(
            opportunity_id=opp,
            pipeline_id=USER_VISITS_RAW_SLOT,  # the visits export's shared slot (#1921)
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
    marker_cols = KMC_VISIT_INPUTS
    config = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[FieldComputation(name=c, path="form.@name") for c in marker_cols],
        filters={"status": ["approved", "over_limit"]},
    )
    config.pipeline_id = pipeline
    sql = build_visit_sql(config, [opp], props_doc=KMC_PROPS)
    with connection.cursor() as cur:
        cur.execute(f"SELECT visit_id FROM ({sql}) q ORDER BY visit_id")
        got = [r[0] for r in cur.fetchall()]
    assert got == ["70000", "70001"], "only approved and over_limit visits are valid data"


@pytest.mark.django_db
def test_the_freshest_copy_of_a_visit_wins_and_half_written_copies_are_ignored():
    """One visit cached three times in the visits slot: an OLD copy (a stale status),
    a FRESH copy, and an in-progress sentinel copy (negative visit_count). Layer 1
    must read the fresh one.

    (Before #1921 these were three pipelines' slots; since then every visits pipeline
    shares one slot per opportunity, and the same three copies arise inside it -- a
    stale generation not yet replaced, and a rebuild in flight.)"""
    from datetime import timedelta

    from django.db import connection
    from django.utils import timezone

    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
    from connect_labs.labs.analysis.config import AnalysisPipelineConfig, FieldComputation

    opp, now = 976544, timezone.now()
    from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT as SLOT

    rows = [
        # (pipeline_id, visit_count, expires_at, status) -- the same visit_id throughout
        (SLOT, 1, now + timedelta(minutes=1), "pending"),  # old copy
        (SLOT, 2, now + timedelta(minutes=55), "approved"),  # fresh copy (a later generation)
        (SLOT, -7, now + timedelta(minutes=58), "rejected"),  # in-progress sentinel
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
    marker_cols = KMC_VISIT_INPUTS
    config = AnalysisPipelineConfig(
        grouping_key="username", fields=[FieldComputation(name=c, path="form.@name") for c in marker_cols]
    )
    config.pipeline_id = 99
    sql = build_visit_sql(config, [opp], props_doc=KMC_PROPS)
    with connection.cursor() as cur:
        cur.execute(f"SELECT status FROM ({sql}) q")
        got = [r[0] for r in cur.fetchall()]
    assert got == ["approved"], "the most recently fetched finalized copy, not a stale or half-written one"


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
    from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT, AnalysisPipelineConfig, FieldComputation

    opp, future = 976545, timezone.now() + timezone.timedelta(days=1)
    for i in range(6):
        for pid in (USER_VISITS_RAW_SLOT, 2):  # the visit, plus another source's row in its own slot
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
    marker_cols = KMC_VISIT_INPUTS
    config = AnalysisPipelineConfig(
        grouping_key="username", fields=[FieldComputation(name=c, path="form.@name") for c in marker_cols]
    )
    config.pipeline_id = 1
    sql = build_visit_sql(
        config, [opp], visit_filter={"opportunity_id": opp, "username": "flw_a"}, props_doc=KMC_PROPS
    )
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


@pytest.mark.django_db
def test_another_sources_rows_on_the_opportunity_never_arrive_as_visits():
    """The raw cache holds, per opportunity, the visits slot AND a slot per pipeline on
    any other source (#116, #1921) -- MBW caches its CommCare HQ Register Mother and
    Gold Standard forms beside its visits. Widening the extraction's WHERE to a set of
    opportunities dropped its slot scope, so those forms reached Layer 1 as visits."""
    from django.db import connection
    from django.utils import timezone

    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
    from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT, AnalysisPipelineConfig, FieldComputation

    opp, future = 976546, timezone.now() + timezone.timedelta(days=1)
    for slot, visit_id in ((USER_VISITS_RAW_SLOT, "91000"), (5501, "hq-registration-form"), (5502, "hq-gs-form")):
        RawVisitCache.objects.create(
            opportunity_id=opp,
            pipeline_id=slot,
            visit_count=1,
            expires_at=future,
            visit_id=visit_id,
            username="flw",
            status="approved",
            form_json={"form": {"@name": "Record Visit Details"}},
            visit_date="2026-09-01",
        )
    config = AnalysisPipelineConfig(
        grouping_key="username", fields=[FieldComputation(name=c, path="form.@name") for c in KMC_VISIT_INPUTS]
    )
    config.pipeline_id = 22143
    sql = build_visit_sql(config, [opp], props_doc=KMC_PROPS)
    with connection.cursor() as cur:
        cur.execute(f"SELECT visit_id FROM ({sql}) q")
        assert [r[0] for r in cur.fetchall()] == ["91000"]
