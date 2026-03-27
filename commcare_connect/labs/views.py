import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.views.decorators.http import require_http_methods
from django.views.generic import TemplateView

from commcare_connect.labs.context import clear_context_from_session
from commcare_connect.labs.integrations.connect.oauth import fetch_user_organization_data
from commcare_connect.utils.feature_access import user_has_feature_access


@login_required
@require_http_methods(["POST"])
def clear_context(request):
    """Clear the labs context from session and redirect back."""
    clear_context_from_session(request)

    # Redirect to the referrer or labs overview
    redirect_url = request.headers.get("referer", "/labs/overview/")

    # Remove any context params from the redirect URL
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    parsed = urlparse(redirect_url)
    query_params = parse_qs(parsed.query)

    # Remove context parameters
    query_params.pop("organization_id", None)
    query_params.pop("program_id", None)
    query_params.pop("opportunity_id", None)
    query_params.pop("clear_context", None)

    # Rebuild URL
    new_query = urlencode(query_params, doseq=True)
    new_parsed = parsed._replace(query=new_query)
    redirect_url = urlunparse(new_parsed)

    return HttpResponseRedirect(redirect_url)


@require_http_methods(["POST"])
def refresh_org_data(request):
    """Refresh organization data from Connect API."""
    if not request.user.is_authenticated:
        messages.error(request, "You must be logged in to refresh organization data.")
        return HttpResponseRedirect(request.headers.get("referer", "/"))

    # Get OAuth token from session
    labs_oauth = request.session.get("labs_oauth")
    if not labs_oauth or "access_token" not in labs_oauth:
        messages.error(request, "No OAuth token found. Please log in again.")
        return HttpResponseRedirect("/labs/login/")

    access_token = labs_oauth["access_token"]

    # Fetch fresh organization data
    org_data = fetch_user_organization_data(access_token)

    if org_data:
        # Update session with fresh data
        labs_oauth["organization_data"] = org_data
        request.session["labs_oauth"] = labs_oauth
        request.session.modified = True

        messages.success(
            request,
            f"Successfully refreshed organization data: "
            f"{len(org_data.get('organizations', []))} orgs, "
            f"{len(org_data.get('programs', []))} programs, "
            f"{len(org_data.get('opportunities', []))} opportunities.",
        )
    else:
        messages.error(
            request,
            "Failed to refresh organization data. The Connect API may be slow or unavailable. "
            "Please try again in a moment.",
        )

    # Redirect back to referrer
    return HttpResponseRedirect(request.headers.get("referer", "/"))


class ScoutEmbedView(LoginRequiredMixin, TemplateView):
    """Embeds the Scout data agent widget via the widget SDK."""

    template_name = "labs/scout.html"

    def get_context_data(self, **kwargs):
        from .context import extract_context_from_session

        ctx = super().get_context_data(**kwargs)
        # Strip trailing slash — template adds slashes where needed
        ctx["scout_url"] = os.environ.get("SCOUT_URL", "http://localhost:5173").rstrip("/")
        # Pass the current labs opportunity as the Scout tenant
        labs_ctx = extract_context_from_session(self.request)
        ctx["opportunity_id"] = labs_ctx.get("opportunity_id", "")
        return ctx


class StatusView(LoginRequiredMixin, TemplateView):
    """Status page that tests key URLs against the production API."""

    template_name = "labs/status.html"


