"""A cold load of a program's Drive-backed summary pipelines (workflow 23891).

Measured 2026-10-04: six entity-stage summary pipelines over ONE 173,488-row Drive
folder took 16 minutes cold. The folder was read once (the shared raw slot), but
each entity pipeline then stored its own full per-visit copy (1m15s-2m29s) before a
17-33s aggregation, and the whole thing expired an hour later. These pin the two
fixes:

- an entity pipeline aggregates in ONE pass from the raw slot -- same rows as the
  old copy-then-aggregate path, filters and filtered `first` fields included -- and
  writes no per-visit copy unless a JOIN reads it;
- a Drive cache stays valid while the files are unchanged (one listing per read),
  and is rebuilt when they change -- with every access gate still run on every read.
"""

import pytest
from django.core.cache import cache
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql import gdrive_fetcher as gf
from connect_labs.labs.analysis.backends.sql import gdrive_freshness as freshness
from connect_labs.labs.analysis.backends.sql.models import ComputedEntityCache, ComputedVisitCache, RawVisitCache
from connect_labs.labs.analysis.backends.sql.tests.test_gdrive_fetcher import (  # noqa: F401 - fixtures
    OPP,
    STAFF,
    allowed_root,
    drive,
)
from connect_labs.labs.analysis.config import (
    AnalysisPipelineConfig,
    CacheStage,
    DataSourceConfig,
    FieldComputation,
    JoinConfig,
)
from connect_labs.labs.analysis.pipeline import AnalysisPipeline
from connect_labs.labs.analysis.utils import resolve_join_hashes

ANSWERS = (
    b"qid,state,classification_basis,typology_id,typology_name,score\n"
    b"4.12,Kebbi,answered_clean,T1,Workload,3\n"
    b"4.12,Kebbi,answered_clean,T2,System & Resource Gaps,4\n"
    b"4.12,Kebbi,answered_clean,T1,Workload,5\n"
    b"4.12,Kebbi,unclear,T3,Training,1\n"
    b"4.12,Borno,answered_clean,T2,System & Resource Gaps,2\n"
    b"4.12,Borno,answered_clean,T4,Payment,4\n"
    b"5.01,Kebbi,answered_clean,T3,Training,3\n"
    b"5.01,Borno,unclear,T1,Workload,\n"
)
SOURCE = {"type": "gdrive", "folder_id": "folderA", "file_pattern": "typology_classifications_*.csv"}


@pytest.fixture(autouse=True)
def _clean_records():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def answers(drive):  # noqa: F811
    drive.files["typ1"] = {
        "name": "typology_classifications_a.csv",
        "mimeType": "text/csv",
        "parent": "folderA",
        "content": ANSWERS,
    }
    return drive


def _to_float(x):
    return float(x) if x else None


def _fields():
    return [
        FieldComputation(name="qid", path="row.qid", aggregation="first"),
        FieldComputation(name="answers", path="row.typology_id", aggregation="count"),
        FieldComputation(name="mean_score", path="row.score", aggregation="avg", transform=_to_float),
        FieldComputation(name="states", path="row.state", aggregation="count_unique"),
        FieldComputation(
            name="workload_name",
            path="row.typology_name",
            aggregation="first",
            filter_path="row.typology_id",
            filter_value="T1",
        ),
        FieldComputation(
            name="payment_name",
            path="row.typology_name",
            aggregation="first",
            filter_path="row.typology_id",
            filter_value="T4",
        ),
    ]


def _config(pipeline_id, *, filters=None, stage=CacheStage.ENTITY, fields=None, joins=None):
    stamped = gf.authorize_gdrive_source(SOURCE, OPP, STAFF, pipeline_id=pipeline_id)
    return AnalysisPipelineConfig(
        grouping_key="username",
        fields=fields or _fields(),
        terminal_stage=stage,
        linking_field="qid" if stage == CacheStage.ENTITY else "entity_id",
        filters=filters or {},
        data_source=DataSourceConfig(**stamped),
        pipeline_id=pipeline_id,
        joins=joins or [],
    )


def _run(config, **kw):
    events = list(AnalysisPipeline(access_token="tok").stream_analysis(config, OPP, **kw))
    assert events[-1][0] == "result", events[-1]
    return events


def _snapshot(result):
    return sorted(
        (
            r.entity_id,
            r.total_visits,
            tuple(sorted((k, str(v)) for k, v in r.custom_fields.items())),
            str(r.first_visit_date),
            str(r.last_visit_date),
        )
        for r in result.rows
    )


def _downloads(fake):
    return [c for c in fake.calls if c[0] == "download"]


