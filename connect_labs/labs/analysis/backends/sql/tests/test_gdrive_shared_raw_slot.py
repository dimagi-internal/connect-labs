"""Pipelines over the same Google Drive target share ONE raw-cache slot.

Workflow 23765 reads one Drive folder (~115 MB of CSVs, 173k rows) through several
summary pipelines. Keyed per pipeline, each of them read the whole folder from Drive
and stored its own full raw copy -- the #1921 duplication, for Drive. These pin:

- the slot key: what decides the raw rows (target, pattern, null markers, the
  username/date columns) and nothing else -- not the pipeline, not the stamp;
- reuse: a second pipeline on the same target reads no Drive at all;
- the gate: a pipeline whose OWN stamp does not verify cannot read a slot another
  pipeline filled, however warm it is;
- refresh: a forced refresh on one pipeline re-reads Drive into the shared slot.
"""

import pytest

from connect_labs.labs.analysis.backends.sql import gdrive_fetcher as gf
from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.backends.sql.tests.test_gdrive_fetcher import (  # noqa: F401 - fixtures
    OPP,
    STAFF,
    allowed_root,
    drive,
)
from connect_labs.labs.analysis.config import (
    USER_VISITS_RAW_SLOT,
    AnalysisPipelineConfig,
    CacheStage,
    DataSourceConfig,
    FieldComputation,
    gdrive_raw_slot,
    raw_cache_slot,
)
from connect_labs.labs.analysis.pipeline import AnalysisPipeline

TARGET = {"type": "gdrive", "folder_id": "folderA", "file_pattern": "answers_scored_*.csv"}


def _stamped(pipeline_id, **overrides):
    return DataSourceConfig(**gf.authorize_gdrive_source({**TARGET, **overrides}, OPP, STAFF, pipeline_id=pipeline_id))


def _config(pipeline_id, data_source, stage=CacheStage.VISIT_LEVEL, field_name="state"):
    return AnalysisPipelineConfig(
        grouping_key="username",
        fields=[FieldComputation(name=field_name, path="row.state", aggregation="first")],
        terminal_stage=stage,
        linking_field=field_name if stage == CacheStage.ENTITY else "entity_id",
        data_source=data_source,
        pipeline_id=pipeline_id,
    )


def _run(config, **kw):
    events = list(AnalysisPipeline(access_token="tok").stream_analysis(config, OPP, **kw))
    assert events[-1][0] == "result", events[-1]
    return events


def _downloads(drive):  # noqa: F811
    return [c for c in drive.calls if c[0] == "download"]


# --------------------------------------------------------------------------- the key


def test_same_target_same_slot_whatever_the_pipeline_or_stamp():
    a = DataSourceConfig(**TARGET, authorization={"signature": "one"})
    b = DataSourceConfig(**TARGET, authorization={"signature": "two"})
    assert raw_cache_slot(1, "gdrive", a) == raw_cache_slot(2, "gdrive", b) == gdrive_raw_slot(a)


@pytest.mark.parametrize(
    "change",
    [
        {"file_pattern": "answers_scored_bed*.csv"},
        {"folder_id": "folderB"},
        {"null_values": ["", "NA"]},
        {"username_column": "session_id"},
        {"date_column": "day"},
    ],
)
def test_anything_that_changes_the_rows_changes_the_slot(change):
    assert gdrive_raw_slot(DataSourceConfig(**TARGET)) != gdrive_raw_slot(DataSourceConfig(**{**TARGET, **change}))


def test_null_values_are_a_set():
    a = DataSourceConfig(**TARGET, null_values=["NA", ""])
    b = DataSourceConfig(**TARGET, null_values=["", "NA", "NA"])
    assert gdrive_raw_slot(a) == gdrive_raw_slot(b)


def test_slot_cannot_meet_a_pipeline_id_or_the_visits_slot():
    slot = gdrive_raw_slot(DataSourceConfig(**TARGET))
    assert -(2**31) <= slot < USER_VISITS_RAW_SLOT


def test_other_sources_keep_their_slots():
    assert raw_cache_slot(7, "cchq_forms", DataSourceConfig(type="cchq_forms")) == 7
    assert raw_cache_slot(7, "connect_csv") == USER_VISITS_RAW_SLOT
    # A caller that knows only the type keeps the per-pipeline slot.
    assert raw_cache_slot(7, "gdrive") == 7


# --------------------------------------------------------------------------- reuse


