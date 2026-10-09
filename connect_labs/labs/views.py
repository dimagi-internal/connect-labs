import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.views.decorators.http import require_http_methods
from django.views.generic import TemplateView

from connect_labs.labs.context import AUTO_SELECTED_KEY, clear_context_from_session
from connect_labs.labs.integrations.commcare.api_client import is_cchq_oauth_active
from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data, is_connect_oauth_active
from connect_labs.labs.integrations.ocs.api_client import is_ocs_oauth_active
from connect_labs.utils.feature_access import user_has_feature_access


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

    # force_refresh because this view exists purely to defeat staleness -- serving it
    # the cached tree would make the button a no-op. owner= matters just as much: the
    # entry it repopulates is the one the next login reads, and without it the button
    # would refresh this session while leaving every other reader on the stale copy.
    # The token is this session's, so request.user IS its owner.
    org_data = fetch_user_organization_data(access_token, force_refresh=True, owner=request.user.username)

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
    """Embeds the Scout data agent widget via the widget SDK.

    ``scout_url_env`` selects which env var provides the Scout origin, so a single
    view can back multiple routes (e.g. /labs/scout/ → SCOUT_URL, /labs/scout-prod/
    → SCOUT_PROD_URL) without duplicating the embed plumbing.
    """

    template_name = "labs/scout.html"
    scout_url_env = "SCOUT_URL"
    scout_url_default = "http://localhost:5173"

    def get_context_data(self, **kwargs):
        from .context import extract_context_from_session

        ctx = super().get_context_data(**kwargs)
        # Strip trailing slash — template adds slashes where needed
        ctx["scout_url"] = os.environ.get(self.scout_url_env, self.scout_url_default).rstrip("/")
        # Pass the current labs opportunity as the Scout tenant
        labs_ctx = extract_context_from_session(self.request)
        ctx["opportunity_id"] = labs_ctx.get("opportunity_id", "")
        return ctx


class ScoutProdEmbedView(ScoutEmbedView):
    """Embeds the production Scout deployment (scout.dimagi.com) for side-by-side testing."""

    scout_url_env = "SCOUT_PROD_URL"
    scout_url_default = "https://scout.dimagi.com"


class StatusView(LoginRequiredMixin, TemplateView):
    """Status page that tests key URLs against the production API."""

    template_name = "labs/status.html"


class LabsDocsView(LoginRequiredMixin, TemplateView):
    """
    Landing page for project-wise technical documentation.

    Accessible to any logged-in user regardless of program or opportunity context.
    """

    template_name = "labs/docs/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["doc_projects"] = [
            {
                "name": "Labs Help",
                "url": "/labs/docs/help/",
                "icon": "fa-circle-question",
                "description": "How to use Labs: workflows, audits, tasks, and building reports with Claude",
                "color": "blue",
            },
            {
                "name": "CHC",
                "url": "/labs/docs/chc/",
                "icon": "fa-hand-holding-medical",
                "description": "Connect-CHC system documentation: architecture, apps, workflows, and deployment",
                "color": "teal",
            },
        ]
        return context


class LabsDocsCHCView(LoginRequiredMixin, TemplateView):
    """Connect-CHC system documentation — standalone page served as-is."""

    template_name = "labs/docs/chc.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Keys the comment thread (see docs_comment_views.ALLOWED_DOC_KEYS).
        context["doc_key"] = "chc"
        return context


class LabsOverviewView(LoginRequiredMixin, TemplateView):
    """
    Main landing page for labs projects.

    Shows all available labs projects and custom analysis tools in a card-based layout.
    This page is the default landing for users who log into labs without a specific URL redirect.
    """

    template_name = "labs/overview.html"

    def get(self, request, *args, **kwargs):
        # An organisation coming in -- this page with the organisation NAMED in its
        # address (the context picker, a sign-in `next=`) -- lands on the page its
        # Settings name as home. Only when named: a session that merely remembers an
        # organisation still reaches the overview. Design: the scope-config spec,
        # "An organisation coming in".
        slug = request.GET.get("organization_id")
        auto = (getattr(request, "session", None) or {}).get(AUTO_SELECTED_KEY)
        if (
            slug
            and (getattr(request, "labs_context", None) or {}).get("organization_slug") == slug
            # Chosen FOR the person (they hold only this one): not them coming in for it.
            and auto != {"organization_id": slug}
        ):
            from connect_labs.scope_config.scopes import Scope
            from connect_labs.workflow.page_views import resolve_home

            try:
                home = resolve_home(request, Scope.of("organization", slug))
            except ValueError:
                home = None
            # Only to a home that would open: a fill whose owner this person may use.
            if home:
                return HttpResponseRedirect(f"/labs/p/org/{slug}/")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # OAuth status for each provider. Each helper attempts a silent
        # refresh via the stored refresh_token before reporting the session
        # dead — a raw expires_at check (what this used to do inline) reports
        # a merely time-expired token as disconnected even when a refresh
        # would have worked, forcing a needless re-authorize click on this,
        # the main Labs landing page.
        context["connect_oauth_active"] = is_connect_oauth_active(self.request)
        context["commcare_oauth_active"] = is_cchq_oauth_active(self.request)
        context["ocs_oauth_active"] = is_ocs_oauth_active(self.request)

        # Labs context status
        labs_context = getattr(self.request, "labs_context", {}) or {}
        context["has_labs_context"] = bool(labs_context)

        _has_access = lambda feat: user_has_feature_access(self.request.user, feat)  # noqa: E731

        # ── Labs projects ──────────────────────────────────────────────────────
        _all_labs_projects = [
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
                "name": "Documentations",
                "url": "/labs/docs/",
                "icon": "fa-book",
                "description": "Project-wise technical documentation, open to everyone "
                "regardless of program or opportunity context",
                "color": "teal",
                "feature": "docs",
            },
            {
                "name": "Labs Admin",
                "url": "/labs/admin/",
                "icon": "fa-screwdriver-wrench",
                "description": "Internal tools: inspect records & visits, manage cache & jobs, load boundary data",
                "color": "rose",
                "feature": "admin",
            },
        ]
        context["labs_projects"] = [p for p in _all_labs_projects if _has_access(p["feature"])]

        # ── Custom Analysis projects — Dimagi staff only ──────────────────────
        _all_custom_analysis_projects = [
            {
                "name": "Coverage",
                "url": "/coverage/",
                "icon": "fa-map-marked-alt",
                "description": "Geographic coverage analysis and mapping for Service Areas and Delivery Units",
                "color": "green",
            },
            {
                "name": "KMC Timeline",
                "url": "/custom_analysis/kmc/children/",
                "icon": "fa-baby",
                "description": "Individual child timelines for Kangaroo Mother Care programs",
                "color": "blue",
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
        context["custom_analysis_projects"] = _all_custom_analysis_projects if _has_access("custom_analysis") else []

        return context