def _next_page_load(config):
    """A later page load: the per-minute memo of the folder listing has lapsed."""
    cache.delete(freshness._live_key(config.data_source))


# --------------------------------------------------------------------------- fix 1: one pass


FILTER_CASES = [
    {},
    {"state": ["Kebbi"]},
    {"basis": ["answered_clean"], "state": ["Borno"]},
]


@pytest.mark.django_db
@pytest.mark.parametrize("filters", FILTER_CASES)
def test_entity_one_pass_matches_the_copy_then_aggregate_path(answers, filters):
    """The old path (materialise per-visit rows, then aggregate) and the new one
    (aggregate straight from the raw slot) give identical entity rows."""
    fields = _fields() + [
        FieldComputation(name="state", path="row.state", aggregation="first"),
        FieldComputation(name="basis", path="row.classification_basis", aggregation="first"),
    ]

    old = _config(401, filters=filters, fields=fields)
    old.feeds_joins = True  # exactly the pre-change path: it still writes the copy
    old_result = _run(old)[-1][1]
    assert ComputedVisitCache.objects.filter(pipeline_id=401).exists()

    new = _config(402, filters=filters, fields=fields)
    new_result = _run(new)[-1][1]

    assert old_result.rows, "the comparison must compare something"
    assert _snapshot(new_result) == _snapshot(old_result)
    assert new_result.metadata["total_visits"] == old_result.metadata["total_visits"]
    # ... and the cached read-back agrees too.
    assert _snapshot(_run(new)[-1][1]) == _snapshot(old_result)


@pytest.mark.django_db
def test_entity_pipeline_writes_no_per_visit_copy(answers):
    fields = _fields() + [FieldComputation(name="state", path="row.state", aggregation="first")]
    by_qid = _config(403, filters={"state": ["Kebbi"]}, fields=fields)
    rows = {r.entity_id: r for r in _run(by_qid)[-1][1].rows}
    assert rows["4.12"].total_visits == 4
    assert rows["4.12"].custom_fields["workload_name"] == "Workload"
    assert rows["4.12"].custom_fields["payment_name"] is None  # T4 is Borno-only
    assert not ComputedVisitCache.objects.filter(pipeline_id=403).exists()
    assert ComputedEntityCache.objects.filter(pipeline_id=403).exists()


@pytest.mark.django_db
def test_visit_and_flw_pipelines_still_write_their_per_visit_rows(answers):
    """Their computed visits are read (coverage, MBW, the visit-level result cache)."""
    _run(_config(404, stage=CacheStage.VISIT_LEVEL))
    _run(_config(405, stage=CacheStage.AGGREGATED))
    assert ComputedVisitCache.objects.filter(pipeline_id=404).count() == 8
    assert ComputedVisitCache.objects.filter(pipeline_id=405).count() == 8


@pytest.mark.django_db
def test_an_entity_pipeline_a_join_reads_keeps_its_per_visit_rows(answers):
    target = _config(406)
    reader = _config(
        407,
        stage=CacheStage.VISIT_LEVEL,
        fields=[FieldComputation(name="qid", path="row.qid")],
        joins=[
            JoinConfig(
                from_alias="summary",
                local_key="row.qid",
                remote_key_field="qid",
                fields=[{"name": "n", "from": "answers"}],
            )
        ],
    )
    resolve_join_hashes({"summary": target, "reader": reader})
    assert target.feeds_joins and not reader.feeds_joins

    _run(target)
    assert ComputedVisitCache.objects.filter(pipeline_id=406).count() == 8


@pytest.mark.django_db
def test_a_cached_join_target_without_per_visit_rows_is_rebuilt(answers):
    """Cached before it became a JOIN target, so it has no per-visit rows to read."""
    first = _config(408)
    _run(first)
    assert not ComputedVisitCache.objects.filter(pipeline_id=408).exists()

    now_joined = _config(408)
    now_joined.feeds_joins = True
    events = _run(now_joined)
    assert not any("cache HIT" in (e[1].get("message") or "") for e in events if e[0] == "status")
    assert ComputedVisitCache.objects.filter(pipeline_id=408).count() == 8


# --------------------------------------------------------------------------- fix 2: the files decide


