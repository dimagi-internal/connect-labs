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

from commcare_connect.labs.context import _merge_labs_only_opps
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
        "the caller doesn't already know them. Pass `search` to filter the "
        "tree by case-insensitive substring match on org name/slug, program "
        "name, and opportunity name — empty orgs are dropped from the result."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "search": {
                "type": "string",
                "description": (
                    "Case-insensitive substring filter applied to org name, org slug, "
                    "program name, and opportunity name. Orgs with no matching descendants "
                    "are pruned from the result."
                ),
            },
        },
        "additionalProperties": False,
    },
)
def labs_context(user, search: str = None) -> dict[str, Any]:
    token = require_connect_token(user)
    data = fetch_user_organization_data(token)
    if data is None:
        raise MCPToolError(
            "UPSTREAM_ERROR",
            "Failed to fetch organization data from production Connect.",
        )

    # Merge labs-only synthetic opps into the org/program/opp lists for users
    # who have opted in via view_synthetic_opps. Same chokepoint the web app
    # uses (labs.context.get_org_data), so MCP and UI stay consistent.
    if getattr(user, "view_synthetic_opps", False):
        data = _merge_labs_only_opps(data, user)

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

    filtered_tree = _apply_search(tree, search)
    filtered_totals = (
        _count_tree(filtered_tree)
        if search
        else {
            "organizations": len(organizations),
            "programs": len(programs),
            "opportunities": len(opportunities),
        }
    )

    return {
        "user": data.get("user") or {},
        "organizations": filtered_tree,
        "totals": filtered_totals,
        "search": search or None,
    }


def _apply_search(tree: list[dict], search: str | None) -> list[dict]:
    """Prune the tree to orgs / programs / opps whose names contain `search`.

    If any node in a subtree matches, all of its ancestors are kept so the
    result remains a valid tree. Leaves that don't match are dropped; parents
    with no matching descendants (and no self-match) are dropped.
    """
    if not search:
        return tree
    needle = search.lower()

    def _opp_matches(o: dict) -> bool:
        return needle in (o.get("name") or "").lower()

    def _program_matches_or_keeps(p: dict) -> dict | None:
        kept_opps = [o for o in p.get("opportunities", []) if _opp_matches(o)]
        if needle in (p.get("name") or "").lower():
            # Program name match → keep all its opportunities verbatim.
            return {**p, "opportunities": p.get("opportunities", [])}
        if kept_opps:
            return {**p, "opportunities": kept_opps}
        return None

    out = []
    for org in tree:
        org_self_match = needle in (org.get("name") or "").lower() or needle in (org.get("slug") or "").lower()
        kept_programs = [p for p in (_program_matches_or_keeps(p) for p in org.get("programs", [])) if p]
        kept_opps = [o for o in org.get("opportunities", []) if _opp_matches(o)]
        if org_self_match:
            # Org matches: keep everything underneath it verbatim.
            out.append(org)
            continue
        if kept_programs or kept_opps:
            out.append({**org, "programs": kept_programs, "opportunities": kept_opps})
    return out


def _count_tree(tree: list[dict]) -> dict[str, int]:
    orgs = len(tree)
    programs = sum(len(org.get("programs", [])) for org in tree)
    opportunities = sum(
        len(org.get("opportunities", [])) + sum(len(p.get("opportunities", [])) for p in org.get("programs", []))
        for org in tree
    )
    return {"organizations": orgs, "programs": programs, "opportunities": opportunities}