class LabsOverviewView(LoginRequiredMixin, TemplateView):
    """
    Main landing page for labs projects.

    Shows all available labs projects and custom analysis tools in a card-based layout.
    This page is the default landing for users who log into labs without a specific URL redirect.
    """

    template_name = "labs/overview.html"

    def get_context_data(self, **kwargs):
        from django.utils import timezone

        context = super().get_context_data(**kwargs)

        # Connect OAuth status
        labs_oauth = self.request.session.get("labs_oauth", {})
        connect_expires_at = labs_oauth.get("expires_at", 0)
        context["connect_oauth_active"] = bool(
            labs_oauth.get("access_token") and timezone.now().timestamp() < connect_expires_at
        )

        # CommCare OAuth status
        commcare_oauth = self.request.session.get("commcare_oauth", {})
        commcare_expires_at = commcare_oauth.get("expires_at", 0)
        context["commcare_oauth_active"] = bool(
            commcare_oauth.get("access_token") and timezone.now().timestamp() < commcare_expires_at
        )

        # Open Chat Studio OAuth status
        ocs_oauth = self.request.session.get("ocs_oauth", {})
        ocs_expires_at = ocs_oauth.get("expires_at", 0)
        context["ocs_oauth_active"] = bool(
            ocs_oauth.get("access_token") and timezone.now().timestamp() < ocs_expires_at
        )

        # Labs context status
        labs_context = getattr(self.request, "labs_context", {}) or {}
        context["has_labs_context"] = bool(labs_context)

        # Build Coverage button URL using current labs context opportunity
        opportunity_id = labs_context.get("opportunity_id")
        if opportunity_id:
            coverage_url = f"/coverage/?opportunity_id={opportunity_id}&config=chc_nutrition"
        else:
            coverage_url = "/coverage/?config=chc_nutrition"

        user = self.request.user
        _has_access = lambda feature: user_has_feature_access(user, feature)  # noqa: E731

        # ── Labs projects ──────────────────────────────────────────────────────
        # Each card is shown only if the user has access to that feature.
        _all_labs_projects = [
            {
                "name": "Audit",
                "url": "/audit/",
                "icon": "fa-clipboard-check",
                "description": "Data quality auditing tools for program monitoring",
                "color": "blue",
                "feature": "audit",
            },
            {
                "name": "Solicitations",
                "url": "/solicitations/",
                "icon": "fa-file-contract",
                "description": "RFP management system for posting Solicitations and receiving responses",
                "color": "indigo",
                "feature": "solicitations",
            },
            {
                "name": "Tasks",
                "url": "/tasks/",
                "icon": "fa-tasks",
                "description": "Task management and workflow tracking for program managers and network managers",
                "color": "purple",
                "feature": "tasks",
            },
            {
                "name": "Workflows",
                "url": "/labs/workflow/",
                "icon": "fa-diagram-project",
                "description": "Build and run automated data pipelines with AI-powered agents",
                "color": "amber",
                "feature": "workflow",
            },
        ]
        context["labs_projects"] = [p for p in _all_labs_projects if _has_access(p["feature"])]

        # ── Custom Analysis projects — all require the "custom_analysis" feature ──
        _all_custom_analysis_projects = [
            {
                "name": "Coverage",
                "url": "/coverage/",
                "icon": "fa-map-marked-alt",
                "description": "Geographic coverage analysis and mapping for Service Areas and Delivery Units",
                "color": "green",
                "buttons": [
                    {"label": "CHC Nutrition View", "url": coverage_url},
                    {"label": "Generic View", "url": "/coverage/"},
                ],
            },
            {
                "name": "CHC Nutrition",
                "url": "/custom_analysis/chc_nutrition/",
                "icon": "fa-heartbeat",
                "description": "Nutrition and health metrics analysis for the Child Health Campaign",
                "color": "rose",
            },
            {
                "name": "KMC Timeline",
                "url": "/custom_analysis/kmc/children/",
                "icon": "fa-baby",
                "description": "Individual child timelines for Kangaroo Mother Care programs",
                "color": "blue",
            },
            {
                "name": "MBW GPS Analysis",
                "url": "/custom_analysis/mbw/gps/",
                "icon": "fa-location-dot",
                "description": "GPS distance metrics and travel analysis for Mother Baby Wellness",
                "color": "emerald",
            },
            {
                "name": "RUTF Timeline",
                "url": "/custom_analysis/rutf/children/",
                "icon": "fa-weight-scale",
                "description": "SAM follow-up tracking with MUAC measurements for malnutrition programs",
                "color": "orange",
            },
            {
                "name": "Audit of Audits",
                "url": "/custom_analysis/audit_of_audits/",
                "icon": "fa-magnifying-glass-chart",
                "description": "Cross-opportunity admin report of all workflow runs and audit sessions",
                "color": "purple",
            },
        ]
        context["custom_analysis_projects"] = (
            _all_custom_analysis_projects if _has_access("custom_analysis") else []
        )

        return context
