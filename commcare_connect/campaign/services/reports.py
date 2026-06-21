"""Report generation for the Reporting & Monitoring tab.

Produces the rows for the "Export data" button and the custom-report builder, as
CSV. Worker-based reports stream row-by-row over the campaign's worker store
(WorkerCase for a CommCare-domain campaign, else Worker) so a 50k roster exports
without buffering in memory.
"""

from __future__ import annotations

from types import SimpleNamespace

from commcare_connect.campaign.services import serializers

# Custom-report column label -> the key in the serialized worker dict (_worker).
WORKER_COLUMN_KEY = {
    "Worker ID": "id",
    "Name": "name",
    "Region": "region",
    "LGA": "lga",
    "Role": "role",
    "Gender": "gender",
    "Days worked": "daysWorked",
    "Amount": "amount",
    "KYC status": "kyc",
    "Payment status": "pay",
    "Enrollment date": "enrolled",
}
DEFAULT_WORKER_COLUMNS = [
    "Worker ID",
    "Name",
    "Region",
    "Role",
    "Days worked",
    "Amount",
    "KYC status",
    "Payment status",
]
# Group-by label -> the serialized worker key to sort/group rows by. Workers carry
# no activity/donor attribution, so the UI's "Activity"/"Donor" group-by options
# have no real worker field to bind to — they fall through to an ungrouped export
# rather than mislabeling a region column as "Activity"/"Donor".
GROUP_KEY = {"Region": "region", "Role": "role"}

WORKER_TYPES = {"worker_payments", "kyc_status", "attendance"}


def _iter_serialized_workers(campaign):
    role_names = {r.role_id: r.name for r in campaign.worker_roles.all()}
    region_names = {r.region_id: r.name for r in campaign.regions.all()}
    if campaign.commcare_domain:
        from commcare_connect.campaign.models import WorkerCase

        for wc in WorkerCase.objects.filter(campaign=campaign).order_by("worker_id").iterator(chunk_size=2000):
            yield serializers._worker(SimpleNamespace(**wc.properties), role_names, region_names)
    else:
        from commcare_connect.campaign.models import Worker

        for w in Worker.objects.filter(campaign=campaign).order_by("worker_id").iterator(chunk_size=2000):
            yield serializers._worker(w, role_names, region_names)


def _worker_report(campaign, columns, group_by):
    cols = [c for c in (columns or []) if c in WORKER_COLUMN_KEY] or DEFAULT_WORKER_COLUMNS
    group_key = GROUP_KEY.get(group_by)
    rows = list(_iter_serialized_workers(campaign))
    if group_key:
        rows.sort(key=lambda w: str(w.get(group_key, "")))
    header = ([group_by] if group_key else []) + cols
    out = [header]
    for w in rows:
        line = ([w.get(group_key, "")] if group_key else []) + [w.get(WORKER_COLUMN_KEY[c], "") for c in cols]
        out.append(line)
    return out


def _household_coverage_report(campaign):
    hs = getattr(campaign, "household_stat", None)
    out = [["Region", "Households", "Visited", "Coverage %"]]
    for c in (hs.coverage if hs else []) or []:
        hh, visited = c.get("hh", 0), c.get("visited", 0)
        pct = round(visited / hh * 100, 1) if hh else 0
        out.append([c.get("name", ""), hh, visited, pct])
    return out


def _activity_report(campaign):
    out = [["Activity", "Name", "Donor", "Region", "Status", "Target", "Reached", "Workers", "% reached"]]
    for a in campaign.activities.all().order_by("activity_id"):
        pct = round((a.reached or 0) / a.target * 100, 1) if a.target else 0
        out.append([a.activity_id, a.name, a.donor, a.region, a.status, a.target, a.reached, a.workers, pct])
    return out


def _reporting_summary_report(campaign):
    out = [["Day", "Enrolled", "Attended", "Paid"]]
    for d in campaign.report_days.all():
        out.append([d.day, d.enrolled, d.attended, d.paid])
    return out


def build_report(campaign, *, report_type, columns=None, group_by="", date_range=""):
    """Return a list of rows (header first) for ``report_type``. ``date_range`` is
    accepted for parity with the UI; the synthetic dataset is a single round so it
    doesn't sub-filter here."""
    if report_type in WORKER_TYPES:
        return _worker_report(campaign, columns, group_by)
    if report_type == "household_coverage":
        return _household_coverage_report(campaign)
    if report_type == "activity_performance":
        return _activity_report(campaign)
    if report_type == "reporting_summary":
        return _reporting_summary_report(campaign)
    raise ValueError(f"unknown report_type {report_type!r}")


def filename_for(report_type: str, campaign) -> str:
    code = (campaign.code or "campaign").lower().replace(" ", "-")
    return f"{code}-{report_type}.csv"
