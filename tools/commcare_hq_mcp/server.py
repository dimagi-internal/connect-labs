"""CommCare HQ MCP Server.

Provides CommCare application structure context for Claude Code sessions.
Tools let you explore app modules, form questions, and JSON field paths
for building workflow pipeline schemas.

Auth is automatic:
- CommCare HQ: reads COMMCARE_USERNAME + COMMCARE_API_KEY from project .env
- Connect API: reads CLI OAuth token from ~/.commcare-connect/token.json

Usage (stdio, for Claude Code):
    python tools/commcare_hq_mcp/server.py
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
        "get_form_json_paths to drill into specific forms."
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
async def get_opportunity_url(opportunity_id: int) -> dict:
    """Get the full production Connect URL for an opportunity.

    Looks up the opportunity's org_slug and returns the URL you can use
    to view or manage the opportunity in the Connect web interface.

    Args:
        opportunity_id: The Connect opportunity ID (e.g., 874)
    """
    from connect_client import CONNECT_URL
    from connect_client import get_opportunity_apps as _get_opp

    try:
        opp = await _get_opp(opportunity_id)
        org_slug = opp.get("org_slug", "")
        if not org_slug:
            return {"error": f"Opportunity {opportunity_id} has no organization slug"}
        url = f"{CONNECT_URL}/a/{org_slug}/opportunity/{opportunity_id}/"
        return {
            "opportunity_id": opportunity_id,
            "opportunity_name": opp.get("opportunity_name", ""),
            "org_slug": org_slug,
            "url": url,
        }
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


if __name__ == "__main__":
    mcp.run(transport="stdio")
