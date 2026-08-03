"""
Duplicate Detection over visit-clustering groupings — sends already-computed
visit-clustering groupings (connect_labs.audit.visit_clustering) to the
external Duplicate Detection API and writes back which images were confirmed
duplicates.

Runs once per grouping, across every image in it regardless of which image
path it came from -- the only filter is whatever the Visit Clustering tile's
time/distance parameters already produced. Scoped to a single track's own
audit: Track A and Track B are separate run_audit_creation invocations (see
weekly_dual_track_audit.py's job handler), each with its own independently-
computed clusters, so a grouping never spans across tracks.

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

from django.conf import settings

from connect_labs.audit.data_access import is_audit_creation_cancelled
from connect_labs.audit.duplicate_detection import DEFAULT_MAX_IMAGES_PER_DAY as DEFAULT_MAX_IMAGES_PER_GROUPING
from connect_labs.audit.duplicate_detection import DuplicateDetectionClient, assign_group_ids

logger = logging.getLogger(__name__)

# A grouping with only 1 resolvable image has nothing to compare -- either
# because the grouping itself only had 1 image, or because a signed-URL
# lookup failed for one of its blobs (see get_signed_url).
_MIN_IMAGES_TO_CHECK = 2

# Same cap, same fallback default, as PR #1070's day/FLW/type-bucketed
# detection (connect_labs.audit.duplicate_detection) -- a visit-clustering
# grouping can chain transitively over many consecutive visits, so without a
# cap one grouping could send an unbounded, all-or-nothing POST against a
# long-running (180s-timeout) endpoint. Imports the constant rather than
# re-declaring it, so the two can't silently drift apart.


def _mark_duplicate(session, blob_meta_by_id, blob_id, group_id) -> bool:
    """Merge a confirmed-duplicate verdict into blob_id's assessment via
    AuditSessionRecord.flag_potential_duplicate_and_tag -- flags it (the same
    "Potential Duplicate" label PR #1070 uses, so both detection paths show up
    together in the AI-flags summary) AND auto-tags the human `result` as
    "duplicate_fake" when the assessment is still untouched, never overwriting
    a manual verdict. Returns False (no-op) if blob_id isn't one of this
    session's known images.

    Note: `group_id` shares a single `assessment["duplicate_group"]` field
    with PR #1070's day/FLW/type-bucketed detection, which uses its own,
    unrelated component-index space. If a session were ever processed by
    BOTH stages (they're gated by different criteria flags and target
    different templates today, so this doesn't happen in practice), whichever
    stage ran last would overwrite the other's group id for a shared blob.
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


def run_grouping_duplicate_detection(
    targets,
    *,
    get_signed_url,
    client=None,
    progress_callback=None,
    cancel_key=None,
    max_images_per_grouping=None,
) -> dict:
    """
    Args:
        targets: one entry per FLW session that had clusters computed --
            {"session": AuditSessionRecord, "data_access": AuditDataAccess,
             "opp_id": int, "clusters": [visit_clustering-shaped dicts],
             "blob_meta_by_id": {blob_id: {"visit_id": int, "question_id": str}}}.
            "session" is used only for its .id -- this function re-fetches the
            session via data_access.get_audit_session() before touching it, so
            whatever the AI-review stage(s) already wrote/saved to it in the
            meantime is preserved rather than clobbered by save_audit_session's
            full-document replace.
        get_signed_url: callable(blob_id, opp_id) -> str | None. Resolves a
            world-readable URL for one blob; None means skip that blob.
        client: DuplicateDetectionClient instance (constructed lazily if
            omitted -- inject a fake for tests).
        progress_callback: callable(processed, total, message) -- total
            counts groupings across all targets.
        cancel_key: cooperative-cancellation key checked between targets and
            between groupings (see is_audit_creation_cancelled).
        max_images_per_grouping: cap on how many images from one grouping are
            sent in a single API call (defaults to the shared
            settings.DUPLICATE_DETECTION_MAX_IMAGES_PER_DAY, same as PR
            #1070's day/FLW/type-bucketed detection). A grouping over the cap
            still runs, just on its first N images -- the rest are counted
            into "skipped_over_limit", never silently dropped.

    For each target session, for each of its groupings with >= 2 resolvable
    images: bundle them into one API call (one call per grouping). The
    response's groups are collapsed into connected components via
    assign_group_ids (the endpoint may return overlapping groups); every blob
    in a component of size >= 2 gets flagged. A grouping whose API call fails
    is logged and skipped -- never raises.

    Returns {"groupings_checked", "groupings_skipped", "skipped_over_limit",
    "images_flagged", "errors", "cancelled"}.
    """
    client = client or DuplicateDetectionClient()
    max_images_per_grouping = (
        max_images_per_grouping
        if max_images_per_grouping is not None
        else getattr(settings, "DUPLICATE_DETECTION_MAX_IMAGES_PER_DAY", DEFAULT_MAX_IMAGES_PER_GROUPING)
    )
    groupings_checked = 0
    groupings_skipped = 0
    skipped_over_limit = 0
    images_flagged = 0
    errors = 0
    cancelled = False

    total_groupings = sum(len(t["clusters"]) for t in targets)
    processed = 0

    try:
        for target in targets:
            if cancel_key and is_audit_creation_cancelled(cancel_key):
                cancelled = True
                break

            data_access = target["data_access"]
            # target["session"] is the object create_audit_session() returned at
            # audit-creation time -- its visit_results is still {}. By the time
            # this stage runs, the AI-review stage(s) have already re-fetched
            # and saved their own writes to this session. save_audit_session()
            # is a full-document replace, so mutating and saving the STALE
            # object here would silently wipe every assessment those stages
            # just wrote. Re-fetch fresh right before touching it.
            session = data_access.get_audit_session(target["session"].id)
            if not session:
                logger.warning(f"[DuplicateDetection] Session {target['session'].id} not found -- skipping")
                continue
            opp_id = target["opp_id"]
            blob_meta_by_id = target["blob_meta_by_id"]
            session_updated = False

            for i, cluster in enumerate(target["clusters"]):
                if i > 0 and cancel_key and is_audit_creation_cancelled(cancel_key):
                    cancelled = True
                    break

                processed += 1
                image_ids = cluster.get("image_ids") or []
                if len(image_ids) > max_images_per_grouping:
                    dropped = len(image_ids) - max_images_per_grouping
                    logger.info(
                        f"[DuplicateDetection] Grouping {cluster.get('group_id')} has {len(image_ids)} images; "
                        f"capping at {max_images_per_grouping} ({dropped} skipped)"
                    )
                    image_ids = image_ids[:max_images_per_grouping]
                    skipped_over_limit += dropped

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
                # Persist the raw API response, keyed by this visit-clustering
                # grouping's own group_id -- mirrors duplicate_detection.py's
                # raw_groups_store (keyed by question_id|day there) so a later
                # investigation ("why wasn't X flagged as a duplicate?") can
                # read back exactly what the detector returned for this
                # grouping's manifest instead of it being discarded the moment
                # assign_group_ids collapses it into flags.
                session.data.setdefault("visit_cluster_duplicate_detection", {})[str(cluster.get("group_id"))] = groups
                session_updated = True
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
                    data_access.save_audit_session(session)
                except Exception as exc:
                    logger.warning(f"[DuplicateDetection] Failed to save session {session.id}: {exc}")

            if cancelled:
                break
    finally:
        client.close()

    return {
        "groupings_checked": groupings_checked,
        "groupings_skipped": groupings_skipped,
        "skipped_over_limit": skipped_over_limit,
        "images_flagged": images_flagged,
        "errors": errors,
        "cancelled": cancelled,
    }
