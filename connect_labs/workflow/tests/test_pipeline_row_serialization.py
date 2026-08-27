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

import pytest
from django.test import RequestFactory

from connect_labs.labs.analysis.models import EntityRow, FLWRow, VisitRow
from connect_labs.workflow.data_access import PipelineDataAccess, WorkflowDataAccess
from connect_labs.workflow.views import PipelineDataStreamView

# The snapshot path resolves pipeline definitions through the labs-only
# synthetic registry, which is a real DB lookup.
pytestmark = pytest.mark.django_db

# The real schema of pipeline 5226 (opp 10046), the visit_level pipeline from
# the ace#1657 report.
PIPELINE_SCHEMA = {
    "name": "Bednet follow-up submissions",
    "fields": [
        {"name": "consent_confirmed", "path": "form.agree_again.consent_confirmed", "aggregation": "first"},
        {"name": "slept_under_net", "path": "form.net_check.slept_under_net", "aggregation": "first"},
        {"name": "net_visibly_hanging", "path": "form.net_check.net_visibly_hanging", "aggregation": "first"},
    ],
    "filters": {},
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
}

ALIAS = "performance_data"
OPP_ID = 10046
PIPELINE_ID = 5226
DEFINITION_ID = 5230

# Both sides are compared at the layer a dashboard actually reads, so there is
# no escape hatch: the two key sets must be equal, `opportunity_id` included.


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
    """Serialize via the real snapshot code path.

    Deliberately entered at `WorkflowDataAccess.get_cached_pipeline_data` — the
    function that actually builds `instance.snapshot.pipelines.<alias>.rows` —
    rather than at the inner `_serialize_pipeline_rows`. The framework's
    `opportunity_id` stamp lives in that outer layer, so comparing any lower
    would force the parity test to except it, and an excepted key is exactly
    where a divergence hides.
    """
    result = MagicMock()
    result.rows = rows

    definition = MagicMock(
        pipeline_sources=[{"pipeline_id": PIPELINE_ID, "alias": ALIAS}],
        opportunity_ids=[OPP_ID],
    )
    pipeline_def = MagicMock(schema=dict(PIPELINE_SCHEMA))
    pipeline_def.name = "Bednet follow-up submissions"

    access = WorkflowDataAccess(access_token="t", opportunity_id=OPP_ID)
    try:
        with (
            patch.object(WorkflowDataAccess, "get_definition", return_value=definition),
            patch.object(PipelineDataAccess, "get_definition", return_value=pipeline_def),
            patch(
                "connect_labs.workflow.views._resolve_pipeline_sources_for_run",
                return_value=(definition.pipeline_sources, {}),
            ),
            patch("connect_labs.labs.analysis.pipeline.AnalysisPipeline") as MockPipeline,
        ):
            # Real PipelineDataAccess, real _serialize_pipeline_rows, real
            # opportunity_id stamp — only the cache read underneath is stubbed.
            MockPipeline.return_value.get_cached_result_only.return_value = result
            data = access.get_cached_pipeline_data(DEFINITION_ID, OPP_ID)
    finally:
        access.close()
    return data[ALIAS]["rows"]


