"""A pipeline relation with no rows says why, and load_missing rebuilds it.

A pipeline's stored output (``labs_computed_*_cache``) lives an hour after the pipeline runs
(``SQLCacheManager``: ``PIPELINE_CACHE_TTL_HOURS``, 1 in production). A saved run's page draws
its snapshot and never runs the pipelines, and ``data_describe(load_missing)`` only loaded the
VISITS, so a workflow's ``p_<alias>`` relations read nothing a day later and data_describe said
only 0 (data_chat_eval: workflow 9027, rows expired the day before).
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.models import ComputedFLWCache
from connect_labs.labsdata import pipelines
from connect_labs.labsdata.pipelines import PipelineRelation

pytestmark = pytest.mark.django_db

REL = PipelineRelation(
    name="p_children",
    alias="children",
    pipeline_id=19776,
    pipeline_name="Children",
    stage="aggregated",
    config_hash="current",
    field_names=("weight",),
)


def _rows(opp, config_hash, expires_at):
    ComputedFLWCache.objects.create(
        opportunity_id=opp,
        pipeline_id=REL.pipeline_id,
        config_hash=config_hash,
        visit_count=3,
        expires_at=expires_at,
        username="flw",
    )


def test_each_empty_opportunity_says_why():
    now = timezone.now()
    _rows(1, "current", now - timedelta(days=1))  # ran a day ago: expired
    _rows(2, "older-version", now + timedelta(hours=1))  # current rows, for a pipeline since changed
    reasons = pipelines.stale_reasons({"p_children": [1, 2, 3]}, [REL], now=now)["p_children"]
    assert reasons[1]["state"] == "expired" and reasons[1]["expired_at"].startswith(
        (now - timedelta(days=1)).date().isoformat()
    )
    assert "expired on" in reasons[1]["why"]
    assert reasons[2]["state"] == "outdated"
    assert reasons[3]["state"] == "not_built"


def test_describe_never_reports_a_silent_zero():
    from connect_labs.labsdata import service

    selection = SimpleNamespace(opportunities=[SimpleNamespace(id=1), SimpleNamespace(id=2)], relations=[REL])
    _rows(1, "current", timezone.now() - timedelta(hours=2))
    with (
        mock.patch.object(service.engine, "pipeline_fields", return_value={}),
        mock.patch.object(service.engine, "pipeline_status", return_value={"p_children": {1: 0, 2: 40}}),
    ):
        out = service._pipeline_relations(selection, withheld=False)["p_children"]
    assert out["stale_opportunities"] == [1]
    assert out["why_no_rows"]["1"]["state"] == "expired"
    assert "load_missing" in out["note"] and "1 of 2" in out["note"]


def test_load_missing_rebuilds_the_empty_opportunities_a_few_at_a_time():
    selection = SimpleNamespace(relations=[REL], workflow={"id": 9027, "name": "KMC", "scope": {"program_id": 10121}})
    ran = []

    def run_pipeline(opp, pid):
        ran.append((opp, pid))
        return {"rows": [1], "metadata": {}}

    clock = iter([0, 1, 100, 101])  # start; before the 2nd opportunity; before the 3rd
    with (
        mock.patch.object(
            pipelines,
            "read_workflow",
            return_value=(
                SimpleNamespace(pipeline_sources=[{"pipeline_id": 19776}], data={}),
                "tok",
                {"program_id": 10121},
                [],
            ),
        ),
        mock.patch(
            "connect_labs.workflow.visit_cache._default_slot",
            return_value=(None, lambda opp, pid: 3, run_pipeline),
        ),
        mock.patch("time.monotonic", side_effect=lambda: next(clock)),
    ):
        report = pipelines.refill(mock.Mock(), selection, [10122, 10123, 10124], budget_seconds=25)
    # Two within the budget; the third waits for the next call.
    assert report["rebuilt"] == [10122, 10123] and report["pending"] == [10124]
    assert ran == [(10122, 19776), (10123, 19776)]


def test_a_failing_opportunity_does_not_cost_the_rest():
    selection = SimpleNamespace(relations=[REL], workflow={"id": 9027, "name": "KMC", "scope": {"program_id": 10121}})

    def run_pipeline(opp, pid):
        if opp == 1:
            raise RuntimeError("export failed")
        return {"rows": [], "metadata": {}}

    with (
        mock.patch.object(
            pipelines, "read_workflow", return_value=(SimpleNamespace(pipeline_sources=[], data={}), "t", {}, [])
        ),
        mock.patch(
            "connect_labs.workflow.visit_cache._default_slot", return_value=(None, lambda o, p: 0, run_pipeline)
        ),
    ):
        report = pipelines.refill(mock.Mock(), selection, [1, 2])
    assert report["rebuilt"] == [2] and report["failed"][0]["opportunity_id"] == 1


def test_describe_with_load_missing_rebuilds_through_the_selection():
    """Through ``service.describe`` itself, as data_describe calls it (the harness caught
    this path broken where the helpers alone passed)."""
    from connect_labs.labsdata import service

    opps = [SimpleNamespace(id=1, name="One", llo="L")]
    selection = SimpleNamespace(
        opportunities=opps,
        relations=[REL],
        workflow={"id": 9027, "name": "KMC", "scope": {"program_id": 10121}},
        as_dict=lambda: {},
    )
    space = SimpleNamespace(selection=selection)
    with (
        mock.patch.object(service, "_held", return_value=space),
        mock.patch.object(service, "warm", return_value=[]),
        mock.patch.object(service, "related_registries", return_value=[]),
        mock.patch.object(service.fields, "cache_status", return_value=[]),
        mock.patch.object(service.engine, "pipeline_fields", return_value={}),
        mock.patch.object(service.engine, "pipeline_status", return_value={"p_children": {1: 0}}),
        mock.patch.object(
            service.pipelines, "refill", return_value={"rebuilt": [1], "failed": [], "pending": []}
        ) as refill,
    ):
        out = service.describe(mock.Mock(), space, load_missing=True, include_fields=False, include_registries=False)
    refill.assert_called_once()
    assert refill.call_args.args[1] is selection and refill.call_args.args[2] == [1]
    assert out["pipelines_rebuilt"]["rebuilt"] == [1]
