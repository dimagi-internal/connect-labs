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
        "update_solicitation, list_responses, get_response, and award_response to manage "
        "solicitations, responses, and awards via the Connect Labs Record API.\n\n"
        "REVIEWS: Use list_reviews, get_review, create_review, and update_review to "
        "manage solicitation response reviews with scores and recommendations.\n\n"
        "FUNDS: Use list_funds, get_fund, create_fund, update_fund, add_fund_allocation, "
        "and remove_fund_allocation to manage funder budgets and allocations. Funds are "
        "scoped by organization_id. Awards can auto-allocate from funds.\n\n"
        "GOOGLE SHEETS: Use read_google_sheet to read data from Google Sheets and "
        "list_sheet_tabs to see available tabs. Requires OAuth login first via "
        "python tools/commcare_mcp/google_auth.py login\n\n"
        "SAMPLE IDS: Use get_sample_ids to discover real fund, solicitation, and "
        "program IDs from the current environment. Useful for constructing valid "
        "localhost URLs or testing API calls without manual lookups."
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
    evaluation_criteria_json: str = "",
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
        evaluation_criteria_json: JSON array of evaluation criteria objects
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
        if evaluation_criteria_json:
            import json as _json

            data["evaluation_criteria"] = _json.loads(evaluation_criteria_json)

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


# --- Award Tool ---


@mcp.tool()
async def award_response(
    response_id: int,
    reward_budget: int,
    org_id: str,
    fund_id: int = 0,
) -> dict:
    """Award a solicitation response with a budget, and optionally allocate from a fund.

    Marks the response as "awarded" and sets the reward_budget and org_id.
    If fund_id is provided, also creates an allocation entry on that fund.

    Args:
        response_id: The response record ID to award
        reward_budget: Budget amount to award
        org_id: Organization ID receiving the award
        fund_id: Fund ID to allocate from (optional — 0 means no fund allocation)
    """
    from solicitation_tools import award_response as _award

    try:
        return await _award(
            response_id=response_id,
            reward_budget=reward_budget,
            org_id=org_id,
            fund_id=fund_id if fund_id else None,
        )
    except Exception as e:
        return {"error": str(e)}


# --- Fund Tools ---


