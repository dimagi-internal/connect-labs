"""
Views for the Audit of Audits admin report.

Access is restricted to users with @dimagi.com email addresses via
DimagiUserRequiredMixin. This report is intentionally not visible to
normal users (Network Managers, FLWs, etc.) — the tile is also hidden
from the overview page for non-@dimagi.com users.
"""

import logging

from django.contrib.auth.mixins import AccessMixin, LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.views.generic import TemplateView

from commcare_connect.labs.context import get_org_data
from commcare_connect.labs.integrations.connect.api_client import LabsAPIError
from commcare_connect.utils.dimagi_user import is_dimagi_user
from commcare_connect.workflow.templates import list_templates

from .data_access import AuditOfAuditsDataAccess

logger = logging.getLogger(__name__)

DIMAGI_EMAIL_DOMAIN = "@dimagi.com"

# Session key used to cache discovered template types across config page visits.
# After each report run the view stores the union of all seen template types so
# the config page can show them as filter options on the next visit.
SESSION_KEY_TEMPLATE_TYPES = "aoa_known_template_types"


def _registry_template_labels() -> dict[str, str]:
    """Return all registered workflow template types as {key: display_name}.

    Reads from the live workflow template registry so any newly added
    template automatically appears in the Audit of Audits config page.
    """
    try:
        return {t["key"]: t["name"] for t in list_templates()}
    except Exception:
        logger.warning("[AuditOfAudits] Could not load workflow template registry", exc_info=True)
        return {}


def _dimagi_display_name(user) -> str:
    """Return the best available identifier to display for the current user."""
    email = getattr(user, "email", "") or ""
    if email.endswith(DIMAGI_EMAIL_DOMAIN):
        return email
    username = getattr(user, "username", "") or ""
    return username or email