@pytest.mark.django_db
def test_second_pipeline_on_the_target_reads_no_drive(drive):  # noqa: F811
    visits = _config(101, _stamped(101))
    by_state = _config(102, _stamped(102), stage=CacheStage.ENTITY, field_name="state_key")

    _run(visits)
    first = len(_downloads(drive))
    assert first == 2  # the two answers_scored_*.csv files

    events = _run(by_state)
    assert len(_downloads(drive)) == first, "the second pipeline re-read Drive"
    assert any("Reusing 3 rows" in (e[1].get("message") or "") for e in events if e[0] == "status")
    assert {r.state_key for r in events[-1][1].rows} == {"Kebbi", "Borno", "NA"}

    # One stored copy, in the shared slot -- not one per pipeline.
    slot = SQLCacheManager(OPP, visits).raw_slot_id
    assert slot == SQLCacheManager(OPP, by_state).raw_slot_id < USER_VISITS_RAW_SLOT
    assert RawVisitCache.objects.filter(opportunity_id=OPP).count() == 3
    assert set(RawVisitCache.objects.values_list("pipeline_id", flat=True)) == {slot}


@pytest.mark.django_db
def test_a_pipeline_with_its_own_target_does_not_share(drive):  # noqa: F811
    _run(_config(101, _stamped(101)))
    other = _config(103, _stamped(103, file_pattern="answers_scored_mal*.csv"))
    events = _run(other)
    assert len(events[-1][1].rows) == 1
    assert RawVisitCache.objects.filter(opportunity_id=OPP).count() == 4  # 3 + 1, two slots


# --------------------------------------------------------------------------- the gate


@pytest.mark.django_db
def test_a_warm_slot_is_no_permission_to_read_it(drive):  # noqa: F811
    _run(_config(101, _stamped(101)))
    assert RawVisitCache.objects.filter(opportunity_id=OPP).exists()

    # Same target, but the stamp was minted for pipeline 101, not 104.
    borrowed = _config(104, _stamped(101), field_name="other")
    events = list(AnalysisPipeline(access_token="tok").stream_analysis(borrowed, OPP))
    assert events[-1][0] == "error"
    assert "not authorized for this pipeline" in events[-1][1]["message"]

    unstamped = _config(105, DataSourceConfig(**TARGET), field_name="other")
    events = list(AnalysisPipeline(access_token="tok").stream_analysis(unstamped, OPP))
    assert events[-1][0] == "error" and "not been authorized" in events[-1][1]["message"]


@pytest.mark.django_db
def test_a_non_member_cannot_read_a_warm_slot(drive, monkeypatch):  # noqa: F811
    _run(_config(101, _stamped(101)))
    monkeypatch.setattr(gf, "_caller_opportunity_ids", lambda request, token: {999})
    events = list(AnalysisPipeline(access_token="tok").stream_analysis(_config(102, _stamped(102)), OPP))
    assert events[-1][0] == "error" and "not a member" in events[-1][1]["message"]


# --------------------------------------------------------------------------- refresh


@pytest.mark.django_db
def test_forced_refresh_on_one_pipeline_refreshes_the_shared_slot(drive):  # noqa: F811
    visits = _config(101, _stamped(101))
    _run(visits)
    drive.files["fileCSV2"]["content"] = b"session_id,state,quality,day\ns9,Borno,5,2026-07-01\ns10,Kano,3,x\n"

    before = len(_downloads(drive))
    _run(visits, force_refresh=True)
    assert len(_downloads(drive)) == before + 2
    assert RawVisitCache.objects.filter(opportunity_id=OPP).count() == 4

    # A sibling's cold build now reads the refreshed rows without touching Drive.
    after = len(_downloads(drive))
    events = _run(_config(102, _stamped(102), stage=CacheStage.ENTITY, field_name="state_key"))
    assert len(_downloads(drive)) == after
    assert "Kano" in {r.state_key for r in events[-1][1].rows}


@pytest.mark.django_db
def test_one_forced_page_load_reads_drive_once_for_every_pipeline(drive):  # noqa: F811
    """The runner forwards ?refresh=1 to every pipeline of the page; the folder is
    read once for all of them, not once per pipeline (the #1926 floor)."""
    from types import SimpleNamespace

    request = SimpleNamespace(GET={"refresh": "1"}, session={}, user=None)
    for pid, name in ((101, "a"), (102, "b"), (103, "c")):
        config = _config(pid, _stamped(pid), field_name=name)
        events = list(AnalysisPipeline(request=request, access_token="tok").stream_analysis(config, OPP))
        assert events[-1][0] == "result", events[-1]
    assert len(_downloads(drive)) == 2
