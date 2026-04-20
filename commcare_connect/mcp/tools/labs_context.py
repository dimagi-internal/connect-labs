"""labs_context — return the org/program/opportunity tree for the caller.

Mirrors the "labs context" data the web app stashes in its OAuth session via
``fetch_user_organization_data`` (see ``commcare_connect/labs/context.py``).
The MCP caller authenticates with a PAT, and we forward their stored Connect
OAuth access_token to production Connect at ``/export/opp_org_program_list/``.

Output is organized hierarchically (organization → program → opportunity) so
that agents can find a concrete ``opportunity_id`` / ``program_id`` /
``organization_id`` to pass to other scoped tools without having to guess or
call multiple endpoints. Opportunities without a parent program (i.e.
non-managed opps owned directly by an org) are attached to their org under a
separate ``opportunities`` list, since they have no parent program to nest
under.
"""

from __future__ import annotations

import logging
from typing import Any

from commcare_connect.labs.integrations.connect.oauth import fetch_user_organization_data

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)


def _opp_summary(opp: dict) -> dict:
    """Trim the raw opportunity serializer output to the fields agents actually use."""
    return {
        "id": opp.get("id"),
        "name": opp.get("name"),
        "is_active": opp.get("is_active"),
        "end_date": opp.get("end_date"),
        "visit_count": opp.get("visit_count"),
    }


def _program_summary(prog: dict, opportunities: list[dict]) -> dict:
    return {
        "id": prog.get("id"),
        "name": prog.get("name"),
        "delivery_type": prog.get("delivery_type"),
        "currency": prog.get("currency"),
        "opportunities": [_opp_summary(o) for o in opportunities],
    }


@register(
    name="labs_context",
    description=(
        "Return the caller's labs context: all organizations, programs, and "
        "opportunities the authenticated user can access, organized "
        "hierarchically (organization → program → opportunity). Mirrors the "
        "same data Labs stores in its OAuth session (see "
        "commcare_connect/labs/context.py); fetched live from "
        "/export/opp_org_program_list/ on production Connect. Use this to "
        "discover valid opportunity_id / program_id / organization_id values "
        "for the scoped tools (workflow_list, list_solicitations, etc.) when "
        "the caller doesn't already know them."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def labs_context(user) -> dict[str, Any]:
    token = require_connect_token(user)
    data = fetch_user_organization_data(token)
    if data is None:
        raise MCPToolError(
            "UPSTREAM_ERROR",
            "Failed to fetch organization data from production Connect.",
        )

    organizations: list[dict] = data.get("organizations") or []
    programs: list[dict] = data.get("programs") or []
    opportunities: list[dict] = data.get("opportunities") or []

    # Index programs by their owning org slug. A program's ``organization`` field
    # is serialized as the org's slug (SlugRelatedField), which matches the
    # ``slug`` field on each organization record.
    programs_by_org_slug: dict[str, list[dict]] = {}
    for prog in programs:
        programs_by_org_slug.setdefault(prog.get("organization"), []).append(prog)

    # Partition opportunities:
    #   - ``program`` set → managed opp, nest under its program
    #   - ``program`` null → non-managed opp, nest directly under its org
    # Production's serializer sets ``program`` only when the opp is a managed
    # opportunity (see OpportunityDataExportSerializer.get_program).
    opps_by_program_id: dict[int, list[dict]] = {}
    opps_by_org_slug: dict[str, list[dict]] = {}
    for opp in opportunities:
        program_id = opp.get("program")
        if program_id is not None:
            opps_by_program_id.setdefault(program_id, []).append(opp)
        else:
            opps_by_org_slug.setdefault(opp.get("organization"), []).append(opp)

    tree: list[dict] = []
    for org in organizations:
        slug = org.get("slug")
        org_programs = programs_by_org_slug.get(slug, [])
        tree.append(
            {
                "id": org.get("id"),
                "slug": slug,
                "name": org.get("name"),
                "programs": [_program_summary(p, opps_by_program_id.get(p.get("id"), [])) for p in org_programs],
                "opportunities": [_opp_summary(o) for o in opps_by_org_slug.get(slug, [])],
            }
        )

    return {
        "user": data.get("user") or {},
        "organizations": tree,
        "totals": {
            "organizations": len(organizations),
            "programs": len(programs),
            "opportunities": len(opportunities),
        },
    }
