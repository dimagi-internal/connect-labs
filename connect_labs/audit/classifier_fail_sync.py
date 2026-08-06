"""
Sync classifier_fails.csv (see connect_labs/labs/s3_export.py) with the human
review outcome after a Bulk Image Audit / Dual-Track save or complete request.

Called from ExperimentSaveAuditView.post and ExperimentAuditCompleteView.post
right after session.data["visit_results"] is persisted. Best-effort: any
failure here (S3, CommCareHQ metadata, Connect API) is logged and swallowed so
it can never break the actual save/complete flow the user is waiting on.
"""

import logging

from django.conf import settings
from django.urls import reverse

from connect_labs.audit.link_helpers import build_connect_visit_url, build_hq_form_url, resolve_hq_link_base
from connect_labs.labs import s3_export
from connect_labs.labs.context import get_org_data

logger = logging.getLogger(__name__)


def sync_after_save(session, request, data_access) -> None:
    """Entry point for the save/complete views. Never raises."""
    try:
        _sync_after_save(session, request, data_access)
    except Exception:
        logger.exception("[Audit] classifier-fail sync failed for session %s", session.id)


def _sync_after_save(session, request, data_access) -> None:
    human_result_by_blob: dict[str, str] = {}
    human_notes_by_blob: dict[str, str] = {}
    has_ai_flag = False

    for visit_result in session.data.get("visit_results", {}).values():
        for blob_id, assessment in visit_result.get("assessments", {}).items():
            if assessment.get("ai_result") == "no_match" or assessment.get("duplicate_group") is not None:
                has_ai_flag = True
            result = assessment.get("result")
            if result:
                human_result_by_blob[blob_id] = result
            notes = assessment.get("notes")
            if notes:
                human_notes_by_blob[blob_id] = notes

    if not has_ai_flag:
        # No classifier ever flagged an image in this session -- nothing in
        # classifier_fails.csv to sync, so skip the HQ/Connect/S3 round trips.
        return

    opportunity_id = session.opportunity_id
    url_by_blob = _resolve_urls(session, request, data_access, opportunity_id) if opportunity_id else {}

    reviewed_by = getattr(request.user, "username", "") or ""
    s3_export.sync_classifier_fail_outcomes(
        session.id,
        human_result_by_blob,
        human_notes_by_blob,
        url_by_blob,
        reviewed_by=reviewed_by,
    )


def _resolve_urls(session, request, data_access, opportunity_id) -> dict[str, dict]:
    """Build {blob_id: {"image_url", "form_url", "connect_url"}} for every image
    in the session, batching the HQ/Connect lookups once per session rather than
    once per image (same approach as ExperimentBulkAssessmentDataView.get and
    _resolve_visit_cluster_group in views.py)."""
    visit_ids = session.visit_ids or []
    if not visit_ids:
        return {}

    try:
        visits = data_access.get_visits_batch(visit_ids, opportunity_id)
    except Exception:
        logger.exception("[Audit] Failed to fetch visit batch for opportunity %s", opportunity_id)
        return {}

    xform_id_by_visit = {str(v["id"]): v.get("xform_id") for v in visits}
    link_id_by_visit = {str(v["id"]): (v.get("user_id"), v.get("user_visit_id")) for v in visits}

    hq_link_base = resolve_hq_link_base(data_access.access_token, opportunity_id)

    org_slug = ""
    org_data = get_org_data(request)
    for opp in org_data.get("opportunities", []):
        if opp.get("id") == opportunity_id:
            org_slug = opp.get("organization", "")
            break
    connect_url = getattr(settings, "CONNECT_PRODUCTION_URL", "https://connect.dimagi.com").rstrip("/")

    urls_by_blob: dict[str, dict] = {}
    for visit_id_str, images in session.data.get("visit_images", {}).items():
        form_url = build_hq_form_url(hq_link_base, xform_id_by_visit.get(visit_id_str))
        user_id, user_visit_id = link_id_by_visit.get(visit_id_str, (None, None))
        connect_visit_url = build_connect_visit_url(connect_url, org_slug, opportunity_id, user_id, user_visit_id)
        for image in images:
            blob_id = image.get("blob_id")
            if not blob_id:
                continue
            image_url = request.build_absolute_uri(
                reverse("audit:audit_image_connect", kwargs={"opp_id": opportunity_id, "blob_id": blob_id})
            )
            urls_by_blob[blob_id] = {
                "image_url": image_url,
                "form_url": form_url,
                "connect_url": connect_visit_url,
            }
    return urls_by_blob
