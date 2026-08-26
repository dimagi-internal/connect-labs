"""Parity guard for the two pipeline-data payloads.

A `visit_level` pipeline can be read two ways, and both shapes reach dashboard
render code:

* **snapshot** — a run that has been concluded freezes its rows into
  `instance.snapshot.pipelines.<alias>.rows`, built by the cached reads in
  `WorkflowDataAccess.get_cached_pipeline_data` →
  `PipelineDataAccess._serialize_pipeline_rows`.
* **live SSE** — a run still `in_progress` streams its rows from
  `/labs/workflow/api/<id>/pipeline-data/stream/`
  (`PipelineDataStreamView.stream_data`).

Those two used to be built by two hand-written dicts, and they drifted: the SSE
one omitted `status` and `flagged`, the only two fields `VisitRow` carries and
`FLWRow` does not. Because a dashboard whose scene shows a reviewer *taking* a
decision has to be left `in_progress` (concluding a run makes the page
read-only), the review dashboard was exactly the one reading the payload with
no review outcome in it — it rendered `0 flagged` / `0 rejected` over 276 rows
containing 14 flagged and 6 rejected visits, with no error anywhere. ace#1657.

These tests pin the two payloads' key sets against each other so they cannot
drift apart again.
"""

import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory

from connect_labs.labs.analysis.models import FLWRow, VisitRow
from connect_labs.workflow.data_access import PipelineDataAccess
from connect_labs.workflow.views import PipelineDataStreamView

# Keys the framework stamps onto a live row but not onto a cached one (the
# cached path stamps it a layer up, in get_cached_pipeline_data). Excluded from
# the key-set comparison; everything else must match exactly.
FRAMEWORK_ONLY_LIVE_KEYS = {"opportunity_id"}


def _visit_rows():
    """Two visit-level rows, one of them a rejected + flagged visit."""
    return [
        VisitRow(
            id="v1",
            username="flw_one",
            status="approved",
            flagged=False,
            entity_id="e1",
            entity_name="Household 1",
            computed={"consent_confirmed": True, "slept_under_net": True},
        ),
        VisitRow(
            id="v2",
            username="flw_two",
            status="rejected",
            flagged=True,
            entity_id="e2",
            entity_name="Household 2",
            computed={"consent_confirmed": False, "slept_under_net": False},
        ),
    ]


def _snapshot_rows(rows):
    """Serialize via the real cached/snapshot code path."""
    access = PipelineDataAccess(access_token="t", opportunity_id=10046)
    try:
        result = MagicMock()
        result.rows = rows
        return access._serialize_pipeline_rows(result)
    finally:
        access.close()


def _live_rows(rows, rf: RequestFactory):
    """Serialize via the real live SSE code path, parsing the emitted events."""

    class FakeMixin:
        def __init__(self):
            self._pipeline_result = MagicMock()
            self._pipeline_result.rows = rows
            self._pipeline_from_cache = False

        def stream_pipeline_events(self, *args, **kwargs):
            return iter(())

    request = rf.get("/labs/workflow/api/5230/pipeline-data/stream/?opportunity_id=10046")
    request.user = MagicMock(is_authenticated=True)
    request.labs_context = {"opportunity_id": 10046}
    request.session = {"labs_oauth": {"access_token": "t"}}

    definition = MagicMock(
        pipeline_sources=[{"pipeline_id": 5226, "alias": "performance_data"}],
        opportunity_ids=[10046],
    )
    pipeline_def = MagicMock(schema={"terminal_stage": "visit_level"})
    pipeline_def.name = "Visit performance data"

    with (
        patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA,
        patch("connect_labs.workflow.views.PipelineDataAccess"),
        patch("connect_labs.workflow.views._resolve_pipeline_definition", return_value=pipeline_def),
        patch(
            "connect_labs.workflow.views._resolve_pipeline_sources_for_run",
            return_value=(definition.pipeline_sources, {}),
        ),
        patch.object(PipelineDataStreamView, "_maybe_probe_cchq_access", return_value=iter(())),
        patch("connect_labs.labs.analysis.pipeline.AnalysisPipeline"),
        patch("connect_labs.labs.analysis.sse_streaming.AnalysisPipelineSSEMixin", FakeMixin),
    ):
        MockWDA.return_value.get_definition.return_value = definition
        view = PipelineDataStreamView()
        view.kwargs = {"definition_id": 5230}
        events = [json.loads(chunk[len("data: ") :]) for chunk in view.stream_data(request)]

    payloads = [e for e in events if (e.get("data") or {}).get("pipelines")]
    assert payloads, f"stream emitted no pipeline payload; events={events}"
    return payloads[-1]["data"]["pipelines"]["performance_data"]["rows"]


