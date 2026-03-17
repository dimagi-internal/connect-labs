"""Downloads page for S3-backed CSV exports."""
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseRedirect
from django.views import View
from django.views.generic import TemplateView

from commcare_connect.custom_analysis.audit_of_audits.views import DimagiUserRequiredMixin
from commcare_connect.labs.s3_export import AUDIT_SESSIONS_KEY, WORKFLOW_RUNS_KEY, _get_s3_client

logger = logging.getLogger(__name__)

_ALLOWED_KEYS = frozenset([WORKFLOW_RUNS_KEY, AUDIT_SESSIONS_KEY])


class ExportsIndexView(LoginRequiredMixin, DimagiUserRequiredMixin, TemplateView):
    """Lists available S3 export files with metadata and download links.

    Dimagi-staff only. No overview tile — accessed via direct URL.
    """

    template_name = "custom_analysis/exports/index.html"

    def get_context_data(self, **kwargs):
        from django.conf import settings

        context = super().get_context_data(**kwargs)
        bucket = getattr(settings, "LABS_EXPORTS_BUCKET", None) or None
        files = []

        if bucket:
            s3 = _get_s3_client()
            for key, label in [
                (WORKFLOW_RUNS_KEY, "workflow_runs.csv"),
                (AUDIT_SESSIONS_KEY, "audit_sessions.csv"),
            ]:
                try:
                    meta = s3.head_object(Bucket=bucket, Key=key)
                    custom = meta.get("Metadata", {})
                    size_kb = round(meta["ContentLength"] / 1024, 1)
                    files.append(
                        {
                            "key": key,
                            "label": label,
                            "last_updated": custom.get("last-updated", ""),
                            "size_kb": size_kb,
                            "row_count": custom.get("row-count", "—"),
                        }
                    )
                except Exception:
                    logger.warning("Could not read S3 metadata for %s", key, exc_info=True)

        context["files"] = files
        context["bucket_configured"] = bool(bucket)
        return context


class DownloadExportView(LoginRequiredMixin, DimagiUserRequiredMixin, View):
    """Generates a 15-minute pre-signed S3 URL and redirects the browser to it."""

    def get(self, request, *args, **kwargs):
        from django.conf import settings

        key = request.GET.get("key", "")
        if key not in _ALLOWED_KEYS:
            return HttpResponse("Invalid export key.", status=400)

        bucket = getattr(settings, "LABS_EXPORTS_BUCKET", None) or None
        if not bucket:
            return HttpResponse("Export storage is not configured.", status=404)

        s3 = _get_s3_client()
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=900,
        )
        return HttpResponseRedirect(url)
