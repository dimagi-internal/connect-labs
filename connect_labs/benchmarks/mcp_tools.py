"""Cohort administration -- the missing write path for Plan 1's benchmark store.

Plan 1 shipped models, disclosure rules, a publisher, and a read API, but no
way to create a cohort or add opportunities to one, so nothing could ever be
published and the read API returned ``{}`` forever. These are plain local
ORM writes against ``BenchmarkCohort`` / ``BenchmarkCohortMember`` -- the labs
DB is the system of record for benchmarks (see models.py), so unlike most
labs apps there is no Connect token or LabsRecord API involved here.
"""

from __future__ import annotations

from typing import Any

from connect_labs.benchmarks.models import MIN_PEERS_FLOOR, BenchmarkCohort, BenchmarkCohortMember
from connect_labs.benchmarks.publish import publish_benchmark
from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data
from connect_labs.mcp.connect_token import require_connect_token
from connect_labs.mcp.tool_registry import MCPToolError, register
from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.templates import resolve_snapshot_contract


def _serialize_cohort(cohort: BenchmarkCohort) -> dict[str, Any]:
    return {
        "id": cohort.pk,
        "name": cohort.name,
        "organization_id": cohort.organization_id,
        "description": cohort.description,
        "auto_publish_on_completion": cohort.auto_publish_on_completion,
        "min_peers": cohort.min_peers,
        "min_denominator": cohort.min_denominator,
        "opportunity_ids": sorted(cohort.opportunity_ids),
    }


@register(
    name="benchmarks_cohort_create",
    description=(
        "Create a benchmark cohort -- a named set of opportunities that may be "
        "benchmarked against each other. Membership IS the grant: an opportunity "
        "sees benchmarks for the cohorts it belongs to and nothing else. "
        f"min_peers must be >= {MIN_PEERS_FLOOR} (below that, the reader is one "
        "of the contributors, so the one remaining bar is a named peer's exact "
        "value)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "organization_id": {
                "type": "string",
                "description": "Connect organisation slug that owns and may publish this cohort (e.g. 'dimagi-kmc').",
            },
            "description": {"type": "string", "default": ""},
            "min_peers": {
                "type": "integer",
                "default": 5,
                "description": (
                    "Disclosure floor: minimum contributing peers required to publish a figure. "
                    f"Must be >= {MIN_PEERS_FLOOR}."
                ),
            },
            "min_denominator": {
                "type": "integer",
                "default": 25,
                "description": "Disclosure floor: minimum denominator required to publish a rate.",
            },
        },
        "required": ["name", "organization_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def benchmarks_cohort_create(
    user,
    *,
    name: str,
    organization_id: str,
    description: str = "",
    min_peers: int = 5,
    min_denominator: int = 25,
) -> dict[str, Any]:
    if min_peers < MIN_PEERS_FLOOR:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"min_peers must be >= {MIN_PEERS_FLOOR}: at {min_peers} the reader is one of the "
            "contributors, so the one remaining bar is a named peer's exact value.",
        )
    cohort = BenchmarkCohort.objects.create(
        name=name,
        organization_id=organization_id,
        description=description,
        min_peers=min_peers,
        min_denominator=min_denominator,
    )
    return _serialize_cohort(cohort)


@register(
    name="benchmarks_cohort_add_opportunities",
    description=(
        "Add opportunities to a benchmark cohort. Idempotent -- re-adding an "
        "opportunity already in the cohort is a no-op. Returns the cohort's "
        "full membership after the add."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cohort_id": {"type": "integer"},
            "opportunity_ids": {
                "type": "array",
                "items": {"type": "integer"},
            },
        },
        "required": ["cohort_id", "opportunity_ids"],
        "additionalProperties": False,
    },
    is_write=True,
)
def benchmarks_cohort_add_opportunities(
    user,
    *,
    cohort_id: int,
    opportunity_ids: list[int],
) -> dict[str, Any]:
    try:
        cohort = BenchmarkCohort.objects.get(pk=cohort_id)
    except BenchmarkCohort.DoesNotExist as exc:
        raise MCPToolError("NOT_FOUND", f"No cohort with id {cohort_id}") from exc

    for opportunity_id in opportunity_ids:
        BenchmarkCohortMember.objects.get_or_create(cohort=cohort, opportunity_id=int(opportunity_id))

    return _serialize_cohort(cohort)


@register(
    name="benchmarks_cohort_list",
    description="List benchmark cohorts owned by an organization, with membership counts.",
    input_schema={
        "type": "object",
        "properties": {
            "organization_id": {"type": "string"},
        },
        "required": ["organization_id"],
        "additionalProperties": False,
    },
)
def benchmarks_cohort_list(user, *, organization_id: str) -> dict[str, Any]:
    cohorts = BenchmarkCohort.objects.filter(organization_id=organization_id).order_by("name")
    return {
        "cohorts": [
            {
                "id": cohort.pk,
                "name": cohort.name,
                "organization_id": cohort.organization_id,
                "opportunity_count": cohort.members.count(),
                "min_peers": cohort.min_peers,
                "min_denominator": cohort.min_denominator,
            }
            for cohort in cohorts
        ]
    }


