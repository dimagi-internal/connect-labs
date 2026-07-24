"""Labs-embedded analytics dashboard.

Surfaces the self-hosted Umami stats inside labs so viewers ride labs' own
Connect OAuth — no second login. The Umami admin UI at /umami/ remains for
deep exploration and configuration.
"""
import logging
import time
from datetime import timedelta

from django.utils import timezone
from django.views.generic import TemplateView

from connect_labs.labs.view_mixins import AdminRequiredMixin
from connect_labs.utils import umami_api

logger = logging.getLogger(__name__)


def _ms(dt) -> int:
    return int(dt.timestamp() * 1000)


class AnalyticsDashboardView(AdminRequiredMixin, TemplateView):
    template_name = "labs/analytics_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["umami_url"] = "/umami"
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
            context["top_pages"] = umami_api.metrics("url", windows["7d"], now_ms, limit=12)
            context["top_events"] = umami_api.metrics("event", windows["7d"], now_ms, limit=12)
            context["top_browsers"] = umami_api.metrics("browser", windows["7d"], now_ms, limit=6)
        except umami_api.UmamiAPIError as e:
            logger.warning("Analytics dashboard degraded: %s", e)
            context["analytics_error"] = str(e)
        return context
