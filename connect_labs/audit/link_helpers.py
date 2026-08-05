"""
Shared URL-builder helpers for linking an audit assessment back to its source:
the CommCareHQ form submission and the visit in Connect.

Factored out of ExperimentBulkAssessmentExportCSVView._resolve_hq_link_base and
_resolve_visit_cluster_group (connect_labs/audit/views.py) so the classifier-fail
training-data export (connect_labs/audit/classifier_fail_sync.py) can build the
same links without duplicating the logic.
"""

import logging

from connect_labs.labs.analysis.data_access import fetch_opportunity_metadata

logger = logging.getLogger(__name__)


def resolve_hq_link_base(access_token: str, opportunity_id: int) -> str | None:
    """Return the CommCareHQ form_data base URL for an opportunity, or None if unresolvable.

    Callers append ``/{xform_id}/`` per form -- kept as a base (rather than
    taking xform_id here) so a caller resolving many forms for one opportunity
    only pays the opportunity-metadata fetch once.
    """
    try:
        metadata = fetch_opportunity_metadata(access_token, opportunity_id)
    except Exception:
        logger.exception("[Audit] Failed to resolve CommCareHQ domain for opportunity %s", opportunity_id)
        return None

    domain = metadata.get("cc_domain")
    if not domain:
        return None
    deliver_app = (metadata.get("raw") or {}).get("deliver_app") or {}
    hq_server_url = (deliver_app.get("hq_server") or {}).get("url") or "https://www.commcarehq.org"
    return f"{hq_server_url.rstrip('/')}/a/{domain}/reports/form_data"


def build_hq_form_url(hq_link_base: str | None, xform_id: str | None) -> str:
    """Combine a resolved base (see resolve_hq_link_base) with one form's xform_id."""
    return f"{hq_link_base}/{xform_id}/" if hq_link_base and xform_id else ""


def build_connect_visit_url(connect_url: str, org_slug: str, opportunity_id, user_id, user_visit_id) -> str:
    """Return the "view in Connect" visit URL, or "" if any required piece is missing."""
    if not (org_slug and user_id and user_visit_id):
        return ""
    return (
        f"{connect_url.rstrip('/')}/a/{org_slug}/opportunity/{opportunity_id}/user_visits/"
        f"?user={user_id}&visit_id={user_visit_id}"
    )