class TestLiveAndSnapshotPayloadParity:
    def test_key_sets_match_for_visit_level_rows(self, rf: RequestFactory):
        rows = _visit_rows()
        live = _live_rows(rows, rf)
        snapshot = _snapshot_rows(rows)

        assert len(live) == len(snapshot) == 2
        for live_row, snapshot_row in zip(live, snapshot):
            assert set(live_row) - FRAMEWORK_ONLY_LIVE_KEYS == set(snapshot_row), (
                "live SSE and snapshot payloads disagree on row keys; "
                f"live-only={set(live_row) - FRAMEWORK_ONLY_LIVE_KEYS - set(snapshot_row)} "
                f"snapshot-only={set(snapshot_row) - set(live_row)}"
            )

    def test_key_sets_match_for_flw_level_rows(self, rf: RequestFactory):
        rows = [
            FLWRow(username="flw_one", total_visits=12, approved_visits=10, flagged_visits=2),
            FLWRow(username="flw_two", total_visits=3, approved_visits=3, flagged_visits=0),
        ]
        live = _live_rows(rows, rf)
        snapshot = _snapshot_rows(rows)

        for live_row, snapshot_row in zip(live, snapshot):
            assert set(live_row) - FRAMEWORK_ONLY_LIVE_KEYS == set(snapshot_row)

    def test_live_payload_carries_the_review_outcome(self, rf: RequestFactory):
        """The ace#1657 regression itself: `status` and `flagged` must survive
        the live read, with their real values — not be absent (which a render
        reads as 0 flagged / 0 rejected)."""
        live = _live_rows(_visit_rows(), rf)

        assert [r["status"] for r in live] == ["approved", "rejected"]
        assert [r["flagged"] for r in live] == [False, True]

    def test_live_payload_still_tags_source_opportunity(self, rf: RequestFactory):
        live = _live_rows(_visit_rows(), rf)
        assert {r["opportunity_id"] for r in live} == {10046}

    def test_a_pipeline_field_still_overrides_the_framework_opportunity_tag(self, rf: RequestFactory):
        """`chc_audit_history` declares a pipeline field literally named
        `opportunity_id` (connect_labs/workflow/templates/chc_audit_history.py).
        On the live path that field has always won over the framework's tag;
        the shared serializer must preserve that ordering."""
        rows = [VisitRow(id="v1", username="flw_one", computed={"opportunity_id": 999})]
        live = _live_rows(rows, rf)
        assert live[0]["opportunity_id"] == 999


class TestVisitLevelAggregateCountersAreStructurallyZero:
    """`VisitRow` declares none of the aggregate counters and — unlike `FLWRow`
    and `EntityRow` — has no `__getattr__` fallback into its computed fields
    (connect_labs/labs/analysis/models.py). So on a `visit_level` payload every
    one of them is the serializer's literal `0` default, in BOTH payloads, and a
    render binding `row.flagged_visits` gets a truthful-looking zero.

    This test documents that as a known, deliberate property rather than a
    surprise. If a future change makes these fields real on a visit row, or
    drops them from the payload, this test should be updated on purpose.
    """

    COUNTERS = (
        "total_visits",
        "approved_visits",
        "pending_visits",
        "rejected_visits",
        "flagged_visits",
    )

    def test_counters_are_zero_on_a_visit_row(self):
        (row,) = _snapshot_rows([VisitRow(id="v1", username="flw", status="rejected", flagged=True)])
        assert [row[c] for c in self.COUNTERS] == [0, 0, 0, 0, 0]

    def test_a_declared_pipeline_field_of_the_same_name_still_wins(self):
        """The one escape hatch: a schema that declares e.g. a `total_visits`
        field lands it in `computed`, which is merged last and overwrites the
        structural zero."""
        (row,) = _snapshot_rows([VisitRow(id="v1", username="flw", computed={"total_visits": 7})])
        assert row["total_visits"] == 7