@pytest.mark.django_db
def test_unchanged_files_serve_the_cache_past_the_hour(answers, settings):
    settings.PIPELINE_CACHE_TTL_HOURS = 1
    config = _config(410)
    _run(config)
    downloads = len(_downloads(answers))

    # Written to outlive the hourly TTL: the files, not the clock, invalidate it.
    horizon = timezone.now() + freshness.max_age() - timezone.timedelta(minutes=5)
    assert RawVisitCache.objects.filter(opportunity_id=OPP).exclude(expires_at__gt=horizon).count() == 0
    assert ComputedEntityCache.objects.filter(pipeline_id=410).exclude(expires_at__gt=horizon).count() == 0

    _next_page_load(config)
    listings = len([c for c in answers.calls if c[0] == "list"])
    events = _run(config)
    assert any("cache HIT" in (e[1].get("message") or "") for e in events if e[0] == "status")
    assert len(_downloads(answers)) == downloads, "an unchanged folder was re-read"
    assert len([c for c in answers.calls if c[0] == "list"]) == listings + 1, "one listing decides"


@pytest.mark.django_db
def test_changed_files_rebuild_and_siblings_reuse_the_new_read(answers):
    by_qid = _config(411)
    by_state = _config(
        412,
        filters={"state": ["Borno"]},
        fields=_fields() + [FieldComputation(name="state", path="row.state", aggregation="first")],
    )
    _run(by_qid)
    _run(by_state)
    downloads = len(_downloads(answers))

    answers.files["typ1"]["content"] = ANSWERS + b"6.01,Kano,answered_clean,T2,System & Resource Gaps,5\n"
    _next_page_load(by_qid)

    rows = {r.entity_id for r in _run(by_qid)[-1][1].rows}
    assert "6.01" in rows
    assert len(_downloads(answers)) == downloads + 1

    # The sibling's cached result predates the change: rebuilt, from the slot the
    # first pipeline just refilled -- no second Drive read.
    events = _run(by_state)
    assert any("rebuilding" in (e[1].get("message") or "") for e in events if e[0] == "status")
    assert any("Reusing" in (e[1].get("message") or "") for e in events if e[0] == "status")
    assert len(_downloads(answers)) == downloads + 1


@pytest.mark.django_db
def test_a_cache_with_no_recorded_fingerprint_is_rebuilt(answers):
    """E.g. written before this existed, or its record was evicted."""
    config = _config(413)
    _run(config)
    downloads = len(_downloads(answers))
    cache.clear()

    _run(config)
    assert len(_downloads(answers)) == downloads + 1


@pytest.mark.django_db
def test_drive_listing_failure_serves_the_cache(answers, monkeypatch):
    config = _config(414)
    _run(config)
    _next_page_load(config)

    def boom(data_source):
        raise gf.GDriveSourceError("Drive is down")

    monkeypatch.setattr(gf, "source_fingerprint", boom)
    events = _run(config)
    assert any("cache HIT" in (e[1].get("message") or "") for e in events if e[0] == "status")


@pytest.mark.django_db
def test_gates_still_run_on_a_fresh_cached_read(answers, monkeypatch):
    config = _config(415)
    _run(config)

    monkeypatch.setattr(gf, "_caller_opportunity_ids", lambda request, token: {999})
    events = list(AnalysisPipeline(access_token="tok").stream_analysis(config, OPP))
    assert events[-1][0] == "error" and "not a member" in events[-1][1]["message"]

    borrowed = _config(416)
    borrowed.data_source = config.data_source  # pipeline 415's stamp
    monkeypatch.setattr(gf, "_caller_opportunity_ids", lambda request, token: {OPP})
    events = list(AnalysisPipeline(access_token="tok").stream_analysis(borrowed, OPP))
    assert events[-1][0] == "error" and "not authorized for this pipeline" in events[-1][1]["message"]


@pytest.mark.django_db
def test_query_rows_treats_a_stale_drive_cache_as_cold(answers):
    from connect_labs.workflow.pipeline_query import cached_queryset

    config = _config(417)
    _run(config)
    assert cached_queryset(config, OPP) is not None

    answers.files["typ1"]["content"] = ANSWERS + b"7.01,Kano,unclear,T1,Workload,1\n"
    _next_page_load(config)
    assert cached_queryset(config, OPP) is None


def test_fingerprint_follows_content_not_order():
    a = {"id": "1", "name": "a.csv", "size": "10", "md5Checksum": "x", "modifiedTime": "t1"}
    b = {"id": "2", "name": "b.csv", "size": "20", "md5Checksum": "y", "modifiedTime": "t1"}
    base = gf.fingerprint_metas([a, b])
    assert gf.fingerprint_metas([b, a]) == base
    for change in ({"md5Checksum": "z"}, {"modifiedTime": "t2"}, {"size": "11"}, {"name": "c.csv"}):
        assert gf.fingerprint_metas([{**a, **change}, b]) != base
    assert gf.fingerprint_metas([a]) != base  # a file removed
