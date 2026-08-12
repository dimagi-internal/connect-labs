"""
Sync classifier_fails.csv (see connect_labs/labs/s3_export.py) with the human
review outcome after a Bulk Image Audit / Dual-Track save or complete request.

Called from ExperimentSaveAuditView.post and ExperimentAuditCompleteView.post
right after session.data["visit_results"] is persisted. Best-effort: any
failure here (S3, CommCareHQ metadata, Connect API) is logged and swallowed so
it can never break the actual save/complete flow the user is waiting on.
"""

import logging

from connect_labs.audit.link_helpers import resolve_urls_by_blob
from connect_labs.labs import s3_export

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
            # "error" is included alongside "no_match": when two independent
            # reviewers watch one image and one errors while another
            # genuinely fails, _combine_reviewer_results lets the error win,
            # so the assessment's own combined ai_result is "error" even
            # though a real classifier-fail row (from the failing reviewer's
            # own verdict) exists in classifier_fails.csv for this image. Without
            # this, has_ai_flag can stay False for the whole session and skip
            # syncing a human review that genuinely happened.
            if assessment.get("ai_result") in ("no_match", "error") or assessment.get("duplicate_group") is not None:
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
    in the session. Thin wrapper over link_helpers.resolve_urls_by_blob (shared with
    the AI-review/duplicate-detection producers, which resolve the same URLs eagerly
    at record time rather than waiting for this save/complete path)."""
    return resolve_urls_by_blob(
        data_access=data_access,
        access_token=data_access.access_token,
        opportunity_id=opportunity_id,
        visit_ids=session.visit_ids or [],
        visit_images=session.data.get("visit_images", {}),
        request=request,
    )
