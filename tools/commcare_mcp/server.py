"""CommCare HQ MCP Server.

Provides CommCare application structure context for Claude Code sessions.
Tools let you explore app modules, form questions, and JSON field paths
for building workflow pipeline schemas.

Auth is automatic:
- CommCare HQ: reads COMMCARE_USERNAME + COMMCARE_API_KEY from project .env
- Connect API: reads CLI OAuth token from ~/.commcare-connect/token.json

Usage (stdio, for Claude Code):
    python tools/commcare_mcp/server.py
"""

from __future__ import annotations

import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RESOURCES_DIR = Path(__file__).parent / "resources"

mcp = FastMCP(
    "commcare-hq",
    instructions=(
        "CommCare HQ application structure server. Use these tools to understand "
        "CommCare app form structure, question types, and JSON field paths. "
        "This is especially useful when building or debugging workflow pipeline "
        "schemas (PIPELINE_SCHEMAS) that map form fields to data extraction paths.\n\n"
        "WORKFLOW: Start with get_opportunity_apps(opportunity_id) to discover the "
        "domain and app IDs, then use get_app_structure, get_form_questions, or "
        "get_form_json_paths to drill into specific forms.\n\n"
        "SOLICITATIONS: Use list_solicitations, get_solicitation, create_solicitation, "
        "update_solicitation, list_responses, and get_response to manage solicitations "
        "and their responses via the Connect Labs Record API."
    ),
)


# --- Resources ---


@mcp.resource("commcare://app-schema")
def app_schema_resource() -> str:
    """CommCare app structure reference — question types, case properties, validation rules."""
    return (RESOURCES_DIR / "app_schema.md").read_text(encoding="utf-8")


@mcp.resource("commcare://xml-reference")
def xml_reference_resource() -> str:
    """CommCare XForm/Suite/Case XML structure reference."""
    return (RESOURCES_DIR / "xml_reference.md").read_text(encoding="utf-8")


@mcp.resource("commcare://data-patterns")
def data_patterns_resource() -> str:
    """How CommCare form submission JSON is structured — path mapping rules and pitfalls."""
    return (RESOURCES_DIR / "data_patterns.md").read_text(encoding="utf-8")


# --- Tools ---


