"""Every pipeline reading an opportunity's user_visits export shares ONE raw slot (#1921).

The raw cache used to be keyed (opportunity, pipeline). The visits filler always
walks the same endpoint -- ``/export/opportunity/<id>/user_visits/`` -- so every
visits pipeline on an opportunity wrote a byte-identical copy, and every NEW
pipeline paid a full walk of the export into its own cold slot. On 2026-09-18 opp
2154 held five full ~55k-row copies written inside three hours, and the walks
pinned a web task's CPU and OOM-killed a worker. Neither single-flight (#1551) nor
the delta top-up (#1561) could help: both were keyed on the same cold slot.

#116 is why the key was per-pipeline, and it still holds for DIFFERENT sources: a
CommCare-forms or Connect-export pipeline writes rows that are not visits, and must
not clobber (or be clobbered by) the visits slot. So only the user_visits export
shares; every other source keeps its own slot. Both halves are pinned below.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.backend import SQLBackend
from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.config import (
    AnalysisPipelineConfig,
    CacheStage,
    DataSourceConfig,
    FieldComputation,
)

pytestmark = pytest.mark.django_db

OPP = 2154
VISITS_PIPELINE_A = 20911
VISITS_PIPELINE_B = 20931
FORMS_PIPELINE = 12989


def _records(n, first_id=1000):
    return [
        {"id": first_id + i, "username": f"flw{i % 3}", "visit_date": "2026-09-17", "status": "approved"}
        for i in range(n)
    ]


def _counting_client(pages_per_walk):
    """A get_export_client stand-in whose paginate() replays `pages_per_walk` on every call."""
    client = MagicMock()
    client.paginate.side_effect = lambda *a, **k: iter(pages_per_walk)
    client.__enter__ = lambda s: client
    client.__exit__ = lambda s, *a: False
    return client


def _visits_config(pipeline_id):
    cfg = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[FieldComputation(name="n", path="form.x", aggregation="count")],
        terminal_stage=CacheStage.AGGREGATED,
    )
    cfg.pipeline_id = pipeline_id
    return cfg


def _forms_config(pipeline_id):
    cfg = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[FieldComputation(name="n", path="form.x", aggregation="count")],
        terminal_stage=CacheStage.AGGREGATED,
        data_source=DataSourceConfig(type="cchq_forms", form_name="Register Mother", app_id="app"),
    )
    cfg.pipeline_id = pipeline_id
    return cfg


# --- Cost: the point of the change ------------------------------------------------


def test_two_visits_pipelines_on_one_opportunity_walk_the_export_once():
    """The falsifier for #1921. The second pipeline must be a cache HIT on the rows the
    first one fetched, not a second full pagination into a cold slot of its own."""
    client = _counting_client([_records(50)])
    backend = SQLBackend()
    with patch("connect_labs.labs.integrations.connect.factory.get_export_client", return_value=client):
        first = list(backend.stream_raw_visits(OPP, "t", expected_visit_count=50, pipeline_id=VISITS_PIPELINE_A))
        second = list(backend.stream_raw_visits(OPP, "t", expected_visit_count=50, pipeline_id=VISITS_PIPELINE_B))

    assert client.paginate.call_count == 1
    assert first[-1] == ("complete", 50)
    assert second == [("cached", 50)]


def test_a_list_fetch_and_a_pipeline_stream_share_the_walk_too():
    """``fetch_raw_visits`` (audit, the visit-cache warm-up, image readers) walks the same
    export, so it reads and fills the same slot as the pipeline stream."""
    client = _counting_client([_records(20)])
    backend = SQLBackend()
    with patch("connect_labs.labs.integrations.connect.factory.get_export_client", return_value=client):
        rows = backend.fetch_raw_visits(OPP, "t", expected_visit_count=20, pipeline_id=VISITS_PIPELINE_A)
        events = list(backend.stream_raw_visits(OPP, "t", expected_visit_count=20, pipeline_id=VISITS_PIPELINE_B))
        unscoped = backend.fetch_raw_visits(OPP, "t", expected_visit_count=20)

    assert client.paginate.call_count == 1
    assert len(rows) == 20
    assert events == [("cached", 20)]
    assert len(unscoped) == 20


def test_the_rebuild_lock_is_taken_on_the_shared_slot():
    """Single-flight must partition the way the cache does. Keyed per pipeline, two
    pipelines missing the same opportunity at once both led their own walk."""
    backend = SQLBackend()
    seen = []

    class _Claim:
        def __init__(self, opp, slot):
            seen.append((opp, slot))

        def __enter__(self):
            return True

        def __exit__(self, *a):
            return False

    client = _counting_client([_records(5)])
    with (
        patch("connect_labs.labs.analysis.backends.sql.backend.claim_raw_rebuild", _Claim),
        patch("connect_labs.labs.integrations.connect.factory.get_export_client", return_value=client),
    ):
        list(backend.stream_raw_visits(OPP, "t", expected_visit_count=5, pipeline_id=VISITS_PIPELINE_A))
        RawVisitCache.objects.all().delete()
        list(backend.stream_raw_visits(OPP, "t", expected_visit_count=5, pipeline_id=VISITS_PIPELINE_B))

    assert len(seen) == 2
    assert seen[0] == seen[1]


def test_a_pipelines_extraction_reads_the_rows_a_sibling_pipeline_fetched():
    """Sharing the write is only half of it: pipeline B's SQL extraction must read the
    slot pipeline A filled, or B computes over nothing."""
    client = _counting_client([[{**r, "form_json": {"form": {"x": 1}}} for r in _records(6)]])
    backend = SQLBackend()
    with patch("connect_labs.labs.integrations.connect.factory.get_export_client", return_value=client):
        list(backend.stream_raw_visits(OPP, "t", expected_visit_count=6, pipeline_id=VISITS_PIPELINE_A))

    result = backend.get_period_scoped_flw_result(OPP, _visits_config(VISITS_PIPELINE_B))
    assert result is not None
    assert sum(r.total_visits for r in result.rows) == 6


# --- #116: other sources keep their own slot -------------------------------------


def _store_forms(n):
    SQLCacheManager(OPP, _forms_config(FORMS_PIPELINE)).store_raw_visits(
        [{"id": f"form-{i}", "username": "flw0", "form_json": {"form": {"x": 1}}} for i in range(n)], n
    )


def test_a_forms_pipeline_write_does_not_clobber_the_shared_visits_slot():
    client = _counting_client([_records(30)])
    backend = SQLBackend()
    with patch("connect_labs.labs.integrations.connect.factory.get_export_client", return_value=client):
        list(backend.stream_raw_visits(OPP, "t", expected_visit_count=30, pipeline_id=VISITS_PIPELINE_A))

    _store_forms(7)

    assert SQLCacheManager(OPP, pipeline_id=VISITS_PIPELINE_B).get_raw_visit_count() == 30
    assert SQLCacheManager(OPP, _forms_config(FORMS_PIPELINE)).get_raw_visit_count() == 7


def test_a_visits_walk_does_not_clobber_a_forms_pipeline_slot():
    _store_forms(7)

    client = _counting_client([_records(30)])
    backend = SQLBackend()
    with patch("connect_labs.labs.integrations.connect.factory.get_export_client", return_value=client):
        list(backend.stream_raw_visits(OPP, "t", expected_visit_count=30, pipeline_id=VISITS_PIPELINE_A))
        # A visits walk requested UNDER the forms pipeline's id (the visit-cache
        # warm-up does this for every pipeline a workflow reads) still lands in
        # the visits slot, never in the forms one.
        backend.fetch_raw_visits(OPP, "t", expected_visit_count=30, pipeline_id=FORMS_PIPELINE, force_refresh=True)

    forms = SQLCacheManager(OPP, _forms_config(FORMS_PIPELINE)).get_raw_visits_queryset()
    assert sorted(forms.values_list("visit_id", flat=True)) == sorted(f"form-{i}" for i in range(7))


def test_a_forms_pipelines_extraction_never_reads_visits():
    client = _counting_client([_records(30)])
    backend = SQLBackend()
    with patch("connect_labs.labs.integrations.connect.factory.get_export_client", return_value=client):
        list(backend.stream_raw_visits(OPP, "t", expected_visit_count=30, pipeline_id=VISITS_PIPELINE_A))
    _store_forms(7)

    result = backend.get_period_scoped_flw_result(OPP, _forms_config(FORMS_PIPELINE))
    assert sum(r.total_visits for r in result.rows) == 7


def test_a_delta_top_up_on_an_image_bearing_slot_asks_for_images():
    """Image readers and pipelines now share one slot, so a pipeline's top-up can land on
    rows fetched WITH images. Appending image-less rows there would tell the image
    reader those new visits have no photo."""
    expires_at = timezone.now() + timedelta(minutes=50)
    manager = SQLCacheManager(OPP)
    manager.store_raw_visits_start(0, images_fetched=True)
    manager.store_raw_visits_batch(_records(40))
    manager.store_raw_visits_finalize(40)
    RawVisitCache.objects.update(expires_at=expires_at)

    client = _counting_client([_records(5, first_id=2000)])
    with patch("connect_labs.labs.integrations.connect.factory.get_export_client", return_value=client):
        SQLBackend().fetch_raw_visits(OPP, "t", expected_visit_count=45, pipeline_id=VISITS_PIPELINE_B)

    params = client.paginate.call_args.kwargs.get("params") or client.paginate.call_args.args[1]
    assert params.get("images") == "true"
    assert not RawVisitCache.objects.filter(opportunity_id=OPP, images_fetched=False).exists()
