"""Reporting exports — the 'Export data' button and the custom-report builder."""
import csv

from django.http import JsonResponse, StreamingHttpResponse

from connect_labs.campaign.auth.decorators import require_perm
from connect_labs.campaign.services import reports

REPORT_TYPES = {
    "worker_payments",
    "kyc_status",
    "attendance",
    "household_coverage",
    "activity_performance",
    "reporting_summary",
}


class _Echo:
    """A write-only buffer csv.writer uses to stream rows one at a time."""

    def write(self, value):
        return value


@require_perm("reporting", "export")
def report_export(request):
    """Stream a report as a CSV download. Params: type, columns (comma list),
    group_by, range. Serves the bootstrap-selected campaign."""
    from connect_labs.campaign.api.bootstrap import _select_campaign

    campaign = _select_campaign(request)
    if campaign is None:
        return JsonResponse({"error": "no campaign"}, status=404)

    report_type = request.GET.get("type", "reporting_summary")
    if report_type not in REPORT_TYPES:
        return JsonResponse({"error": "bad report type"}, status=400)

    columns = [c for c in request.GET.get("columns", "").split(",") if c]
    rows = reports.build_report(
        campaign,
        report_type=report_type,
        columns=columns,
        group_by=request.GET.get("group_by", ""),
        date_range=request.GET.get("range", ""),
    )
    writer = csv.writer(_Echo())
    response = StreamingHttpResponse((writer.writerow(row) for row in rows), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{reports.filename_for(report_type, campaign)}"'
    return response