@mcp.tool()
async def get_opportunity_apps(opportunity_id: int) -> dict:
    """Get the CommCare domain and app IDs for a Connect opportunity.

    This is the starting point — it resolves an opportunity ID to the CommCare
    domain and learn/deliver app IDs needed by the other tools.

    Returns the cc_domain and cc_app_id for both the learn and deliver apps.

    Args:
        opportunity_id: The Connect opportunity ID (e.g., 874)
    """
    from connect_client import get_opportunity_apps as _get_opp

    try:
        return await _get_opp(opportunity_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def list_apps(domain: str = "") -> dict:
    """List all CommCare applications for a domain.

    Returns app names, IDs, module counts, and form counts.
    Use this to find the app_id needed for other tools.

    Args:
        domain: CommCare domain name (use get_opportunity_apps to find this)
    """
    from hq_client import list_apps as _list_apps

    try:
        apps = await _list_apps(domain or None)
        return {"apps": apps, "count": len(apps)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_app_structure(
    app_id: str = "",
    domain: str = "",
    opportunity_id: int = 0,
    app_type: str = "deliver",
) -> dict:
    """Get the module/form/case-type structure of a CommCare application.

    Shows the full app tree: modules → forms (with xmlns) → case types.
    Use this to understand how an app is organized before drilling into forms.

    Provide EITHER (opportunity_id) OR (domain + app_id):
    - opportunity_id: auto-resolves domain and app_id from Connect
    - domain + app_id: direct CommCare HQ lookup

    Args:
        app_id: The CommCare application ID (from list_apps or get_opportunity_apps)
        domain: CommCare domain name
        opportunity_id: Connect opportunity ID (alternative to domain+app_id)
        app_type: "deliver" or "learn" — which app to use when using opportunity_id
    """
    from connect_client import resolve_domain_and_app
    from extractors import extract_app_structure
    from hq_client import get_app

    try:
        resolved_domain, resolved_app_id = await resolve_domain_and_app(
            opportunity_id=opportunity_id or None,
            domain=domain,
            app_id=app_id,
            app_type=app_type,
        )
        app = await get_app(resolved_domain, resolved_app_id)
        return extract_app_structure(app)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_form_questions(
    xmlns: str,
    app_id: str = "",
    domain: str = "",
    opportunity_id: int = 0,
    app_type: str = "deliver",
) -> dict:
    """Get the full question tree for a specific form.

    Shows all questions with their types, labels, constraints, skip logic,
    and nesting (groups/repeats). Use this to understand what data a form collects.

    Provide EITHER (opportunity_id) OR (domain + app_id).

    Args:
        xmlns: The form's xmlns identifier (from get_app_structure)
        app_id: The CommCare application ID
        domain: CommCare domain name
        opportunity_id: Connect opportunity ID (alternative to domain+app_id)
        app_type: "deliver" or "learn" — which app to use when using opportunity_id
    """
    from connect_client import resolve_domain_and_app
    from extractors import extract_form_questions
    from hq_client import get_app

    try:
        resolved_domain, resolved_app_id = await resolve_domain_and_app(
            opportunity_id=opportunity_id or None,
            domain=domain,
            app_id=app_id,
            app_type=app_type,
        )
        app = await get_app(resolved_domain, resolved_app_id)
        result = extract_form_questions(app, xmlns)
        if result is None:
            return {"error": f"Form with xmlns '{xmlns}' not found in app {resolved_app_id}"}
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_form_json_paths(
    xmlns: str,
    app_id: str = "",
    domain: str = "",
    opportunity_id: int = 0,
    app_type: str = "deliver",
) -> dict:
    """Map form questions to their JSON submission paths for pipeline schemas.

    THIS IS THE KEY TOOL for building PIPELINE_SCHEMAS. It shows exactly what
    path each form question will have in submitted form JSON.

    Example output:
        {"json_path": "form.weight", "type": "Int", "label": "Weight (grams)"}
        {"json_path": "form.child_info.birth_weight", "type": "Decimal", "label": "Birth Weight"}

    Use the json_path values directly in PIPELINE_SCHEMAS field definitions:
        {"name": "weight", "path": "form.weight", "transform": "float"}

    Provide EITHER (opportunity_id) OR (domain + app_id).

    Args:
        xmlns: The form's xmlns identifier (from get_app_structure)
        app_id: The CommCare application ID
        domain: CommCare domain name
        opportunity_id: Connect opportunity ID (alternative to domain+app_id)
        app_type: "deliver" or "learn" — which app to use when using opportunity_id
    """
    from connect_client import resolve_domain_and_app
    from extractors import extract_form_json_paths
    from hq_client import get_app

    try:
        resolved_domain, resolved_app_id = await resolve_domain_and_app(
            opportunity_id=opportunity_id or None,
            domain=domain,
            app_id=app_id,
            app_type=app_type,
        )
        app = await get_app(resolved_domain, resolved_app_id)
        result = extract_form_json_paths(app, xmlns)
        if result is None:
            return {"error": f"Form with xmlns '{xmlns}' not found in app {resolved_app_id}"}
        return result
    except Exception as e:
        return {"error": str(e)}


# --- Solicitation Tools ---


@mcp.tool()
async def list_solicitations(
    program_id: str = "",
    organization_id: str = "",
    status: str = "",
    solicitation_type: str = "",
) -> dict:
    """List solicitations, optionally filtered by program or organization, status, or type.

    Args:
        program_id: Filter by program ID (e.g., "42")
        organization_id: Filter by organization ID or slug (alternative to program_id)
        status: Filter by status ("draft", "active", "closed")
        solicitation_type: Filter by type ("eoi", "rfp")
    """
    from solicitation_tools import list_solicitations as _list

    try:
        results = await _list(
            program_id=program_id or None,
            organization_id=organization_id or None,
            status=status or None,
            solicitation_type=solicitation_type or None,
        )
        return {"solicitations": results, "count": len(results)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_solicitation(solicitation_id: int) -> dict:
    """Get a single solicitation by ID.

    Args:
        solicitation_id: The solicitation record ID
    """
    from solicitation_tools import get_solicitation as _get

    try:
        result = await _get(solicitation_id)
        if result is None:
            return {"error": f"Solicitation {solicitation_id} not found"}
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def create_solicitation(
    title: str,
    description: str = "",
    program_id: str = "",
    organization_id: str = "",
    solicitation_type: str = "eoi",
    status: str = "draft",
    is_public: bool = False,
    scope_of_work: str = "",
    application_deadline: str = "",
    expected_start_date: str = "",
    expected_end_date: str = "",
    estimated_scale: str = "",
    contact_email: str = "",
) -> dict:
    """Create a new solicitation.

    Args:
        title: Solicitation title (required)
        description: Detailed description
        program_id: Program ID (provide this or organization_id)
        organization_id: Organization ID or slug (alternative to program_id)
        solicitation_type: "eoi" (Expression of Interest) or "rfp" (Request for Proposal)
        status: Initial status ("draft", "active", "closed")
        is_public: Whether the solicitation is publicly visible
        scope_of_work: Scope of work description
        application_deadline: Deadline for applications (ISO date string, e.g. "2026-04-01")
        expected_start_date: Expected start date (ISO date string)
        expected_end_date: Expected end date (ISO date string)
        estimated_scale: Scale estimate (e.g. "1000 beneficiaries")
        contact_email: Contact email for inquiries
    """
    from solicitation_tools import create_solicitation as _create

    try:
        data = {
            "title": title,
            "description": description,
            "solicitation_type": solicitation_type,
            "status": status,
            "is_public": is_public,
        }
        # Only include optional fields if provided
        if scope_of_work:
            data["scope_of_work"] = scope_of_work
        if application_deadline:
            data["application_deadline"] = application_deadline
        if expected_start_date:
            data["expected_start_date"] = expected_start_date
        if expected_end_date:
            data["expected_end_date"] = expected_end_date
        if estimated_scale:
            data["estimated_scale"] = estimated_scale
        if contact_email:
            data["contact_email"] = contact_email

        return await _create(
            program_id=program_id or None,
            organization_id=organization_id or None,
            data=data,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def update_solicitation(solicitation_id: int, data_json: str) -> dict:
    """Update an existing solicitation.

    Merges the provided fields into the existing solicitation data.

    Args:
        solicitation_id: The solicitation record ID to update
        data_json: JSON string of fields to update (e.g. '{"status": "active", "title": "New Title"}')
    """
    import json as _json

    from solicitation_tools import update_solicitation as _update

    try:
        update_data = _json.loads(data_json)
        return await _update(solicitation_id, update_data)
    except _json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in data_json: {e}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def list_responses(solicitation_id: int) -> dict:
    """List all responses for a solicitation.

    Args:
        solicitation_id: The solicitation record ID to get responses for
    """
    from solicitation_tools import list_responses as _list

    try:
        results = await _list(solicitation_id)
        return {"responses": results, "count": len(results)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_response(response_id: int) -> dict:
    """Get a single solicitation response by ID.

    Args:
        response_id: The response record ID
    """
    from solicitation_tools import get_response as _get

    try:
        result = await _get(response_id)
        if result is None:
            return {"error": f"Response {response_id} not found"}
        return result
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