def _caller_organization_slugs(user, access_token: str) -> set[str]:
    """Connect organisation slugs the caller belongs to, per production Connect.

    Mirrors ``workflows.py``'s ``_collect_user_opportunity_ids`` -- same fetch,
    same cache, read for organisation slugs instead of opportunity ids.
    """
    data = fetch_user_organization_data(access_token, owner=getattr(user, "username", None))
    if not data:
        return set()
    return {str(org.get("slug")) for org in (data.get("organizations") or []) if org.get("slug")}


@register(
    name="benchmarks_publish",
    description=(
        "Publish a completed workflow run's graded figures to a benchmark cohort. Refuses "
        "a run that is not completed -- its figures are still moving -- and refuses a caller "
        "who lacks access to the cohort's organisation. Everything the publisher's own "
        "disclosure rules withhold (non-rate indicators, cohorts below min_peers/min_denominator "
        "after the rules run) is withheld and reported back, never silently dropped. If the "
        "run's snapshot is not the shape this publisher reads -- or yields no benchmarkable "
        "observations at all -- the call fails loudly rather than writing an empty publication, "
        "so a caller must not treat a raised error as 'nothing to publish'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cohort_id": {"type": "integer"},
            "workflow_id": {
                "type": "integer",
                "description": "The workflow definition id the run belongs to.",
            },
            "run_id": {"type": "integer"},
            "opportunity_id": {
                "type": "integer",
                "description": "Scope for loading the run, if it is opportunity-owned.",
            },
            "program_id": {
                "type": "integer",
                "description": "Scope for loading the run, if it is program-owned (multi-opp report).",
            },
        },
        "required": ["cohort_id", "workflow_id", "run_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def benchmarks_publish(
    user,
    *,
    cohort_id: int,
    workflow_id: int,
    run_id: int,
    opportunity_id: int | None = None,
    program_id: int | None = None,
) -> dict[str, Any]:
    try:
        cohort = BenchmarkCohort.objects.get(pk=cohort_id)
    except BenchmarkCohort.DoesNotExist as exc:
        raise MCPToolError("NOT_FOUND", f"Benchmark cohort {cohort_id} not found.") from exc

    token = require_connect_token(user)
    if cohort.organization_id not in _caller_organization_slugs(user, token):
        raise MCPToolError(
            "PERMISSION_DENIED",
            f"You do not have access to organisation {cohort.organization_id!r}, which owns cohort {cohort_id}.",
        )

    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id, program_id=program_id)
    try:
        run = wda.get_run(run_id)
        if run is None:
            raise MCPToolError("NOT_FOUND", f"Run {run_id} not found.")
        if not run.is_completed:
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"Run {run_id} is not completed -- its figures are still moving and cannot be published.",
            )

        # The graded payload a saved run stores is one level down from
        # `run.snapshot`, under `["state"][<state_key>]` --
        # `workflow/snapshot_builders.wrap_for_runner` wraps it there so the
        # runner's `view.state.<key>` contract resolves, and `state_key`
        # defaults to "snapshot" but is spec-driven per workflow (see
        # `workflow/history_rebuild.py`'s identical resolution). Verified
        # against `connect_labs/semantic/snapshot.py::build` and
        # `connect_labs/benchmarks/publish.py`'s own docstring, both of which
        # name this exact path.
        definition = wda.get_definition(workflow_id)
        state_key = "snapshot"
        if definition is not None:
            contract = resolve_snapshot_contract(definition)
            if contract.get("ok"):
                state_key = (contract.get("snapshot_inputs") or {}).get("state_key") or "snapshot"

        state = (run.snapshot or {}).get("state") or {}
        graded_payload = state.get(state_key) or {}

        meta = graded_payload.get("meta") or {}
        registry_id = (meta.get("registry") or {}).get("registry_id")
        as_of = (
            meta.get("as_of")
            or (str(run.period_end)[:10] if run.period_end else None)
            or (str(run.completed_at)[:10] if run.completed_at else None)
        )

        publication = publish_benchmark(
            cohort,
            snapshot=graded_payload,
            source_workflow_id=workflow_id,
            source_run_id=run_id,
            registry_id=registry_id,
            as_of=as_of,
            published_by=getattr(user, "username", "") or "",
        )
    finally:
        wda.close()

    return {
        "publication_id": publication.pk,
        "cohort_id": cohort.pk,
        "as_of": str(publication.as_of),
        "value_count": publication.values.count(),
        "withheld_indicator_ids": publication.withheld_indicator_ids,
    }