@mcp.tool()
async def list_funds(program_id: str) -> dict:
    """List all funds accessible via a program.

    Args:
        program_id: Program ID for ACL scoping
    """
    from fund_tools import list_funds as _list

    try:
        results = await _list(program_id)
        return {"funds": results, "count": len(results)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_fund(fund_id: int) -> dict:
    """Get a single fund by ID, including its allocations.

    Args:
        fund_id: The fund record ID
    """
    from fund_tools import get_fund as _get

    try:
        result = await _get(fund_id)
        if result is None:
            return {"error": f"Fund {fund_id} not found"}
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def create_fund(
    program_id: str,
    name: str,
    total_budget: float = 0,
    currency: str = "USD",
    description: str = "",
    program_ids_json: str = "",
    delivery_types_json: str = "",
    status: str = "active",
) -> dict:
    """Create a new fund, scoped by program for access control.

    Args:
        program_id: Program ID for ACL scoping (the primary program this fund is associated with)
        name: Fund/funder name (e.g. "ECF", "GiveWell")
        total_budget: Total budget amount (default 0)
        currency: Currency code (default "USD")
        description: Fund description
        program_ids_json: JSON array of ALL program IDs this fund covers (e.g. '[46, 68]')
        delivery_types_json: JSON array of delivery types (e.g. '["CHC", "MBW"]')
        status: Fund status ("active", "closed")
    """
    import json as _json

    from fund_tools import create_fund as _create

    try:
        program_ids = _json.loads(program_ids_json) if program_ids_json else None
        delivery_types = _json.loads(delivery_types_json) if delivery_types_json else None

        return await _create(
            program_id=program_id,
            name=name,
            total_budget=total_budget or None,
            currency=currency,
            description=description,
            program_ids=program_ids,
            delivery_types=delivery_types,
            status=status,
        )
    except _json.JSONDecodeError as e:
        return {"error": f"Invalid JSON: {e}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def update_fund(fund_id: int, data_json: str) -> dict:
    """Update an existing fund. Merges provided fields into existing data.

    Args:
        fund_id: The fund record ID to update
        data_json: JSON string of fields to update (e.g. '{"status": "closed", "total_budget": 500000}')
    """
    import json as _json

    from fund_tools import update_fund as _update

    try:
        update_data = _json.loads(data_json)
        return await _update(fund_id, update_data)
    except _json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in data_json: {e}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def add_fund_allocation(fund_id: int, allocation_json: str) -> dict:
    """Add an allocation entry to a fund.

    Args:
        fund_id: The fund record ID
        allocation_json: JSON string of the allocation object, e.g.:
            '{"amount": 100000, "type": "award", "org_id": "99", "org_name": "PPFN", "notes": "CHC award"}'
    """
    import json as _json

    from fund_tools import add_fund_allocation as _add

    try:
        allocation = _json.loads(allocation_json)
        return await _add(fund_id, allocation)
    except _json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in allocation_json: {e}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def remove_fund_allocation(fund_id: int, index: int) -> dict:
    """Remove an allocation entry from a fund by index.

    Args:
        fund_id: The fund record ID
        index: Zero-based index of the allocation to remove
    """
    from fund_tools import remove_fund_allocation as _remove

    try:
        return await _remove(fund_id, index)
    except Exception as e:
        return {"error": str(e)}


# --- Review Tools ---


@mcp.tool()
async def list_reviews(response_id: int) -> dict:
    """List all reviews for a solicitation response.

    Args:
        response_id: The response record ID to get reviews for
    """
    from review_tools import list_reviews as _list

    try:
        results = await _list(response_id)
        return {"reviews": results, "count": len(results)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_review(review_id: int) -> dict:
    """Get a single review by ID.

    Args:
        review_id: The review record ID
    """
    from review_tools import get_review as _get

    try:
        result = await _get(review_id)
        if result is None:
            return {"error": f"Review {review_id} not found"}
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def create_review(
    response_id: int,
    llo_entity_id: str,
    score: int = 0,
    recommendation: str = "under_review",
    notes: str = "",
    criteria_scores_json: str = "",
    reviewer_username: str = "",
    tags: str = "",
) -> dict:
    """Create a review for a solicitation response.

    Args:
        response_id: The response record ID being reviewed
        llo_entity_id: LLO entity ID (required for API scoping)
        score: Overall score 1-100 (0 means not set)
        recommendation: "under_review", "approved", "rejected", "needs_revision"
        notes: Reviewer notes
        criteria_scores_json: JSON dict of criterion_id -> score (1-10), e.g. '{"crit_1": 8, "crit_2": 6}'
        reviewer_username: Username of the reviewer
        tags: Comma-separated tags
    """
    import json as _json

    from review_tools import create_review as _create

    try:
        criteria_scores = _json.loads(criteria_scores_json) if criteria_scores_json else None

        return await _create(
            response_id=response_id,
            llo_entity_id=llo_entity_id,
            score=score if score else None,
            recommendation=recommendation,
            notes=notes,
            criteria_scores=criteria_scores,
            reviewer_username=reviewer_username,
            tags=tags,
        )
    except _json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in criteria_scores_json: {e}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def update_review(review_id: int, data_json: str) -> dict:
    """Update an existing review. Merges provided fields into existing data.

    Args:
        review_id: The review record ID to update
        data_json: JSON string of fields to update (e.g. '{"score": 85, "recommendation": "approved"}')
    """
    import json as _json

    from review_tools import update_review as _update

    try:
        update_data = _json.loads(data_json)
        return await _update(review_id, update_data)
    except _json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in data_json: {e}"}
    except Exception as e:
        return {"error": str(e)}


# --- Google Sheets Tools ---


@mcp.tool()
async def read_google_sheet(
    url: str,
    tab_name: str = "",
    cell_range: str = "",
) -> dict:
    """Read data from a Google Sheet by URL.

    Returns headers and rows as dicts. Requires Google OAuth login first
    (run: python tools/commcare_mcp/google_auth.py login).

    Args:
        url: Google Sheets URL (e.g. https://docs.google.com/spreadsheets/d/...)
             or just the spreadsheet ID
        tab_name: Sheet tab name (optional — auto-detected from URL gid or uses first tab)
        cell_range: A1 notation range like "A1:D10" (optional — reads all data if empty)
    """
    from google_tools import read_google_sheet as _read

    try:
        return await _read(url, tab_name=tab_name, cell_range=cell_range)
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def list_sheet_tabs(url: str) -> dict:
    """List all tabs in a Google Sheet.

    Args:
        url: Google Sheets URL or spreadsheet ID
    """
    from google_tools import list_sheet_tabs as _list

    try:
        return await _list(url)
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


# --- Sample IDs Tool ---


@mcp.tool()
async def get_sample_ids() -> dict:
    """Get a small set of real fund, solicitation, and program IDs from the current environment.

    Returns IDs and human-readable names for each category (up to 5 per category).
    Use this to discover valid IDs for constructing localhost URLs or testing
    API calls without needing manual curl commands.

    Returns:
        {
            "funds": [{"id": 123, "name": "ECF"}, ...],
            "solicitations": [{"id": 456, "name": "CHC EOI Nigeria"}, ...],
            "programs": [{"id": 42, "name": "CHC Nigeria"}, ...],
        }
    """
    from sample_ids_tools import get_sample_ids as _get

    try:
        return await _get()
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