class DimagiUserRequiredMixin(AccessMixin):
    """
    Restricts view access to users with a @dimagi.com email or username.

    Checks both .email and .username because CommCare Connect OAuth profiles
    store the email address in the username field (e.g. mtheis@dimagi.com)
    while the .email field may be blank in the OAuth user_profile payload.

    Unauthenticated users are redirected to login (via handle_no_permission).
    Authenticated non-@dimagi.com users receive a 403 PermissionDenied.
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not is_dimagi_user(request.user):
            raise PermissionDenied("This report is restricted to Dimagi staff.")
        return super().dispatch(request, *args, **kwargs)


class AuditOfAuditsView(LoginRequiredMixin, DimagiUserRequiredMixin, TemplateView):
    """
    Two-mode view for the Audit of Audits admin report.

    Mode 1 — Config (no ?org_ids= in GET):
        Renders config.html showing an org multi-select form.  No API calls.

    Mode 2 — Report (?org_ids=1&org_ids=2 in GET):
        Runs the two-phase API fetch scoped to the selected organizations,
        then renders report.html with the results.

    Restricted to @dimagi.com / LABS_ADMIN_USERNAMES users only.
    """

    def get_template_names(self):
        if self.request.GET.getlist("org_ids"):
            return ["custom_analysis/audit_of_audits/report.html"]
        return ["custom_analysis/audit_of_audits/config.html"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Always available: user identity + full org list for the config form
        org_data = get_org_data(self.request)
        user_orgs: list[dict] = [o for o in org_data.get("organizations", []) if isinstance(o.get("id"), int)]
        user_opps: list[dict] = org_data.get("opportunities", []) or []

        context["user_email"] = _dimagi_display_name(self.request.user)
        context["user_username"] = getattr(self.request.user, "username", "") or ""
        context["user_orgs"] = user_orgs

        # ── Config mode: no org selection yet — just show the form ───────────
        selected_org_id_strs: list[str] = self.request.GET.getlist("org_ids")
        if not selected_org_id_strs:
            # Build template type options from the live workflow template registry.
            # Merge with session cache to include any types discovered at runtime
            # that may not yet have a registered template module.
            registry_labels = _registry_template_labels()
            cached_types: list[str] = self.request.session.get(SESSION_KEY_TEMPLATE_TYPES, [])
            all_keys: list[str] = sorted(set(registry_labels.keys()) | set(cached_types))
            context["available_template_types"] = [
                {
                    "value": k,
                    "label": registry_labels.get(k, k.replace("_", " ").title()),
                }
                for k in all_keys
            ]
            return context

        # ── Report mode ───────────────────────────────────────────────────────
        labs_oauth = self.request.session.get("labs_oauth", {})
        access_token = labs_oauth.get("access_token")

        if not access_token:
            context["error"] = "No OAuth token found. Please log in to Connect Labs first."
            context["rows"] = []
            context["total_runs"] = 0
            context["completed_run_count"] = 0
            context["filter_type"] = ""
            context["selected_org_ids"] = []
            return context

        selected_org_ids: list[int] = [int(x) for x in selected_org_id_strs if x.isdigit()]

        # Template type filter — empty list means "all types" (no filter applied).
        selected_template_types: list[str] = self.request.GET.getlist("template_types")
        template_types_filter: list[str] | None = selected_template_types if selected_template_types else None

        # Filter opportunities via the org → program → opportunity chain:
        #   org.slug  →  program.organization  →  program.id  →  opp.program
        # This mirrors the context selector's cascading filter logic.
        selected_org_id_set: set[int] = set(selected_org_ids)
        selected_org_slugs: set[str] = {
            o["slug"] for o in user_orgs if o.get("id") in selected_org_id_set and o.get("slug")
        }

        user_programs: list[dict] = org_data.get("programs", []) or []
        selected_program_ids: set = {
            p["id"] for p in user_programs if p.get("organization") in selected_org_slugs and p.get("id") is not None
        }

        if selected_program_ids:
            filtered_opps = [
                o for o in user_opps if isinstance(o.get("id"), int) and o.get("program") in selected_program_ids
            ]
        elif selected_org_slugs:
            # Org slugs resolved but no programs matched — org may legitimately have no programs.
            # Fall back to all opportunities so the report remains usable.
            logger.warning(
                "[AuditOfAudits] No programs found for org slugs %s "
                "(user has %d programs). Falling back to all opportunities.",
                sorted(selected_org_slugs),
                len(user_programs),
            )
            filtered_opps = [o for o in user_opps if isinstance(o.get("id"), int)]
        else:
            # Selected org IDs didn't resolve to any known org in this session.
            # Return empty rather than leaking all opportunities.
            logger.warning(
                "[AuditOfAudits] Selected org IDs %s not found in user orgs (session may be stale).",
                selected_org_ids,
            )
            filtered_opps = []

        opportunity_ids: list[int] = [o["id"] for o in filtered_opps]
        opp_name_map: dict[int, str] = {o["id"]: o.get("name", "") for o in filtered_opps}

        # Map org id → org name for the report header summary
        org_name_map: dict[int, str] = {o["id"]: o.get("name", "") for o in user_orgs}
        selected_orgs_display: list[str] = [org_name_map.get(oid, str(oid)) for oid in selected_org_ids]

        logger.info(
            "[AuditOfAudits] User %s — selected %d/%d orgs, %d opportunities",
            _dimagi_display_name(self.request.user),
            len(selected_org_ids),
            len(user_orgs),
            len(opportunity_ids),
        )

        rows = []
        error = None

        if not selected_org_ids and not opportunity_ids:
            error = "No organizations or opportunities found. Try refreshing your session via the Labs home page."

        if not error:
            try:
                with AuditOfAuditsDataAccess(
                    access_token=access_token,
                    organization_ids=selected_org_ids,
                    opportunity_ids=opportunity_ids,
                    template_types=template_types_filter,
                ) as da:
                    rows = da.build_report_data()

                # Annotate each row with the human-readable opportunity name
                for row in rows:
                    opp_id = row.get("opportunity_id")
                    row["opportunity_name"] = opp_name_map.get(opp_id, "") if opp_id else ""

                # Cache all discovered template types in the session so the config
                # page can show them as filter options on the next visit.
                discovered_types = sorted({r["template_type"] for r in rows if r["template_type"]})
                previously_known = self.request.session.get(SESSION_KEY_TEMPLATE_TYPES, [])
                all_known = sorted(set(discovered_types) | set(previously_known))
                if all_known:
                    self.request.session[SESSION_KEY_TEMPLATE_TYPES] = all_known

            except LabsAPIError as e:
                logger.error("[AuditOfAudits] API error: %s", e, exc_info=True)
                error = f"Failed to load data from Connect API: {e}"
            except Exception as e:
                logger.error("[AuditOfAudits] Unexpected error: %s", e, exc_info=True)
                error = "An unexpected error occurred while loading the report."

        # filter_type is kept only to set the select's initial value on page load.
        # Actual filtering is done client-side in JS — no server round-trip needed.
        filter_type = self.request.GET.get("template_type", "").strip()

        # Build template type label map from registry for the filter dropdown
        registry_labels = _registry_template_labels()

        # Collect unique template types from rows for the filter dropdown,
        # using human-readable labels from the registry where available.
        all_template_types = [
            {"value": t, "label": registry_labels.get(t, t.replace("_", " ").title())}
            for t in sorted({r["template_type"] for r in rows if r["template_type"]})
        ]

        context.update(
            {
                "rows": rows,
                "total_runs": len(rows),
                "completed_run_count": sum(1 for r in rows if r["status"] == "completed"),
                "filter_type": filter_type,
                "all_template_types": all_template_types,
                "registry_labels": registry_labels,
                "selected_org_ids": selected_org_ids,
                "selected_orgs_display": selected_orgs_display,
                "error": error,
            }
        )
        return context
