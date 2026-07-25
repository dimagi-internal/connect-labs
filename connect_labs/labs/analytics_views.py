"""Labs-embedded analytics dashboard.

Surfaces the self-hosted Umami stats inside labs so viewers ride labs' own
Connect OAuth — no second login. The Umami admin UI at /umami/ remains for
deep exploration and configuration.
"""
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db.models import Count
from django.utils import timezone
from django.views.generic import TemplateView

from connect_labs.audit_trail import service as audit_service
from connect_labs.audit_trail.models import Action as AuditAction
from connect_labs.labs.view_mixins import AdminRequiredMixin
from connect_labs.utils import umami_api

logger = logging.getLogger(__name__)


def _ms(dt) -> int:
    return int(dt.timestamp() * 1000)


class UmamiSSOView(AdminRequiredMixin, TemplateView):
    """One-click bridge into the full Umami UI, gated by labs OAuth.

    Umami shares labs' origin (path-based at /umami), so this view mints the
    admin JWT server-side and hands it to the browser's localStorage — no
    separate Umami login for staff. Access is audited: inside Umami everyone
    is the shared admin identity, so who-opened-it lives in our trail.
    """

    template_name = "labs/umami_sso.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        audit_service.record(AuditAction.READ, resource_type="umami_dashboard")
        try:
            context["umami_token"] = umami_api.get_admin_token()
            context["umami_target"] = f"/umami/websites/{settings.UMAMI_WEBSITE_ID}"
        except umami_api.UmamiAPIError as e:
            logger.warning("Umami SSO unavailable: %s", e)
            context["sso_error"] = str(e)
        return context


class AnalyticsDashboardView(AdminRequiredMixin, TemplateView):
    template_name = "labs/analytics_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # The analytics store is PHI-adjacent usage metadata (who used which
        # feature, opaque scopes) — viewing it is itself an audited access.
        audit_service.record(AuditAction.READ, resource_type="analytics_dashboard")
        context["umami_url"] = "/umami"

        # Top pages by FULL URL (path + redacted query) — aggregated from the
        # audit trail's page_view events rather than Umami, because Umami
        # splits path/query at ingest and can't aggregate the combination.
        # Parameters are where labs pages carry their meaning (scope ids,
        # filters), so distinct parameter sets count as distinct pages here.
        # Independent of Umami config, so it renders even when Umami is down.
        from connect_labs.audit_trail.models import AuditEvent

        context["top_full_urls"] = list(
            AuditEvent.objects.filter(
                action=AuditAction.PAGE_VIEW,
                occurred_at__gte=timezone.now() - timedelta(days=7),
                labs_only=False,
            )
            .values("path", "query_string")
            .annotate(n=Count("id"))
            .order_by("-n")[:12]
        )

        if not umami_api.is_configured():
            context[
                "analytics_error"
            ] = "Umami is not configured (UMAMI_HOST_URL / UMAMI_WEBSITE_ID / UMAMI_ADMIN_PASSWORD)."
            return context

        now = timezone.now()
        now_ms = int(time.time() * 1000)
        windows = {
            "24h": _ms(now - timedelta(hours=24)),
            "7d": _ms(now - timedelta(days=7)),
            "30d": _ms(now - timedelta(days=30)),
        }
        try:
            context["stats"] = {label: umami_api.website_stats(start, now_ms) for label, start in windows.items()}
            context["active_now"] = umami_api.active_visitors()
            series = umami_api.pageviews_series(windows["30d"], now_ms, unit="day")
            context["series_pageviews"] = series.get("pageviews", [])
            context["series_sessions"] = series.get("sessions", [])
            context["top_events"] = umami_api.metrics("event", windows["7d"], now_ms, limit=12)
            context["top_browsers"] = umami_api.metrics("browser", windows["7d"], now_ms, limit=6)
        except umami_api.UmamiAPIError as e:
            logger.warning("Analytics dashboard degraded: %s", e)
            context["analytics_error"] = str(e)
        return context
