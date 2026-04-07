"""
Labs Dashboard Prototype Views

Multiple visualization approaches for hierarchical program data:
- Program Type → Program → Opportunity → FLWs
"""
import json
import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render

logger = logging.getLogger(__name__)


def dashboard_2(request: HttpRequest) -> HttpResponse:
    """Dashboard prototype 2: Accordion/Collapsible Cards View."""
    if not request.user.is_authenticated:
        return redirect("labs:login")

    # Get programs and opportunities
    programs = request.user.programs if hasattr(request.user, "programs") else []
    opportunities = request.user.opportunities if hasattr(request.user, "opportunities") else []

    # Log for debugging
    logger.info(f"Dashboard 2 - Programs: {len(programs)}, Opportunities: {len(opportunities)}")
    if programs:
        logger.debug(f"First program: {programs[0]}")
    if opportunities:
        logger.debug(f"First opportunity: {opportunities[0]}")

    context = {
        "user": request.user,
        "connect_url": settings.CONNECT_PRODUCTION_URL,
        "programs_json": json.dumps(programs),
        "opportunities_json": json.dumps(opportunities),
    }

    return render(request, "labs/dashboard-2.html", context)


def dashboard_3(request: HttpRequest) -> HttpResponse:
    """Dashboard prototype 3: Interactive Tree/Sidebar Navigation."""
    if not request.user.is_authenticated:
        return redirect("labs:login")

    context = {
        "user": request.user,
        "connect_url": settings.CONNECT_PRODUCTION_URL,
        "programs_json": json.dumps(request.user.programs),
        "opportunities_json": json.dumps(request.user.opportunities),
    }

    return render(request, "labs/dashboard-3.html", context)


def dashboard_4(request: HttpRequest) -> HttpResponse:
    """Dashboard prototype 4: Drill-Down Table with Breadcrumbs."""
    if not request.user.is_authenticated:
        return redirect("labs:login")

    context = {
        "user": request.user,
        "connect_url": settings.CONNECT_PRODUCTION_URL,
        "programs_json": json.dumps(request.user.programs),
        "opportunities_json": json.dumps(request.user.opportunities),
    }

    return render(request, "labs/dashboard-4.html", context)


def fetch_flws(request: HttpRequest, opp_id: int) -> JsonResponse:
    """
    Fetch FLW (Field Worker) data for a specific opportunity.

    This is a server-side proxy endpoint that:
    1. Uses the OAuth token from session (not exposed to client)
    2. Calls production API to get FLW data
    3. Returns JSON to client

    Args:
        request: HTTP request with authenticated session
        opp_id: Opportunity ID to fetch FLW data for

    Returns:
        JsonResponse with FLW list or error
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    # Get OAuth token from session
    labs_oauth = request.session.get("labs_oauth")
    if not labs_oauth or "access_token" not in labs_oauth:
        return JsonResponse({"error": "No OAuth token in session"}, status=401)

    access_token = labs_oauth["access_token"]

    # Call production API
    try:
        from commcare_connect.labs.integrations.connect.export_client import ExportAPIClient, ExportAPIError

        with ExportAPIClient(
            base_url=settings.CONNECT_PRODUCTION_URL,
            access_token=access_token,
            timeout=30.0,
        ) as client:
            flw_data = client.fetch_all(f"/export/opportunity/{opp_id}/user_data/")

        logger.info(f"Fetched {len(flw_data)} FLWs for opportunity {opp_id}")
        return JsonResponse({"flws": flw_data, "opportunity_id": opp_id})

    except ExportAPIError as e:
        logger.error(f"Failed to fetch FLWs for opp {opp_id}: {e}")
        return JsonResponse({"error": str(e)}, status=502)
