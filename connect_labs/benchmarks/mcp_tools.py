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
from connect_labs.mcp.tool_registry import MCPToolError, register


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