def _preview_rows(rows):
    """Serialize via the real MCP `pipeline_preview` tool.

    Entered at the tool function itself, not at the serializer it calls — the
    point of this helper is to catch `pipelines.py` growing its own row dict
    again, which a helper that called the shared serializer directly could
    never do.
    """
    from connect_labs.mcp.tools.pipelines import pipeline_preview

    result = MagicMock()
    result.rows = rows

    pipeline_def = MagicMock(schema=dict(PIPELINE_SCHEMA))
    pipeline_def.name = "Bednet follow-up submissions"

    with (
        patch("connect_labs.mcp.tools.pipelines.require_connect_token", return_value="t"),
        patch.object(PipelineDataAccess, "get_definition", return_value=pipeline_def),
        patch("connect_labs.labs.analysis.pipeline.AnalysisPipeline") as MockPipeline,
    ):
        MockPipeline.return_value.stream_analysis_ignore_events.return_value = result
        payload = pipeline_preview(
            user=MagicMock(),
            pipeline_id=PIPELINE_ID,
            opportunity_id=OPP_ID,
            schema_override=dict(PIPELINE_SCHEMA),
        )
    return payload["rows"]


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
    request.labs_context = {"opportunity_id": OPP_ID}
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
            assert set(live_row) == set(snapshot_row), (
                "live SSE and snapshot payloads disagree on row keys; "
                f"live-only={set(live_row) - set(snapshot_row)} "
                f"snapshot-only={set(snapshot_row) - set(live_row)}"
            )

    def test_key_sets_match_for_flw_level_rows(self, rf: RequestFactory):
        rows = [
            FLWRow(username="flw_one", total_visits=12, approved_visits=10, flagged_visits=2),
            FLWRow(username="flw_two", total_visits=3, approved_visits=3, flagged_visits=0),
        ]
        live = _live_rows(rows, rf)
        snapshot = _snapshot_rows(rows)

        assert len(live) == len(snapshot) == 2
        for live_row, snapshot_row in zip(live, snapshot):
            assert set(live_row) == set(snapshot_row)

    def test_preview_payload_matches_too(self, rf: RequestFactory):
        """`pipeline_preview` is the THIRD producer, and it had the same defect:
        it hand-rolled a row dict omitting entity_id, entity_name, status and
        flagged. It is the surface an author checks a pipeline with *before*
        wiring a dashboard to it, so it reported "no review outcome" from the
        same root cause, one step earlier in the workflow."""
        rows = _visit_rows()
        preview = _preview_rows(rows)
        live = _live_rows(rows, rf)

        assert len(preview) == len(live) == 2
        for preview_row, live_row in zip(preview, live):
            assert set(preview_row) == set(live_row), (
                f"preview-only={set(preview_row) - set(live_row)} " f"live-only={set(live_row) - set(preview_row)}"
            )
        assert [r["status"] for r in preview] == ["approved", "rejected"]
        assert [r["flagged"] for r in preview] == [False, True]

    def test_live_payload_carries_the_review_outcome(self, rf: RequestFactory):
        """The ace#1657 regression itself: `status` and `flagged` must survive
        the live read, with their real values — not be absent (which a render
        reads as 0 flagged / 0 rejected)."""
        live = _live_rows(_visit_rows(), rf)

        assert [r["status"] for r in live] == ["approved", "rejected"]
        assert [r["flagged"] for r in live] == [False, True]

    def test_live_payload_still_tags_source_opportunity(self, rf: RequestFactory):
        live = _live_rows(_visit_rows(), rf)
        assert {r["opportunity_id"] for r in live} == {OPP_ID}

    def test_the_framework_tag_wins_over_a_same_named_pipeline_field(self, rf: RequestFactory):
        """#1306: `chc_audit_history` declares a pipeline field literally named
        `opportunity_id` (connect_labs/workflow/templates/chc_audit_history.py).

        This is the inversion of the behaviour the live path used to have. The
        cached and snapshot producers stamp `{**row, "opportunity_id": opp_id}`
        *after* their merge, so the framework value has always won there; only
        the live path let the pipeline field through, which meant that dashboard
        could read one value live and another from its own snapshot.

        Framework-wins is the correct side, not just the majority one: upstream
        filters audit reports to the opportunity in the URL and serializes that
        FK as its pk, so on the prod path the two are equal by construction, and
        where they *can* differ (a fixture-backed synthetic opp carrying a source
        opp id) the framework tag is what keeps chc_audit_history's
        reports-to-entries join self-consistent — `audit_entries` declares no
        `opportunity_id`, so its rows only ever carry the framework value.
        """
        rows = [VisitRow(id="v1", username="flw_one", computed={"opportunity_id": 999})]
        live = _live_rows(rows, rf)
        assert live[0]["opportunity_id"] == OPP_ID

    def test_all_three_producers_agree_on_the_framework_tag(self, rf: RequestFactory):
        """The point of #1306 is agreement, not the winner as such — a value that
        differs between live and snapshot is the ace#1657 failure shape one field
        over, regardless of which side is right."""
        rows = [VisitRow(id="v1", username="flw_one", computed={"opportunity_id": 999})]
        live = _live_rows(rows, rf)
        (snap,) = _snapshot_rows(rows)

        assert live[0]["opportunity_id"] == snap["opportunity_id"], "live and snapshot must not disagree"
        assert live[0]["opportunity_id"] == OPP_ID

    def test_a_pipeline_field_not_shadowing_a_framework_key_is_untouched(self, rf: RequestFactory):
        """Only same-named keys are affected. Ordinary computed fields still
        land on the row exactly as before."""
        rows = [VisitRow(id="v1", username="flw_one", computed={"muac_cm": 11.4, "colour": "red"})]
        live = _live_rows(rows, rf)
        assert live[0]["muac_cm"] == 11.4
        assert live[0]["colour"] == "red"


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

    def test_an_entity_row_gets_null_rather_than_zero(self):
        """`EntityRow` declares `total_visits` but not the other four, and its
        `__getattr__` fallback intercepts before `getattr`'s default can apply
        — so on an entity-level payload those four are `null`, not `0`. A render
        binding them therefore gets a *different* falsy value depending on the
        pipeline's terminal stage. Documented here so it is not a surprise."""
        (row,) = _snapshot_rows([EntityRow(entity_id="e1", entity_name="HH 1", total_visits=4)])
        assert row["total_visits"] == 4
        assert [row[c] for c in ("approved_visits", "pending_visits", "rejected_visits", "flagged_visits")] == [
            None,
            None,
            None,
            None,
        ]

    def test_a_declared_pipeline_field_of_the_same_name_still_wins(self):
        """The one escape hatch: a schema that declares e.g. a `total_visits`
        field lands it in `computed`, which is merged last and overwrites the
        structural zero."""
        (row,) = _snapshot_rows([VisitRow(id="v1", username="flw", computed={"total_visits": 7})])
        assert row["total_visits"] == 7
