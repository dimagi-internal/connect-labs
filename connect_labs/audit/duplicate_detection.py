"""
Duplicate Detection — sends already-computed visit-clustering groupings
(connect_labs.audit.visit_clustering) to the external Duplicate Detection
API and writes back which images were confirmed duplicates.

Runs once per grouping, across every image in it regardless of which image
path/track it came from -- the only filter is whatever the Visit Clustering
tile's time/distance parameters already produced. See
docs/superpowers/specs/2026-07-30-dual-track-audit-classifiers-design.md.
"""

import logging

from connect_labs.audit.data_access import is_audit_creation_cancelled
from connect_labs.audit.models import AI_NOTES_JOIN_SEP
from connect_labs.labs.integrations.duplicate_detection.api_client import DuplicateDetectionClient

logger = logging.getLogger(__name__)

DUPLICATE_DETECTED_LABEL = "Duplicate Detected"

# A grouping with only 1 resolvable image has nothing to compare -- either
# because the grouping itself only had 1 image, or because a signed-URL
# lookup failed for one of its blobs (see get_signed_url).
_MIN_IMAGES_TO_CHECK = 2


def _mark_duplicate(session, blob_meta_by_id, blob_id) -> bool:
    """Merge a confirmed-duplicate verdict into blob_id's assessment.

    set_assessment() (AuditSessionRecord) overwrites the whole assessment
    entry, which would clobber whatever a standard AI reviewer (or a human)
    already wrote for this blob earlier in the same audit-creation run --
    this does a read-modify-write instead. Returns False (no-op) if blob_id
    isn't one of this session's known images.
    """
    meta = blob_meta_by_id.get(blob_id)
    if not meta:
        return False

    visit_key = str(meta["visit_id"])
    visit_result = session.data.setdefault("visit_results", {}).setdefault(visit_key, {"assessments": {}})
    assessments = visit_result.setdefault("assessments", {})
    assessment = assessments.get(blob_id) or {
        "question_id": meta.get("question_id", ""),
        "result": None,
        "notes": "",
    }

    labels = [lbl.strip() for lbl in (assessment.get("ai_notes") or "").split(AI_NOTES_JOIN_SEP) if lbl.strip()]
    if DUPLICATE_DETECTED_LABEL not in labels:
        labels.append(DUPLICATE_DETECTED_LABEL)
    assessment["ai_notes"] = AI_NOTES_JOIN_SEP.join(labels)

    if assessment.get("ai_result") != "error":
        assessment["ai_result"] = "no_match"

    if not assessment.get("result"):
        assessment["result"] = "duplicate_fake"

    assessments[blob_id] = assessment
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
    images: bundle them into one API call (one call per grouping). Every
    blob_id returned in any response group gets flagged via _mark_duplicate.
    A grouping whose API call fails is logged and skipped -- never raises.

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
                result = client.detect_duplicates(images_payload)
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
            flagged_ids = {bid for group in (result.get("groups") or []) for bid in group}
            for blob_id in flagged_ids:
                if _mark_duplicate(session, blob_meta_by_id, blob_id):
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
