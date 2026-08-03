"""
Duplicate Detection over visit-clustering groupings — sends already-computed
visit-clustering groupings (connect_labs.audit.visit_clustering) to the
external Duplicate Detection API and writes back which images were confirmed
duplicates.

Runs once per grouping, across every image in it regardless of which image
path/track it came from -- the only filter is whatever the Visit Clustering
tile's time/distance parameters already produced.

Reuses the shared DuplicateDetectionClient / DuplicateDetectionError /
assign_group_ids from connect_labs.audit.duplicate_detection (PR #1070's
day/FLW/type-bucketed duplicate detection for bulk_image_audit and
muac_picture_audit) rather than duplicating the API client. This module stays
separate because it's a genuinely different grouping strategy serving a
different template (weekly_dual_track_audit): one call per visit-clustering
grouping here, vs. one call per (FLW, day, photo-type) bucket there. See
docs/superpowers/specs/2026-07-30-dual-track-audit-classifiers-design.md.
"""

import logging

from connect_labs.audit.data_access import is_audit_creation_cancelled
from connect_labs.audit.duplicate_detection import DuplicateDetectionClient, assign_group_ids

logger = logging.getLogger(__name__)

# A grouping with only 1 resolvable image has nothing to compare -- either
# because the grouping itself only had 1 image, or because a signed-URL
# lookup failed for one of its blobs (see get_signed_url).
_MIN_IMAGES_TO_CHECK = 2


def _mark_duplicate(session, blob_meta_by_id, blob_id, group_id) -> bool:
    """Merge a confirmed-duplicate verdict into blob_id's assessment via
    AuditSessionRecord.flag_potential_duplicate_and_tag -- flags it (the same
    "Potential Duplicate" label PR #1070 uses, so both detection paths show up
    together in the AI-flags summary) AND auto-tags the human `result` as
    "duplicate_fake" when the assessment is still untouched, never overwriting
    a manual verdict. Returns False (no-op) if blob_id isn't one of this
    session's known images.
    """
    meta = blob_meta_by_id.get(blob_id)
    if not meta:
        return False
    session.flag_potential_duplicate_and_tag(
        visit_id=meta["visit_id"],
        blob_id=blob_id,
        question_id=meta.get("question_id", ""),
        group_id=group_id,
    )
    return True


def run_duplicate_detection(
    targets,
    *,
    get_signed_url,
    client=None,
    progress_callback=None,
    cancel_key=None,
) -> dict:
    """
    Args:
        targets: one entry per FLW session that had clusters computed --
            {"session": AuditSessionRecord, "data_access": AuditDataAccess,
             "opp_id": int, "clusters": [visit_clustering-shaped dicts],
             "blob_meta_by_id": {blob_id: {"visit_id": int, "question_id": str}}}.
        get_signed_url: callable(blob_id, opp_id) -> str | None. Resolves a
            world-readable URL for one blob; None means skip that blob.
        client: DuplicateDetectionClient instance (constructed lazily if
            omitted -- inject a fake for tests).
        progress_callback: callable(processed, total, message) -- total
            counts groupings across all targets.
        cancel_key: cooperative-cancellation key checked between targets and
            between groupings (see is_audit_creation_cancelled).

    For each target session, for each of its groupings with >= 2 resolvable
    images: bundle them into one API call (one call per grouping). The
    response's groups are collapsed into connected components via
    assign_group_ids (the endpoint may return overlapping groups); every blob
    in a component of size >= 2 gets flagged. A grouping whose API call fails
    is logged and skipped -- never raises.

    Returns {"groupings_checked", "groupings_skipped", "images_flagged", "errors", "cancelled"}.
    """
    client = client or DuplicateDetectionClient()
    groupings_checked = 0
    groupings_skipped = 0
    images_flagged = 0
    errors = 0
    cancelled = False

    total_groupings = sum(len(t["clusters"]) for t in targets)
    processed = 0

    for target in targets:
        if cancel_key and is_audit_creation_cancelled(cancel_key):
            cancelled = True
            break

        session = target["session"]
        opp_id = target["opp_id"]
        blob_meta_by_id = target["blob_meta_by_id"]
        session_updated = False

        for i, cluster in enumerate(target["clusters"]):
            if i > 0 and cancel_key and is_audit_creation_cancelled(cancel_key):
                cancelled = True
                break

            processed += 1
            image_ids = cluster.get("image_ids") or []

            images_payload = []
            if len(image_ids) >= _MIN_IMAGES_TO_CHECK:
                for blob_id in image_ids:
                    try:
                        url = get_signed_url(blob_id, opp_id)
                    except Exception as exc:
                        logger.warning(f"[DuplicateDetection] Failed to get signed URL for {blob_id}: {exc}")
                        url = None
                    if url:
                        images_payload.append({"id": blob_id, "url": url})

            if len(images_payload) < _MIN_IMAGES_TO_CHECK:
                groupings_skipped += 1
                if progress_callback:
                    progress_callback(processed, total_groupings, "Checking for duplicates...")
                continue

            try:
                groups = client.detect(images_payload)
            except Exception as exc:
                logger.warning(
                    f"[DuplicateDetection] API call failed for grouping {cluster.get('group_id')} "
                    f"(session {session.id}): {exc}"
                )
                errors += 1
                if progress_callback:
                    progress_callback(processed, total_groupings, "Checking for duplicates...")
                continue

            groupings_checked += 1
            blob_to_group = assign_group_ids(groups)
            for blob_id, group_id in blob_to_group.items():
                if _mark_duplicate(session, blob_meta_by_id, blob_id, group_id):
                    images_flagged += 1
                    session_updated = True

            if progress_callback:
                progress_callback(
                    processed, total_groupings, f"Checked {processed}/{total_groupings} groupings for duplicates"
                )

        if session_updated:
            try:
                target["data_access"].save_audit_session(session)
            except Exception as exc:
                logger.warning(f"[DuplicateDetection] Failed to save session {session.id}: {exc}")

        if cancelled:
            break

    return {
        "groupings_checked": groupings_checked,
        "groupings_skipped": groupings_skipped,
        "images_flagged": images_flagged,
        "errors": errors,
        "cancelled": cancelled,
    }
