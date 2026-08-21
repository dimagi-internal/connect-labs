"""
Celery tasks for asynchronous audit creation.

Provides async audit creation with:
- Multi-stage progress tracking
- SSE streaming support
- Workflow integration
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NamedTuple

from config import celery_app
from connect_labs.audit.data_access import (
    AuditCriteria,
    AuditDataAccess,
    create_mock_request,
    is_audit_creation_cancelled,
)
from connect_labs.audit.link_helpers import resolve_opportunity_attribution, resolve_urls_by_blob
from connect_labs.audit.models import AI_NOTES_JOIN_SEP
from connect_labs.audit.run_checkpoints import call_key, session_key
from connect_labs.audit.visit_cluster_duplicate_detection import run_grouping_duplicate_detection
from connect_labs.audit.visit_clustering import build_flw_visit_clusters
from connect_labs.labs import s3_export
from connect_labs.labs.ai_review_agents.base import (
    ERROR_KIND_AGENT_EXCEPTION,
    ERROR_KIND_RATE_LIMITED,
    ERROR_KIND_TIMEOUT,
    ERROR_KIND_UNKNOWN,
    ERROR_KIND_UNREACHABLE,
)
from connect_labs.utils.celery import set_task_progress
from connect_labs.utils.progress_relays import _RELAYS as AUDIT_PROGRESS_RELAYS  # noqa: F401  (back-compat alias)
from connect_labs.utils.progress_relays import get_relay

logger = logging.getLogger(__name__)

# Celery's autodiscover_tasks() only imports <app>/tasks.py, so a @shared_task
# defined anywhere else in this app never registers -- and a beat entry naming an
# unregistered task fails at dispatch, not at startup. Imported here so
# connect_labs.audit.reconcile_prior_audit_index actually exists on the worker.
from connect_labs.audit.prior_audit_tasks import reconcile_prior_audit_index  # noqa: E402,F401

# The in-process progress-relay registry now lives in connect_labs.utils.progress_relays
# (domain-neutral, reusable by any eager in-process fan-out). AUDIT_PROGRESS_RELAYS
# remains as a back-compat alias to the shared dict so existing imports keep working;
# new code should use register_relay / get_relay / pop_relay.


@celery_app.task(bind=True)
def test_async_simple(self, sleep_seconds: int = 3) -> dict:
    """
    Simple test task for verifying async behavior.

    Used by test_async_audit management command to verify Celery is working.
    """
    set_task_progress(self, "Starting...", current_stage=1, total_stages=3)
    time.sleep(sleep_seconds / 3)

    set_task_progress(self, "Working...", current_stage=2, total_stages=3)
    time.sleep(sleep_seconds / 3)

    set_task_progress(self, "Finishing...", current_stage=3, total_stages=3)
    time.sleep(sleep_seconds / 3)

    return {"success": True, "message": "Test completed"}


def _update_job_progress(
    data_access,
    task_id: str,
    username: str,
    status: str = "running",
    current_stage: int = 0,
    total_stages: int = 4,
    stage_name: str = "",
    message: str = "",
    processed: int = 0,
    total: int = 0,
    result: dict | None = None,
    error: str | None = None,
):
    """Update the job record with progress."""
    try:
        job = data_access.get_audit_creation_job_by_task_id(task_id)
        if job:
            data_access.update_audit_creation_job(
                job_id=job["id"],
                username=username,
                status=status,
                progress={
                    "current_stage": current_stage,
                    "total_stages": total_stages,
                    "stage_name": stage_name,
                    "message": message,
                    "processed": processed,
                    "total": total,
                },
                result=result,
                error=error,
            )
    except Exception as e:
        logger.warning(f"[AuditCreation] Failed to update job progress: {e}")


def _build_ai_to_human_result(agent, auto_apply_actions: list[str] | None) -> dict[str, str]:
    """Map each AI verdict to the human result it should pre-tag at creation time.

    Each entry in the agent's ``result_actions`` describes one verdict: an
    ``ai_result`` (e.g. ``"no_match"``) and the ``human_result`` to apply
    (e.g. ``"fail"``). This returns the subset that should be applied automatically.

    Args:
        agent: An AI review agent (uses ``result_actions`` and ``auto_apply_result``).
        auto_apply_actions: Which action keys auto-apply.
            - ``None``: legacy behavior — honor the agent's ``auto_apply_result``
              flag (all actions if True, none if False). Keeps audits created
              before this option was added (and other callers) unchanged.
            - list: ONLY the named action keys auto-apply. An empty list means
              "flag only — nothing is pre-tagged."

    Returns:
        Dict of ``ai_result -> human_result`` for the auto-applied actions.
    """
    result_actions = getattr(agent, "result_actions", {}) or {}
    if auto_apply_actions is None:
        selected_keys = set(result_actions) if getattr(agent, "auto_apply_result", False) else set()
    else:
        selected_keys = set(auto_apply_actions)

    mapping: dict[str, str] = {}
    for key, action in result_actions.items():
        if key in selected_keys and "ai_result" in action and "human_result" in action:
            mapping[action["ai_result"]] = action["human_result"]
    return mapping


class ResolvedReviewer(NamedTuple):
    """One reviewer resolved for an image path — see resolve() in
    _run_ai_review_on_sessions. comparison_field is the related_fields path
    (e.g. from the wizard's per-image-type config.comparison_field) THIS
    reviewer's own reading should come from; None if this reviewer needs no
    reading or has no field configured."""

    agent: object
    requires_reading: bool
    ai_to_human_map: dict[str, str]
    comparison_field: str | None


class ReviewerVerdict(NamedTuple):
    """One reviewer's outcome on one image — the input to _combine_reviewer_results."""

    agent_id: str
    ai_result: str
    ai_notes: str | None
    ai_confidence: float | None
    ai_to_human_map: dict[str, str]
    # Machine-readable cause when ai_result == "error" (one of the ERROR_KIND_*
    # constants in connect_labs.labs.ai_review_agents.base), else "". The
    # auditor-facing note is deliberately generic prose, so this is what lets a
    # run summary say WHICH failure produced its error count instead of just
    # how many there were.
    error_kind: str = ""


class FetchReviewOutcome(NamedTuple):
    """Return type of _fetch_and_review — replaces a 10-element positional
    tuple that grew error-prone to keep in sync across three return sites."""

    visit_id_str: str
    blob_id: str
    question_id: str
    image_question_id: str
    ai_result: str | None
    ai_notes: str | None
    ai_confidence: float | None
    human_result: str | None
    skipped: bool
    # Individual no_match ReviewerVerdicts for this image (error verdicts are
    # deliberately excluded -- see the fail_verdicts filter below), BEFORE
    # _combine_reviewer_results collapses them into the single winning verdict
    # above. Two independent reviewers (e.g. MUAC OverZoom + MUAC Match) can
    # each fail the same image -- this is what lets the classifier-fail export
    # (connect_labs/labs/s3_export.py's record_classifier_fails) write one row
    # per failing classifier instead of one row for the merged outcome. Empty
    # for images that were skipped or had no runnable reviewer.
    fail_verdicts: tuple = ()
    # Why this image was skipped, when skipped is True. "skipped" used to
    # conflate two unrelated situations that need opposite responses -- a
    # CONFIGURATION problem (no reviewer had a reading it could use, so the run
    # silently produces nothing) and an INFRASTRUCTURE problem (the image could
    # not be downloaded). Tallied by reason in the run summary so they are
    # distinguishable without re-reading the source.
    skip_reason: str = ""
    # ERROR_KIND_* for every reviewer on this image that errored, pre-collapse.
    error_kinds: tuple = ()


# skip_reason values (see FetchReviewOutcome.skip_reason).
SKIP_NO_REVIEWER_READING = "no_reviewer_reading"
SKIP_IMAGE_DOWNLOAD_FAILED = "image_download_failed"
SKIP_IMAGE_EMPTY = "image_empty"

# Cap on distinct (image path, reading field) pairs named in the misconfiguration
# diagnostics. The pairs come from admin-configured reviewer specs crossed with
# whatever field paths the data happens to carry, so the count is not bounded by
# anything validated -- log the worst offenders rather than risking one line per
# distinct combination. Ordered by descending image count, so the cap only ever
# drops the rarest cases.
_MAX_LOGGED_MISSING_FIELDS = 10


def _combine_reviewer_results(
    per_agent_results: list[ReviewerVerdict],
) -> tuple[str, str | None, float | None, str | None]:
    """Combine independent per-reviewer verdicts on one image into the single
    ai_result/ai_notes/ai_confidence/human_result an assessment stores.

    Each reviewer runs and is scored independently (e.g. MUAC OverZoom and
    MUAC Match both watching the same photo) — an error or failure from ANY
    reviewer wins over a pass, so a flagged image is never silently hidden by
    another reviewer's pass. Each reviewer's own badge_label/pass_label stays
    intact in the combined notes so failures remain distinguishable at a
    glance (e.g. "Hyperzoomed" vs "MUAC Mismatch (strict tolerance)").

    The AI_NOTES_JOIN_SEP join below is load-bearing beyond this function:
    AuditSessionRecord.get_assessment_stats() (connect_labs/audit/models.py)
    splits ai_notes back apart on the SAME constant to tally each reviewer's
    own flag count separately. A badge_label must stay classifier-level (no
    per-visit data embedded) or that tally silently fragments into one bogus
    entry per distinct value.

    human_result is derived from the SAME winning bucket that decided
    ai_result — never from an independent poll of all reviewers — so it can
    never contradict the displayed ai_result (e.g. persisting "pass" as the
    human decision while the badge shows "no_match" because a different
    reviewer, whose own auto_apply_actions doesn't cover its fail case,
    failed independently).

    Args:
        per_agent_results: one ReviewerVerdict per reviewer that actually ran.
            Must be non-empty — the caller is responsible for treating "no
            reviewer ran" as skipped, not as calling this function.

    Returns:
        (ai_result, ai_notes, ai_confidence, human_result)
    """
    if not per_agent_results:
        raise ValueError("_combine_reviewer_results requires at least one reviewer verdict")

    errors = [v for v in per_agent_results if v.ai_result == "error"]
    failures = [v for v in per_agent_results if v.ai_result == "no_match"]
    passes = [v for v in per_agent_results if v.ai_result == "match"]

    if errors:
        ai_result, winning = "error", errors
    elif failures:
        ai_result, winning = "no_match", failures
    else:
        ai_result, winning = "match", passes

    ai_notes = AI_NOTES_JOIN_SEP.join(v.ai_notes for v in winning if v.ai_notes) or None
    confidences = [v.ai_confidence for v in winning if v.ai_confidence is not None]
    ai_confidence = confidences[0] if confidences else None

    # Only the winning reviewers' own auto-apply mapping can set human_result —
    # a reviewer outside the winning bucket never gets a vote (see docstring).
    mapped_results = {v.ai_to_human_map.get(ai_result) for v in winning}
    if "fail" in mapped_results:
        human_result = "fail"
    elif "pass" in mapped_results:
        human_result = "pass"
    else:
        human_result = None

    return ai_result, ai_notes, ai_confidence, human_result


def _reading_for(comparison_field: str | None, reading_by_field: dict[str, str]) -> str | None:
    """The reading value ONE specific reviewer should use, so that when several
    independent reviewers watch the same image path (see ResolvedReviewer),
    each pulls its own configured field instead of all of them sharing
    whichever related field happened to have a value first.

    comparison_field is that reviewer's own config.comparison_field (None if
    it wasn't configured, e.g. muac_overzoom, or for the legacy single-agent
    path, which predates per-reviewer field configuration) — falls back to
    "any reading present" for that case, matching the original behavior.
    """
    if comparison_field:
        return reading_by_field.get(comparison_field)
    return next(iter(reading_by_field.values()), None)


# Caps the per-image inner reviewer pool (see _run_ai_review_on_sessions).
# len(runnable) comes from admin-configured reviewer specs, not a validated
# count -- this bounds worst-case concurrent threads/gateway connections per
# image regardless of how many reviewer entries a payload attaches to one
# image path. Current production usage is 2 (MUAC OverZoom + MUAC Match);
# this leaves headroom without being unbounded.
_MAX_REVIEWERS_PER_IMAGE = 4

# Caps the outer per-session image pool (see _run_ai_review_on_sessions). Benchmarked
# directly against the real ML classify gateway at each pool size below, times today's
# actual 2 reviewers/image (i.e. real concurrent gateway calls = pool size x
# reviewers/image) -- full run data (throughput, latency distributions) is in the PR
# description that introduced this constant; figures below are that run's measured
# results, not estimates:
#   pool=5  (10 concurrent calls):  baseline
#   pool=10 (20 concurrent calls):  throughput ~2x pool=5, latency flat -- this value
#   pool=15 (30 concurrent calls):  no throughput gain over pool=10, latency starts climbing
#   pool=20 (40 concurrent calls):  still no throughput gain, latency climbs further, no errors
#   pool=30 (60 concurrent calls):  no throughput gain, 3 of 60 calls (5%) hit read timeouts
# i.e. throughput plateaus at pool=10 and going higher only adds queueing delay, with
# outright timeouts first appearing around 60 concurrent calls.
#
# This pool isn't the only source of concurrent load on the gateway: real-world worst
# case is this value multiplied by _MAX_REVIEWERS_PER_IMAGE above, since each outer
# worker's image can fan out to that many reviewers. At today's actual usage (2
# reviewers/image) that's the 20-concurrent-call row above, right at the plateau. At the
# theoretical cap of 4 reviewers/image, worst case rises to 40 concurrent calls -- the
# row where latency has climbed but no timeouts appeared yet. If reviewer-count-per-image
# grows toward that cap in practice, or the gateway's own capacity changes, re-benchmark
# before raising either constant further.
_MAX_CONCURRENT_IMAGES_PER_SESSION = 10

# Error kinds that mean "the gateway was overloaded", as opposed to "this image
# is broken". Retrying these immediately re-enters the same congestion.
_SATURATION_ERROR_KINDS = frozenset({ERROR_KIND_TIMEOUT, ERROR_KIND_RATE_LIMITED, ERROR_KIND_UNREACHABLE})

# How long the retry sweep waits before re-attempting images that failed for a
# saturation reason.
#
# The sweep re-runs every errored image once, which is right for a broken blob
# and wrong for a busy gateway: it fires the moment the first pass ends, into
# the exact congestion that caused the failure. Measured over 2026-08-11..18 it
# retried 592 images and recovered 160 -- 27%. The failures it was retrying into
# were overwhelmingly timeouts, and a timeout costs a full 60s client timeout
# per attempt, so the sweep was also the most expensive possible way to fail.
#
# The pool sizes above were benchmarked one run at a time. Nothing bounds
# CONCURRENT runs, and on 2026-08-14 five scale runs started within 46 seconds
# (up to 200 concurrent gateway calls against a plateau of ~20), which produced
# 73-92% error rates. Even two runs two minutes apart gave 79% on 2026-08-17,
# while runs spaced hours apart gave 9%. A global concurrency budget is the real
# fix and is tracked in #1231; this constant is the cheap half -- it stops the
# retry from piling onto a gateway that is still saturated.
_RETRY_SWEEP_BACKOFF_SECONDS = 30.0


def _run_ai_review_on_sessions(
    data_access,
    session_ids: list[int],
    access_token: str,
    opp_id: int,
    ai_agent_id: str | None = None,
    auto_apply_actions: list[str] | None = None,
    ai_reviewers: dict | None = None,
    progress_callback=None,
    cancel_key: str | None = None,
    log_tag: str = "",
) -> dict:
    """
    Run AI review agent on the specified audit sessions.

    This runs the AI agent on each image in the session that has related field data.
    Results are persisted to each session's assessment data.

    Args:
        data_access: AuditDataAccess instance
        session_ids: List of session IDs to review
        ai_agent_id: ID of the AI agent to use
        access_token: OAuth token for API access
        opp_id: Opportunity ID
        progress_callback: Optional callback for progress updates (processed, total, message)
        auto_apply_actions: Which AI verdicts auto-apply as human results. None =
            legacy per-agent default; a list (possibly empty) selects exactly which
            action keys pre-tag. See ``_build_ai_to_human_result``.
        cancel_key: If set, checked cooperatively between sessions (and between
            images within a session, including during the post-pass retry
            sweep described below) via ``is_audit_creation_cancelled`` so a
            large review can be stopped mid-run. Sessions already created and
            images already reviewed are left as-is -- only remaining work stops.
        log_tag: Short correlation id (the Celery task id) stamped on every log
            line this function emits. Without it the only way to isolate one
            run's lines is the worker-process number, which Celery reuses across
            tasks -- so concurrent audits interleave indistinguishably.

    Returns:
        Dict with review results summary
    """
    from connect_labs.labs.ai_review_agents.registry import get_agent

    tag = f"[AIReview{':' + log_tag if log_tag else ''}]"

    # Resolve the reviewer(s) for a given image question_id. Unifies two modes:
    #   * per-type (ai_reviewers given): look up the agent list by question_id.
    #     An image path may have more than one independent reviewer (e.g. MUAC
    #     OverZoom + MUAC Match both watching the same photo) — each runs and
    #     is scored independently; see _combine_reviewer_results.
    #   * legacy (ai_agent_id given): the same single agent applies to every question_id
    # Returns a list of ResolvedReviewer — empty when no reviewer applies.
    _reviewer_cache: dict = {}

    def _cache_reviewer(cache_key, agent_id, actions, comparison_field):
        if cache_key not in _reviewer_cache:
            ag = get_agent(agent_id)
            _reviewer_cache[cache_key] = ResolvedReviewer(
                agent=ag,
                requires_reading=getattr(ag, "requires_reading", True),
                ai_to_human_map=_build_ai_to_human_result(ag, actions),
                comparison_field=comparison_field,
            )
        return _reviewer_cache[cache_key]

    def resolve(question_id):
        if ai_reviewers is not None:
            specs = [s for s in (ai_reviewers.get(question_id) or []) if s and s.get("agent_id")]
            return [
                _cache_reviewer(
                    ("qid", question_id, spec["agent_id"]),
                    spec["agent_id"],
                    spec.get("auto_apply_actions"),
                    spec.get("comparison_field"),
                )
                for spec in specs
            ]
        else:
            if not ai_agent_id:
                return []
            return [_cache_reviewer(("global",), ai_agent_id, auto_apply_actions, None)]

    if ai_reviewers is not None:
        logger.info(f"{tag} Per-image-type review on {len(session_ids)} sessions: {ai_reviewers}")
    else:
        logger.info(f"{tag} Running agent '{ai_agent_id}' on {len(session_ids)} sessions")

    # First pass: count only images that have a reviewer AND meet its reading requirement
    total_images_to_review = 0
    session_image_counts = {}
    for session_id in session_ids:
        try:
            session = data_access.get_audit_session(session_id)
            if session:
                visit_images = session.data.get("visit_images", {})
                reviewable_count = 0
                for images in visit_images.values():
                    for image_data in images:
                        if not image_data.get("blob_id"):
                            continue
                        resolved = resolve(image_data.get("question_id", ""))
                        if not resolved:
                            continue
                        related_fields = image_data.get("related_fields", [])
                        has_reading = any(rf.get("value") for rf in related_fields)
                        # Reviewable if AT LEAST ONE resolved reviewer can actually run.
                        # (Coarse approximation for the progress-bar total — has_reading
                        # is a loop invariant, hoisted out of the reviewer check. The
                        # actual per-reviewer skip logic below is comparison_field-precise.)
                        if has_reading or any(not r.requires_reading for r in resolved):
                            reviewable_count += 1
                session_image_counts[session_id] = reviewable_count
                total_images_to_review += reviewable_count
        except Exception:
            pass

    if progress_callback:
        progress_callback(0, total_images_to_review, f"Starting AI review of {total_images_to_review} images...")

    total_reviewed = 0
    total_passed = 0
    total_failed = 0
    total_errors = 0
    total_skipped = 0
    images_processed = 0
    cancelled = False
    review_started_at = time.monotonic()
    # Error causes and skip causes, tallied across the whole run. These are the
    # two numbers that were previously opaque: a bare "errors=160, skipped=475"
    # says something went wrong but not what, and answering that meant querying
    # per-agent log lines that carry no session or blob id to join on.
    error_kind_counts: dict[str, int] = {}
    skip_reason_counts: dict[str, int] = {}
    # For SKIP_NO_REVIEWER_READING only: (image_question_id, wanted_field) ->
    # {"count": n, "present": [field paths that DID have values]}. This is the
    # payload that turns "everything was skipped" into "you configured field X
    # on image Y, but the data carries fields A/B" -- i.e. a fixable statement.
    missing_reading_fields: dict[tuple[str, str], dict] = {}
    # Collected across every session in this run, written to S3 once at the end
    # (see connect_labs.labs.s3_export.record_classifier_fails) rather than once
    # per row -- this is a training-data export, not part of the review flow.
    classifier_fail_rows: list[dict] = []

    def _note_skip(reason: str) -> None:
        skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1

    for session_id in session_ids:
        if cancel_key and is_audit_creation_cancelled(cancel_key):
            logger.info(f"{tag} Cancelled — stopping before session {session_id}")
            cancelled = True
            break
        try:
            # Per-session wall clock. Sessions are processed one after another,
            # so this is what shows a small session costing as much as a large
            # one (a single hung classifier call stalls a session that has only
            # a couple of images and cannot fill the pool).
            session_started_at = time.monotonic()
            session_reviewed_before = total_reviewed
            session_errors_before = total_errors

            # Get session data
            session = data_access.get_audit_session(session_id)
            if not session:
                logger.warning(f"{tag} Session {session_id} not found")
                continue

            if session.data.get("ai_review_complete"):
                logger.info(f"{tag} Session {session_id} already reviewed — skipping (resumed run)")
                skipped_count = session_image_counts.get(session_id, 0)
                images_processed += skipped_count
                if progress_callback:
                    progress_callback(
                        images_processed,
                        total_images_to_review,
                        f"Reviewed {images_processed}/{total_images_to_review} images (resumed)",
                    )
                continue

            # Get visit_images from session data
            # This contains the images and their related field data
            visit_images = session.data.get("visit_images", {})
            logger.info(
                f"{tag} Session {session_id}: found {len(visit_images)} visits with images, "
                f"data keys: {list(session.data.keys())}"
            )
            if not visit_images:
                logger.info(f"{tag} Session {session_id} has no visit_images")
                continue

            # Track if we made any updates to this session
            session_updated = False

            # Rows this session contributes to classifier_fail_rows -- kept separate
            # from that run-level list so the URL resolution below (once per session,
            # not once per run) only has to touch this session's own new rows.
            session_classifier_fail_rows: list[dict] = []

            # blob_id -> that image's OWN opportunity_id, when it carries one --
            # a multi-opp session (e.g. muac_picture_audit) can flag images
            # sourced from a different opportunity than session.opportunity_id
            # (same fallback duplicate_detection.py's img.get("opportunity_id")
            # already uses for the identical shape of data).
            blob_opportunity_id = {
                image.get("blob_id"): image.get("opportunity_id")
                for images in visit_images.values()
                for image in images
                if image.get("blob_id")
            }

            # Phase 1: collect reviewable work items, skip the rest.
            # Each item: (visit_id_str, blob_id, reading_by_field, question_id, image_qid)
            #   image_qid -> the image's own question path, used to resolve its reviewer(s)
            #   reading_by_field -> {field_path: value} from related_fields — each resolved
            #     reviewer picks ITS OWN reading via its comparison_field (see _reading_for),
            #     rather than every reviewer on this path sharing one scalar value.
            #   question_id -> stored on the assessment (best-effort: the first related
            #     field with a value, if any, else the image's own path)
            work_items = []
            for visit_id_str, images in visit_images.items():
                logger.debug(f"{tag} Visit {visit_id_str}: {len(images)} images")
                for image_data in images:
                    blob_id = image_data.get("blob_id")
                    if not blob_id:
                        continue
                    image_qid = image_data.get("question_id", "")
                    resolved = resolve(image_qid)
                    if not resolved:
                        continue  # no reviewer configured for this image type
                    related_fields = image_data.get("related_fields", [])
                    reading_by_field = {
                        rf["path"]: str(rf["value"]) for rf in related_fields if rf.get("path") and rf.get("value")
                    }
                    question_id = image_qid
                    for rf in related_fields:
                        if rf.get("value"):
                            question_id = rf.get("path") or question_id
                            break
                    # Only skip the whole image if EVERY resolved reviewer needs a
                    # reading and none can find ITS OWN configured field's value — a
                    # reviewer that doesn't need one (e.g. muac_overzoom), or a
                    # different reviewer whose own field IS present, still runs
                    # individually in _fetch_and_review.
                    if all(
                        r.requires_reading and not _reading_for(r.comparison_field, reading_by_field) for r in resolved
                    ):
                        # Record WHICH field each reviewer wanted and which ones the
                        # data actually carried. A whole run can end up here with
                        # zero reviews and still report success, so the misconfigured
                        # field path is the single most useful thing to capture.
                        present = sorted(reading_by_field)
                        for r in resolved:
                            wanted = r.comparison_field or "<any related field>"
                            entry = missing_reading_fields.setdefault(
                                (image_qid, wanted), {"count": 0, "present": present}
                            )
                            entry["count"] += 1
                        logger.debug(
                            f"{tag} Skipping blob={blob_id} ({image_qid}): "
                            f"no reviewer has a reading it can use; present fields={present}"
                        )
                        _note_skip(SKIP_NO_REVIEWER_READING)
                        total_skipped += 1
                        images_processed += 1
                        continue
                    work_items.append((visit_id_str, blob_id, reading_by_field, question_id, image_qid))

            # Phase 2: fetch + AI-review all images in parallel.
            # Both the Connect image download and the ML classification call are HTTP-bound,
            # so concurrent workers cut wall-clock time roughly proportional to worker count.
            # httpx.Client (used by both data_access and the agent) is thread-safe.
            # Two levels of concurrency: this outer pool fans out across images;
            # within a single image, independent reviewers (e.g. MUAC OverZoom +
            # MUAC Match) are further parallelized in their own small pool -- see
            # the note below _run_one.
            def _fetch_and_review(item):
                v_id, b_id, reading_by_field, q_id, img_qid = item
                resolved_reviewers = resolve(img_qid)
                # This image's OWN opportunity when it carries one -- a multi-opp
                # session can review images sourced from a different opportunity
                # than session.opportunity_id (same fallback blob_opportunity_id
                # above uses for the classifier-fail row's own attribution).
                img_opp_id = blob_opportunity_id.get(b_id) or opp_id
                try:
                    img_bytes = data_access.download_image_from_connect(b_id, img_opp_id)
                    if not img_bytes:
                        logger.warning(f"{tag} Empty image body for blob={b_id} (opp {img_opp_id})")
                        return FetchReviewOutcome(
                            v_id, b_id, q_id, img_qid, None, None, None, None, True, skip_reason=SKIP_IMAGE_EMPTY
                        )
                except Exception as exc:
                    logger.warning(f"{tag} Failed to fetch image {b_id}: {type(exc).__name__}: {exc}")
                    return FetchReviewOutcome(
                        v_id,
                        b_id,
                        q_id,
                        img_qid,
                        None,
                        None,
                        None,
                        None,
                        True,
                        skip_reason=SKIP_IMAGE_DOWNLOAD_FAILED,
                    )

                from connect_labs.labs.ai_review_agents.types import ReviewContext

                # Each resolved reviewer runs independently on the same image, with
                # its OWN reading (see _reading_for) — a reviewer that requires a
                # reading and doesn't have one just skips itself rather than
                # blocking the others (see the work_items filter above, which only
                # drops the whole image when EVERY reviewer has nothing to work with).
                runnable = []
                for reviewer in resolved_reviewers:
                    rdg = _reading_for(reviewer.comparison_field, reading_by_field)
                    if reviewer.requires_reading and not rdg:
                        continue
                    runnable.append((reviewer, rdg))

                def _run_one(reviewer_rdg):
                    reviewer, rdg = reviewer_rdg
                    ctx = ReviewContext(
                        images={"scale": img_bytes},
                        form_data={"reading": rdg} if rdg else {},
                        metadata={
                            "visit_id": v_id,
                            "blob_id": b_id,
                            "opportunity_id": img_opp_id,
                            "session_id": session_id,
                        },
                    )
                    ai_n = None
                    ai_c = None
                    err_kind = ""
                    try:
                        rv = reviewer.agent.review(ctx)
                        ai_c = rv.confidence
                        if rv.passed:
                            ai_r = "match"
                            # pass_label provides a human-readable classification for the tile footer
                            # (e.g. "Not Hyperzoomed" for muac_overzoom)
                            ai_n = rv.details.get("pass_label")
                        elif rv.failed:
                            ai_r = "no_match"
                            # badge_label is the display label for the top-left badge and tile footer
                            # (e.g. "Hyperzoomed" instead of generic "No Match")
                            ai_n = rv.details.get("badge_label")
                        else:
                            ai_r = "error"
                            ai_n = "; ".join(rv.errors) if rv.errors else None
                            # Agents that predate the taxonomy simply report unknown
                            # rather than silently vanishing from the run tally.
                            err_kind = rv.details.get("error_kind") or ERROR_KIND_UNKNOWN
                    except Exception as exc:
                        logger.exception(f"{tag} Agent raised exception for blob={b_id}")
                        ai_r = "error"
                        ai_n = str(exc)
                        err_kind = ERROR_KIND_AGENT_EXCEPTION
                    # Per-reviewer trace, logged BEFORE combination collapses the losing
                    # verdicts away — otherwise "muac_overzoom passed but muac_match
                    # failed" is unrecoverable after the fact (see review finding).
                    logger.debug(
                        f"{tag} {reviewer.agent.agent_id}: visit={v_id}, blob={b_id}, "
                        f"reading={rdg}, result={ai_r}, notes={ai_n!r}"
                    )
                    return ReviewerVerdict(
                        reviewer.agent.agent_id, ai_r, ai_n, ai_c, reviewer.ai_to_human_map, err_kind
                    )

                if not runnable:
                    return FetchReviewOutcome(
                        v_id, b_id, q_id, img_qid, None, None, None, None, True, skip_reason=SKIP_NO_REVIEWER_READING
                    )

                # Independent reviewers on the same image (e.g. MUAC OverZoom +
                # MUAC Match) each make their own blocking HTTP call. Running them
                # one after another would double this image's AI-review latency
                # for every extra reviewer on its path — run them concurrently
                # instead. Skip the nested pool for the (common) single-reviewer
                # case, where it would just add thread-creation overhead for no
                # benefit. _run_one already catches its own exceptions, so map()
                # never raises here.
                #
                # Safety: this creates a brand-new, independent pool per call --
                # never resubmit work to the OUTER `pool` (below) from in here or
                # from anything _run_one calls. Submitting back to the same
                # bounded pool a task is already running in is the classic
                # nested-executor deadlock (outer workers all blocked waiting on
                # the outer pool itself, with no worker left to service them).
                #
                # `runnable`'s length comes from admin-configured reviewer specs
                # (ai_reviewers[question_id] in the wizard payload), not a
                # bounded/validated count -- cap max_workers so a misconfigured
                # or oversized reviewer list can't spin up unbounded OS threads
                # (and unbounded concurrent connections to the ML gateway) on
                # top of the outer pool's own _MAX_CONCURRENT_IMAGES_PER_SESSION
                # workers -- see that constant's definition for the combined
                # worst-case concurrency this multiplies out to.
                if len(runnable) == 1:
                    per_agent_results = [_run_one(runnable[0])]
                else:
                    inner_workers = min(len(runnable), _MAX_REVIEWERS_PER_IMAGE)
                    with ThreadPoolExecutor(max_workers=inner_workers) as inner_pool:
                        per_agent_results = list(inner_pool.map(_run_one, runnable))

                ai_result, ai_notes, ai_confidence, human_result = _combine_reviewer_results(per_agent_results)
                # Only genuine classifier fails go to the training-data export -- an
                # "error" verdict (rate limit, gateway hiccup) isn't an AI judgment
                # about the image and would just be noise in classifier_fails.csv.
                fail_verdicts = tuple(v for v in per_agent_results if v.ai_result == "no_match")
                # Every erroring reviewer's cause, not just the winning one --
                # two reviewers on the same image can fail for different reasons
                # and the run tally should see both.
                error_kinds = tuple(v.error_kind for v in per_agent_results if v.ai_result == "error" and v.error_kind)
                return FetchReviewOutcome(
                    v_id,
                    b_id,
                    q_id,
                    img_qid,
                    ai_result,
                    ai_notes,
                    ai_confidence,
                    human_result,
                    False,
                    fail_verdicts,
                    error_kinds=error_kinds,
                )

            def _persist_outcome(outcome):
                # Persist the combined AI result so the classification label is
                # always available to display in the tile footer. human_result is
                # None unless some resolved reviewer's verdict was opted into
                # auto-apply for this image type.
                session.set_assessment(
                    visit_id=int(outcome.visit_id_str),
                    blob_id=outcome.blob_id,
                    question_id=outcome.question_id,
                    result=outcome.human_result,
                    notes="",
                    ai_result=outcome.ai_result,
                    ai_notes=outcome.ai_notes,
                    ai_confidence=outcome.ai_confidence,
                )

            # Session-scoped set of (visit_id_str, blob_id, classifier_id) keys
            # already contributed to session_classifier_fail_rows -- the retry
            # sweep below re-runs EVERY reviewer on a retried image, including
            # one that already produced a definitive no_match on the first
            # pass (only a co-located reviewer's "error" made the image a
            # retry candidate). Without this, a deterministic classifier's
            # same verdict gets exported to classifier_fails.csv a second
            # time whenever its sibling reviewer's error clears on retry.
            exported_fail_keys: set[tuple[str, str, str]] = set()

            def _classifier_fail_rows_for(outcome):
                # One training-data row per failing classifier -- two
                # independent reviewers (e.g. MUAC OverZoom + MUAC Match)
                # can each fail the same image, and each is its own row.
                rows = []
                for verdict in outcome.fail_verdicts:
                    key = (outcome.visit_id_str, outcome.blob_id, verdict.agent_id)
                    if key in exported_fail_keys:
                        continue
                    exported_fail_keys.add(key)
                    opp_for_row, opp_name_for_row = resolve_opportunity_attribution(
                        blob_opportunity_id.get(outcome.blob_id),
                        session.opportunity_id,
                        session.opportunity_name,
                    )
                    rows.append(
                        {
                            "session_id": session.id,
                            "workflow_run_id": session.workflow_run_id,
                            "opportunity_id": opp_for_row,
                            "opportunity_name": opp_name_for_row,
                            "visit_id": int(outcome.visit_id_str),
                            "blob_id": outcome.blob_id,
                            "question_id": outcome.question_id,
                            "classifier_id": verdict.agent_id,
                            "classifier_label": verdict.ai_notes,
                            "ai_confidence": verdict.ai_confidence,
                            # This verdict's OWN implied result, not the
                            # combined outcome.human_result -- if another
                            # reviewer on this image errored, the combine
                            # step lets that error win and human_result
                            # comes back None even though THIS reviewer's
                            # own no_match verdict has a real implied fail.
                            "ai_implied_result": verdict.ai_to_human_map.get(verdict.ai_result),
                        }
                    )
                return rows

            # Images that end this session's first pass with ai_result="error" (a
            # gateway hiccup post_with_retry's own retries didn't clear -- see
            # base.py) get exactly one more attempt after the rest of the batch
            # has run, rather than being a dead end until a human intervenes.
            # Not unbounded: the time already spent on the rest of this
            # session's (and prior sessions') images gives a transient outage
            # room to clear, and a persistent outage shouldn't loop or block
            # the batch -- see the retry sweep below.
            retry_candidates = []

            with ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_IMAGES_PER_SESSION) as pool:
                fut_map = {pool.submit(_fetch_and_review, item): item for item in work_items}
                for fut in as_completed(fut_map):
                    try:
                        outcome = fut.result()
                    except Exception as exc:
                        failed_item = fut_map.get(fut)
                        blob_hint = failed_item[1] if failed_item else "unknown"
                        logger.warning(f"{tag} Unexpected error reviewing image {blob_hint}: {exc}")
                        total_errors += 1
                        error_kind_counts[ERROR_KIND_AGENT_EXCEPTION] = (
                            error_kind_counts.get(ERROR_KIND_AGENT_EXCEPTION, 0) + 1
                        )
                        images_processed += 1
                        continue

                    images_processed += 1
                    # Tallied here rather than inside the pool threads: these are
                    # read-modify-write updates on shared dicts and this consumer
                    # loop is single-threaded, so the counts can't race.
                    for kind in outcome.error_kinds:
                        error_kind_counts[kind] = error_kind_counts.get(kind, 0) + 1
                    if outcome.skipped:
                        total_skipped += 1
                        _note_skip(outcome.skip_reason or SKIP_NO_REVIEWER_READING)
                    else:
                        total_reviewed += 1
                        if outcome.ai_result == "match":
                            total_passed += 1
                            logger.debug(f"{tag} PASS: blob={outcome.blob_id}")
                        elif outcome.ai_result == "no_match":
                            total_failed += 1
                            logger.debug(f"{tag} FAIL: blob={outcome.blob_id}")
                        else:
                            total_errors += 1
                            logger.error(f"{tag} ERROR: blob={outcome.blob_id}, reason={outcome.ai_notes!r}")

                        _persist_outcome(outcome)
                        session_updated = True
                        session_classifier_fail_rows.extend(_classifier_fail_rows_for(outcome))

                        if outcome.ai_result == "error":
                            retry_candidates.append((fut_map[fut], outcome.error_kinds))

                    if progress_callback:
                        progress_callback(
                            images_processed,
                            total_images_to_review,
                            f"Reviewed {images_processed}/{total_images_to_review} images "
                            f"({total_passed} passed, {total_failed} failed)",
                        )

                    if cancel_key and is_audit_creation_cancelled(cancel_key):
                        cancelled = True
                        # This future's own outcome is already recorded above;
                        # drop the rest of this session's not-yet-started work.
                        # Already-running fetches finish but their results are
                        # ignored (the pool still awaits them on __exit__).
                        for pending_fut in fut_map:
                            pending_fut.cancel()
                        logger.info(f"{tag} Cancelled mid-session {session_id} — stopping remaining images")
                        break

            # A cancellation that arrives between the first pass ending and here
            # must still be honored -- otherwise the retry sweep runs to completion
            # (and the session below gets marked ai_review_complete) even though the
            # user asked to stop.
            if retry_candidates and not cancelled and cancel_key and is_audit_creation_cancelled(cancel_key):
                cancelled = True
                logger.info(
                    f"{tag} Cancelled before retry sweep for session {session_id} — "
                    f"skipping {len(retry_candidates)} pending retries"
                )

            if retry_candidates and not cancelled:
                # Back off first if the first pass failed because the gateway was
                # busy -- retrying straight into that is why the sweep only
                # recovered 27% of what it retried. Waited in short slices so a
                # cancel still lands promptly instead of after the full backoff.
                saturation_kinds = sorted(
                    {kind for _, kinds in retry_candidates for kind in kinds} & _SATURATION_ERROR_KINDS
                )
                if saturation_kinds:
                    logger.info(
                        f"{tag} Retry sweep: waiting {_RETRY_SWEEP_BACKOFF_SECONDS:.0f}s before "
                        f"re-attempting -- first pass hit {', '.join(saturation_kinds)}"
                    )
                    waited = 0.0
                    while waited < _RETRY_SWEEP_BACKOFF_SECONDS:
                        if cancel_key and is_audit_creation_cancelled(cancel_key):
                            cancelled = True
                            logger.info(f"{tag} Cancelled during retry-sweep backoff for session {session_id}")
                            break
                        slice_s = min(2.0, _RETRY_SWEEP_BACKOFF_SECONDS - waited)
                        time.sleep(slice_s)
                        waited += slice_s

            if retry_candidates and not cancelled:
                logger.info(
                    f"{tag} Retry sweep: re-attempting {len(retry_candidates)} "
                    f"errored image(s) in session {session_id}"
                )
                with ThreadPoolExecutor(
                    max_workers=min(len(retry_candidates), _MAX_CONCURRENT_IMAGES_PER_SESSION)
                ) as retry_pool:
                    retry_fut_map = {
                        retry_pool.submit(_fetch_and_review, item): (item, first_pass_error_kinds)
                        for item, first_pass_error_kinds in retry_candidates
                    }
                    for fut in as_completed(retry_fut_map):
                        if cancel_key and is_audit_creation_cancelled(cancel_key):
                            cancelled = True
                            for pending_fut in retry_fut_map:
                                pending_fut.cancel()
                            logger.info(
                                f"{tag} Cancelled during retry sweep for session {session_id} — "
                                f"stopping remaining retries"
                            )
                            break

                        retry_item, first_pass_error_kinds = retry_fut_map[fut]
                        try:
                            outcome = fut.result()
                        except Exception as exc:
                            blob_hint = retry_item[1] if retry_item else "unknown"
                            logger.warning(f"{tag} Retry sweep: unexpected error reviewing image {blob_hint}: {exc}")
                            continue

                        if outcome.skipped:
                            continue

                        if outcome.ai_result == "error":
                            # Still an error after the extra attempt -- leave the counts
                            # and persisted message as the first pass left them, so a
                            # persistent outage stays visible instead of being retried
                            # forever.
                            logger.warning(
                                f"{tag} Retry sweep: blob={outcome.blob_id} still errored: " f"{outcome.ai_notes!r}"
                            )
                            continue

                        total_errors -= 1
                        # Retract the first pass's error tally now that it recovered --
                        # mirrors the total_errors decrement above so the run summary's
                        # error breakdown doesn't keep blaming a failure that cleared.
                        for kind in first_pass_error_kinds:
                            if error_kind_counts.get(kind):
                                error_kind_counts[kind] -= 1
                                if error_kind_counts[kind] <= 0:
                                    del error_kind_counts[kind]
                        if outcome.ai_result == "match":
                            total_passed += 1
                        elif outcome.ai_result == "no_match":
                            total_failed += 1
                        logger.info(f"{tag} Retry sweep recovered blob={outcome.blob_id}: now {outcome.ai_result}")
                        _persist_outcome(outcome)
                        session_classifier_fail_rows.extend(_classifier_fail_rows_for(outcome))

            # Resolve image/form/Connect URLs for this session's new classifier-fail
            # rows now, rather than waiting on a human to save/complete the session
            # (see classifier_fail_sync.py, which still backfills these as a safety
            # net if resolution fails here). One resolve call per session, not per
            # row -- matches resolve_urls_by_blob's own batching.
            if session_classifier_fail_rows:
                # session.opportunity_id (NOT the batch-level opp_id -- in a
                # per-FLW multi-opportunity run, a session's real opportunity
                # can differ from the primary opp_id this function was called
                # with, see is_multi_opp_per_flw) is passed as the DEFAULT;
                # resolve_urls_by_blob itself groups by each image's own
                # opportunity_id when present (a multi-opp combined session's
                # images can carry one), same fallback as blob_opportunity_id
                # above.
                # Never let a failure resolving these best-effort training-data
                # URLs prevent the (already-computed, expensive) AI review
                # results below from being saved -- this must not be able to
                # throw out of the try/except this whole session's processing
                # is already wrapped in, or session_updated's save gets skipped
                # even though the actual review succeeded.
                try:
                    urls_by_blob = resolve_urls_by_blob(
                        data_access=data_access,
                        access_token=access_token,
                        opportunity_id=session.opportunity_id,
                        visit_images=session.data.get("visit_images", {}),
                    )
                except Exception:
                    logger.exception(f"{tag} Failed to resolve classifier-fail URLs for session {session_id}")
                    urls_by_blob = {}
                for row in session_classifier_fail_rows:
                    row.update(urls_by_blob.get(row["blob_id"], {}))
                classifier_fail_rows.extend(session_classifier_fail_rows)

            # Save when there are assessments to write OR when the session ran
            # to completion (not cancelled) so a restart skips it entirely.
            # The completion flag is only set when not cancelled mid-review so
            # a restart can re-enter and finish any remaining images.
            if session_updated or not cancelled:
                try:
                    if session_updated:
                        visit_results = session.data.get("visit_results", {})
                        assessment_count = sum(len(vr.get("assessments", {})) for vr in visit_results.values())
                        logger.info(
                            f"{tag} Saving session {session_id} with {assessment_count} assessments "
                            f"in {len(visit_results)} visits"
                        )
                    else:
                        logger.info(f"{tag} No assessments for session {session_id} — checkpointing")
                    if not cancelled:
                        session.data["ai_review_complete"] = True
                    data_access.save_audit_session(session)
                    if session_updated:
                        logger.info(f"{tag} Successfully saved AI results for session {session_id}")
                except Exception as e:
                    logger.warning(f"{tag} Failed to save session {session_id}: {e}")

            session_images = total_reviewed - session_reviewed_before
            session_elapsed = time.monotonic() - session_started_at
            logger.info(
                f"{tag} Session {session_id} done in {session_elapsed:.1f}s: "
                f"images={session_images} errors={total_errors - session_errors_before} "
                f"per_image_s={(session_elapsed / session_images if session_images else 0):.1f}"
            )

        except Exception as e:
            logger.warning(f"{tag} Failed to process session {session_id}: {e}")

        if cancelled:
            break

    if classifier_fail_rows:
        s3_export.record_classifier_fails(classifier_fail_rows)

    elapsed = time.monotonic() - review_started_at

    def _breakdown(counts: dict[str, int]) -> str:
        return ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    # "reviewed" counts every image an agent was RUN on, so it already includes
    # the errors -- passed + failed + errors == reviewed. That reads as "263
    # reviewed" when only 103 produced a verdict, which has actively misled
    # readers into treating the difference as missing work rather than failures.
    # Say the relationship explicitly instead of leaving it to be inferred.
    logger.info(
        f"{tag} Complete in {elapsed:.1f}s: attempted={total_reviewed} "
        f"(passed={total_passed}, failed={total_failed}, errors={total_errors}), "
        f"skipped={total_skipped}, "
        f"per_image_s={(elapsed / total_reviewed if total_reviewed else 0):.1f}"
        + (" (cancelled)" if cancelled else "")
    )
    if error_kind_counts:
        logger.warning(f"{tag} Error breakdown: {_breakdown(error_kind_counts)}")
    if skip_reason_counts:
        logger.info(f"{tag} Skip breakdown: {_breakdown(skip_reason_counts)}")

    # A run that attempted nothing but skipped everything completes "successfully"
    # and produces an empty audit. That is nearly always a misconfigured
    # comparison field rather than an empty dataset, so it is surfaced at ERROR
    # with the exact field paths involved instead of being left to be discovered
    # by a human opening the audit and finding it blank.
    if total_reviewed == 0 and total_skipped > 0:
        logger.error(
            f"{tag} NO IMAGES REVIEWED: all {total_skipped} image(s) were skipped "
            f"({_breakdown(skip_reason_counts)}). This audit will be empty."
        )
        for (image_qid, wanted), info in sorted(missing_reading_fields.items(), key=lambda kv: -kv[1]["count"])[
            :_MAX_LOGGED_MISSING_FIELDS
        ]:
            logger.error(
                f"{tag}   image={image_qid!r} needs reading field {wanted!r} "
                f"-> not present on {info['count']} image(s); fields with values: {info['present'] or 'none'}"
            )
    elif missing_reading_fields:
        # Partial misconfiguration: some images reviewed, others silently dropped.
        for (image_qid, wanted), info in sorted(missing_reading_fields.items(), key=lambda kv: -kv[1]["count"])[
            :_MAX_LOGGED_MISSING_FIELDS
        ]:
            logger.warning(
                f"{tag} Skipped {info['count']} image(s) for image={image_qid!r}: "
                f"reading field {wanted!r} had no value; fields with values: {info['present'] or 'none'}"
            )

    if ai_reviewers is not None:
        summary_agent_id = ",".join(
            sorted({spec["agent_id"] for specs in ai_reviewers.values() for spec in specs if spec.get("agent_id")})
        )
        summary_agent_name = "per-image-type"
    else:
        summary_agent_id = ai_agent_id
        summary_agent_name = get_agent(ai_agent_id).name if ai_agent_id else ""

    return {
        "agent_id": summary_agent_id,
        "agent_name": summary_agent_name,
        "sessions_processed": len(session_ids),
        "total_reviewed": total_reviewed,
        "total_passed": total_passed,
        "total_failed": total_failed,
        "total_errors": total_errors,
        "total_skipped": total_skipped,
        "cancelled": cancelled,
        # Carried on the job record too, not just in the logs -- the run summary
        # is what a human actually reads, and "errors=160" there is as opaque as
        # it was in CloudWatch.
        "error_kinds": dict(error_kind_counts),
        "skip_reasons": dict(skip_reason_counts),
        "elapsed_seconds": round(elapsed, 1),
    }


def _run_duplicate_detection_on_sessions(
    data_access,
    session_ids: list[int],
    access_token: str,
    progress_callback=None,
    cancel_key: str | None = None,
) -> dict:
    """Run group-level duplicate-photo detection on each session and persist.

    Batches each session's images by (FLW, day, photo-type), calls the live
    /detect_duplicates endpoint, and writes non-destructive "Potential Duplicate"
    flags. Runs AFTER per-image AI review so the flag merges additively into any
    existing ai_notes. Non-fatal per session -- a failure on one session is
    logged and the rest proceed.

    INTENTIONALLY SEQUENTIAL across sessions: unlike _run_ai_review_on_sessions,
    which fans image reviews out over a ThreadPoolExecutor, this loop processes
    sessions one at a time -- and each session's run_duplicate_detection makes its
    /detect_duplicates calls sequentially too. Do NOT wrap this in a thread pool
    to run many sessions' detect calls at once: each detect call is a long single
    op, and concurrent calls slow every request enough to be problematic. Modest
    incidental overlap is fine; deliberate parallel fan-out of the detect calls is
    not the intended model.

    ``cancel_key``, when given, is checked between sessions and passed through
    to run_duplicate_detection (which checks it between its own batches too).
    Now that batches are per-FLW rather than per-combined-session, a large
    combined session can make many more sequential (and individually slow --
    up to DETECT_TIMEOUT each) detect calls than before, with no overall time
    limit (CELERY_TASK_TIME_LIMIT is unset) -- cooperative cancellation is the
    only way to stop this stage early. Mirrors the sibling
    visit_cluster_duplicate_detection module's cancellation support.
    """
    from connect_labs.audit.duplicate_detection import build_duplicate_warnings, run_duplicate_detection

    totals = {
        "sessions_processed": 0,
        "groups_detected": 0,
        "images_flagged": 0,
        "batches_processed": 0,
        "skipped_over_limit": 0,
        "skipped_presign": 0,
        "detect_failures": 0,
        "session_errors": 0,  # sessions that raised before finishing detection
        "cancelled": False,
    }
    total = len(session_ids)
    for idx, session_id in enumerate(session_ids, start=1):
        if cancel_key and is_audit_creation_cancelled(cancel_key):
            totals["cancelled"] = True
            break
        try:
            session = data_access.get_audit_session(session_id)
            if not session:
                continue

            if session.data.get("dup_detection_complete"):
                logger.info(f"[DuplicateDetection] Session {session_id} already processed — skipping (resumed run)")
                totals["sessions_processed"] += 1
                continue

            def _cb(p, t, m, _idx=idx):
                if progress_callback:
                    progress_callback(_idx, total, m)

            def _save_now(_session=session, _da=data_access, _sid=session_id):
                try:
                    _da.save_audit_session(_session)
                except Exception as _exc:
                    logger.warning(f"[DuplicateDetection] Per-bucket save failed for session {_sid}: {_exc}")

            summary = run_duplicate_detection(
                session,
                access_token,
                progress_callback=_cb,
                cancel_key=cancel_key,
                data_access=data_access,
                save_callback=_save_now,
            )
            # Always save to capture the per-session summary written after the
            # bucket loop. Only set the completion flag when not cancelled
            # mid-bucket so a restart re-enters and finishes remaining buckets.
            if not summary.get("cancelled"):
                session.data["dup_detection_complete"] = True
            data_access.save_audit_session(session)
            for key in (
                "groups_detected",
                "images_flagged",
                "batches_processed",
                "skipped_over_limit",
                "skipped_presign",
                "detect_failures",
            ):
                totals[key] += summary.get(key, 0)
            totals["sessions_processed"] += 1
            if summary.get("cancelled"):
                totals["cancelled"] = True
                break
        except Exception as e:
            totals["session_errors"] += 1
            logger.warning(f"[DuplicateDetection] Failed to process session {session_id}: {e}")

        if progress_callback:
            progress_callback(idx, total, f"Duplicate detection {idx}/{total} sessions")

    # Build a human-readable note for the run summary whenever ANY part of the
    # de-duplication run failed or was skipped. Empty warnings => clean run.
    warnings, note = build_duplicate_warnings(totals)
    totals["warnings"] = warnings
    totals["note"] = note
    if warnings:
        logger.warning("[DuplicateDetection] %s", note)

    logger.info(f"[DuplicateDetection] Complete across sessions: {totals}")
    return totals


@celery_app.task(bind=True)
def run_audit_creation(
    self,
    access_token: str,
    username: str,
    opportunities: list[dict],
    criteria: dict,
    visit_ids: list[int] | None = None,
    flw_visit_ids: dict | None = None,
    flw_opportunity_ids: dict | None = None,
    template_overrides: dict | None = None,
    workflow_run_id: int | None = None,
    ai_agent_id: str | None = None,
    ai_auto_apply_actions: list[str] | None = None,
    image_audits: list[dict] | None = None,
    context_fields: list[dict] | None = None,
    progress_callback=None,
    cancel_key: str | None = None,
) -> dict:
    """
    Create audit session(s) asynchronously.

    Sessions are self-contained and store their own criteria. If created from
    a workflow, sessions link to the workflow run via labs_record_id.

    Stages:
    1. Fetch visit IDs (if not provided)
    2. Extract images with related fields
    3. Create session(s)
    4. Run AI review agent (if specified)

    Args:
        access_token: OAuth token for API calls
        username: User creating the audit
        opportunities: List of opportunity dicts with id and name
        criteria: Audit criteria dict
        visit_ids: Pre-computed visit IDs (optional, skips fetch)
        flw_visit_ids: Pre-computed FLW->visit_ids mapping (optional)
        flw_opportunity_ids: Pre-computed FLW->opportunity_id mapping (optional). A
            program-owned, multi-opportunity per_flw run has FLWs that each belong
            to exactly one of the selected opportunities -- without this, every
            session in the batch was scoped/stored under `opportunities[0]` alone
            (and image extraction for FLWs outside that one opportunity silently
            came back empty), regardless of which opportunity a given FLW is
            actually in. When provided, each FLW's session and image extraction is
            scoped to ITS real opportunity instead. combined/per_opp granularity
            and legacy callers with no flw_opportunity_ids keep the prior
            single-opportunity behavior unchanged.
        template_overrides: Values to override in criteria (from workflow)
        workflow_run_id: Workflow run ID if triggered from workflow (sessions will link to it)
        ai_agent_id: Optional AI review agent to run after creation
        ai_auto_apply_actions: Which AI verdicts the auditor chose to pre-tag as human
            results. None = legacy per-agent default; a list (possibly empty) selects
            exactly which action keys auto-apply. See ``_build_ai_to_human_result``.
        cancel_key: Cooperative-cancellation identifier to poll (see
            ``is_audit_creation_cancelled``). A direct/wizard call (apply_async)
            omits this and falls back to this task's own ``self.request.id``,
            which is exactly what the caller was handed back and can cancel.
            A workflow job handler that invokes this via ``.apply()`` *inside*
            another already-running task must pass its OWN outer task's id
            explicitly -- ``self.request.id`` here would be a fresh id `.apply()`
            generates for just this nested call, never known outside this
            process, so nothing could ever target it for cancellation.

    Returns:
        Result dict with session_ids, etc.
    """
    # Apply template overrides
    if template_overrides:
        criteria = {**criteria, **template_overrides}

    opportunity_ids = [o["id"] for o in opportunities]
    opp_id = opportunity_ids[0] if opportunity_ids else None
    task_id = self.request.id
    cancel_key = cancel_key or task_id

    logger.info(
        f"[AuditCreation] Starting async audit creation: "
        f"opportunities={opportunity_ids}, user={username}, task_id={task_id}"
    )

    # Parse criteria. AuditCriteria.from_dict picks up enable_time_gap/
    # time_gap_minutes/enable_distance/distance_meters too -- create_audit_session
    # persists them from THIS object (not the raw dict below) onto the session,
    # which is what AuditSessionRecord.to_summary_dict()'s visit_clustering_used
    # later reads back for display. The raw `criteria` dict below is still what
    # build_flw_visit_clusters actually filters visits with -- keep both in sync
    # if these keys are ever renamed.
    audit_criteria = AuditCriteria.from_dict(criteria)
    enable_duplicate_detection = bool(criteria.get("enable_duplicate_detection"))
    granularity = criteria.get("granularity", "combined")
    audit_type = audit_criteria.audit_type

    # Per-image-type reviewers (new wizard) translate into the internal related_fields
    # rules + a question_id -> reviewer map. Legacy payloads (no image_audits) keep using
    # criteria.related_fields and the single ai_agent_id.
    if image_audits is not None:
        from connect_labs.audit.ai_review_config import build_review_config

        related_fields, ai_reviewers = build_review_config(image_audits, context_fields)
    else:
        ai_reviewers = None
        related_fields = audit_criteria.related_fields or []

    # DEBUG: Log the parsed criteria
    logger.info(
        f"[AuditCreation] Parsed criteria: audit_type={audit_type}, "
        f"start_date={audit_criteria.start_date}, end_date={audit_criteria.end_date}, "
        f"count_across_all={audit_criteria.count_across_all}, "
        f"count_per_flw={audit_criteria.count_per_flw}, "
        f"count_per_opp={audit_criteria.count_per_opp}, "
        f"sample_percentage={audit_criteria.sample_percentage}"
    )
    logger.info(f"[AuditCreation] Raw criteria from frontend: {criteria}")

    # Determine stages
    needs_visit_fetch = not visit_ids
    is_per_flw = granularity == "per_flw"
    has_ai_agent = bool(ai_agent_id) or bool(ai_reviewers)
    # Group-level duplicate-photo detection is an independent, opt-in assessment
    # (runs with or without a per-image AI agent). Batched per (FLW, day, type).
    detect_duplicates = bool(criteria.get("detect_duplicates"))
    # Base stages: (fetch visits) + extract images + create sessions + (AI review)
    total_stages = 3 if needs_visit_fetch else 2
    if has_ai_agent:
        total_stages += 1  # Add AI review stage
    if detect_duplicates:
        total_stages += 1  # Add day/FLW/type-bucketed duplicate-detection stage (PR #1070)
    if enable_duplicate_detection:
        total_stages += 1  # Add visit-clustering-grouping duplicate-detection stage

    set_task_progress(
        self,
        "Initializing...",
        current_stage=1,
        total_stages=total_stages,
        stage_name="Initializing",
    )

    # Wall clock per stage. Previously the only way to tell whether a long run
    # was spent fetching, extracting, creating sessions or classifying was to
    # diff the timestamps of unrelated log lines -- and stages that emit no line
    # of their own were invisible entirely.
    _run_started_at = time.monotonic()
    _stage_clock = {"at": _run_started_at}

    def _stage_took() -> float:
        """Seconds since the previous stage boundary, and reset the mark."""
        now = time.monotonic()
        took = now - _stage_clock["at"]
        _stage_clock["at"] = now
        return took

    def _relay(processed, total, message):
        """Forward fine-grained progress to an external relay so the program creator
        can render a per-opp bar that glides per FLW/image, in addition to this
        task's own Celery meta. The relay comes either from an explicit
        ``progress_callback`` (direct calls) or the in-process registry keyed by
        ``workflow_run_id`` — the latter avoids passing a non-serializable closure
        through Celery ``.apply()`` (the eager path serializes its kwargs)."""
        cb = progress_callback or get_relay(workflow_run_id)
        if cb:
            try:
                cb(message, processed=processed, total=total)
            except Exception:
                logger.debug("[AuditCreation] progress relay raised", exc_info=True)

    try:
        # Initialize data access
        mock_request = create_mock_request(access_token, opp_id)
        data_access = AuditDataAccess(opportunity_id=opp_id, request=mock_request)

        # Cache of per-opportunity AuditDataAccess instances. A multi-opportunity
        # per_flw run needs each FLW's images/session scoped to ITS OWN opportunity
        # (see flw_opportunity_ids in the docstring above) rather than reusing the
        # single `data_access` above, which is pinned to opportunities[0].
        _opp_data_access_cache: dict[int, AuditDataAccess] = {opp_id: data_access}

        def _data_access_for_opp(oid: int) -> AuditDataAccess:
            if oid not in _opp_data_access_cache:
                _opp_data_access_cache[oid] = AuditDataAccess(
                    opportunity_id=oid, request=create_mock_request(access_token, oid)
                )
            return _opp_data_access_cache[oid]

        # Update job to running status
        _update_job_progress(
            data_access,
            task_id,
            username,
            status="running",
            current_stage=1,
            total_stages=total_stages,
            stage_name="Initializing",
            message="Starting audit creation...",
        )

        current_stage = 1

        # Immediate first tick so a program-creator row shows life the instant the
        # job starts, instead of sitting dead through the ~20s fetch+extract phases
        # (those only reported to this task's own Celery meta, not the relay).
        _relay(0, 0, "Creating audits · preparing…")

        # =========================================================================
        # STAGE 1: Fetch visit IDs (if not provided)
        # =========================================================================
        if needs_visit_fetch:
            msg = f"Stage {current_stage}/{total_stages}: Fetching visit IDs..."
            set_task_progress(
                self, msg, current_stage=current_stage, total_stages=total_stages, stage_name="Fetching visits"
            )
            _update_job_progress(
                data_access,
                task_id,
                username,
                status="running",
                current_stage=current_stage,
                total_stages=total_stages,
                stage_name="Fetching visits",
                message=msg,
            )

            # Progress callback for granular updates during visit fetching
            def on_visit_fetch_progress(processed: int, total: int, message: str):
                set_task_progress(
                    self,
                    f"Stage {current_stage}/{total_stages}: {message}",
                    current_stage=current_stage,
                    total_stages=total_stages,
                    stage_name="Fetching visits",
                    processed=processed,
                    total=total,
                )
                # Relay to the program-creator row so it glides during fetch, too.
                _relay(processed, total, "Creating audits · fetching visits")

            visit_ids = data_access.get_visit_ids_for_audit(
                opportunity_ids, criteria=audit_criteria, progress_callback=on_visit_fetch_progress
            )
            logger.info(f"[AuditCreation] Stage 'fetch visits' took {_stage_took():.1f}s: {len(visit_ids)} visit IDs")

            current_stage += 1

        # Filter to selected FLWs if provided
        selected_flw_user_ids = criteria.get("selected_flw_user_ids", [])
        if selected_flw_user_ids and flw_visit_ids:
            # Use only visits from selected FLWs
            visit_ids = []
            for flw_id in selected_flw_user_ids:
                visit_ids.extend(flw_visit_ids.get(flw_id, []))
            visit_ids = list(set(visit_ids))
            logger.info(f"[AuditCreation] Filtered to {len(visit_ids)} visits for selected FLWs")

        # Group this run's visits by each FLW's REAL opportunity (only meaningful
        # for is_per_flw with both flw_visit_ids and flw_opportunity_ids provided).
        # Non-empty and containing more than just opp_id means this is a genuine
        # multi-opportunity per_flw batch that needs per-opp extraction below,
        # rather than the single opp_id-scoped call that silently drops every
        # other opportunity's images.
        visits_by_opp: dict[int, list[int]] = {}
        if is_per_flw and flw_visit_ids and flw_opportunity_ids:
            for flw_id in selected_flw_user_ids:
                real_opp = flw_opportunity_ids.get(flw_id, opp_id)
                visits_by_opp.setdefault(real_opp, []).extend(flw_visit_ids.get(flw_id, []))
        is_multi_opp_per_flw = bool(visits_by_opp) and set(visits_by_opp) != {opp_id}

        # =========================================================================
        # STAGE 2: Extract images
        # =========================================================================
        total_visits_for_extraction = len(visit_ids)
        msg = f"Stage {current_stage}/{total_stages}: Extracting images from {total_visits_for_extraction} visits..."
        set_task_progress(
            self, msg, current_stage=current_stage, total_stages=total_stages, stage_name="Extracting images"
        )
        _update_job_progress(
            data_access,
            task_id,
            username,
            status="running",
            current_stage=current_stage,
            total_stages=total_stages,
            stage_name="Extracting images",
            message=msg,
        )

        # Progress callback for granular updates during image extraction
        # Capture current_stage in closure for the callback
        _extraction_stage = current_stage

        def on_extraction_progress(processed: int, total: int, message: str):
            set_task_progress(
                self,
                f"Stage {_extraction_stage}/{total_stages}: {message}",
                current_stage=_extraction_stage,
                total_stages=total_stages,
                stage_name="Extracting images",
                processed=processed,
                total=total,
            )
            # Relay to the program-creator row so it glides during extraction, too.
            _relay(processed, total, "Creating audits · extracting images")

        if is_multi_opp_per_flw:
            # Extract per real opportunity and merge -- a single opp_id-scoped call
            # covering every FLW's visits would silently return no images for any
            # visit outside opportunities[0] (extract_images_for_visits' CommCare
            # fetch is scoped to exactly one opportunity).
            all_visit_images = {}
            for real_opp, opp_visit_ids in visits_by_opp.items():
                opp_images = _data_access_for_opp(real_opp).extract_images_for_visits(
                    opp_visit_ids, real_opp, related_fields=related_fields, progress_callback=on_extraction_progress
                )
                all_visit_images.update(opp_images)
        else:
            all_visit_images = data_access.extract_images_for_visits(
                visit_ids, opp_id, related_fields=related_fields, progress_callback=on_extraction_progress
            )
        image_count = sum(len(imgs) for imgs in all_visit_images.values())
        logger.info(
            f"[AuditCreation] Stage 'extract images' took {_stage_took():.1f}s: "
            f"{image_count} images from {len(visit_ids)} visits"
        )

        # Visit Clustering (optional 3rd filter): fetch visit_date + location once
        # for every visit in this batch, when either checkbox is enabled. Zero cost
        # when disabled -- matches "nothing changes in the output" from the spec.
        clustering_enabled = bool(criteria.get("enable_time_gap") or criteria.get("enable_distance"))
        visit_meta_by_id: dict[str, dict] = {}
        if clustering_enabled:
            try:
                if is_multi_opp_per_flw:
                    for real_opp, opp_visit_ids in visits_by_opp.items():
                        meta_visits = _data_access_for_opp(real_opp).pipeline.fetch_raw_visits(
                            opportunity_id=real_opp, skip_form_json=True, filter_visit_ids=set(opp_visit_ids)
                        )
                        visit_meta_by_id.update({str(v["id"]): v for v in meta_visits})
                else:
                    meta_visits = data_access.pipeline.fetch_raw_visits(
                        opportunity_id=opp_id, skip_form_json=True, filter_visit_ids=set(visit_ids)
                    )
                    visit_meta_by_id = {str(v["id"]): v for v in meta_visits}
            except Exception:
                logger.exception(f"[AuditCreation] Failed to fetch visit metadata for clustering, opp={opp_id}")

        if audit_criteria.exclude_prior_audited:
            from connect_labs.audit.data_access import filter_out_prior_audited

            if is_multi_opp_per_flw:
                prior_index = {}
                for real_opp in visits_by_opp:
                    prior_index.update(_data_access_for_opp(real_opp).get_prior_audited_images(real_opp))
            else:
                prior_index = data_access.get_prior_audited_images(opp_id)
            all_visit_images, excluded_count = filter_out_prior_audited(all_visit_images, prior_index)
            image_count = sum(len(imgs) for imgs in all_visit_images.values())
            logger.info(f"[AuditCreation] Excluded {excluded_count} previously-audited images; {image_count} remain")
            set_task_progress(
                self,
                f"Excluded {excluded_count} previously-audited images",
                current_stage=current_stage,
                total_stages=total_stages,
                stage_name="Extracting images",
            )

        current_stage += 1

        # Cooperative cancellation: if the user cancelled while we were fetching
        # visits / extracting images, abort BEFORE creating any session so a
        # reverted creation can't leave a stray session behind.
        if is_audit_creation_cancelled(cancel_key):
            logger.info(f"[AuditCreation] Task {task_id} cancelled before session creation — aborting")
            _update_job_progress(
                data_access, task_id, username, status="cancelled", message="Cancelled before session creation"
            )
            return {"success": False, "cancelled": True, "sessions": []}

        # =========================================================================
        # STAGE 3: Create session(s)
        # =========================================================================
        msg = f"Stage {current_stage}/{total_stages}: Creating session(s)..."
        set_task_progress(
            self, msg, current_stage=current_stage, total_stages=total_stages, stage_name="Creating sessions"
        )
        _update_job_progress(
            data_access,
            task_id,
            username,
            status="running",
            current_stage=current_stage,
            total_stages=total_stages,
            stage_name="Creating sessions",
            message=msg,
        )

        sessions_created = []
        dup_detection_targets = []
        session_title = criteria.get("title", "")
        session_tag = criteria.get("tag", "")
        session_pass_threshold = criteria.get("pass_threshold", 100)

        # -------------------------------------------------------------------------
        # RESUME: for workflow-triggered runs (stable workflow_run_id), detect
        # sessions created by a previous attempt that crashed before finishing.
        # If found, skip re-creation and let subsequent stages pick up from where
        # they left off (each stage checks its own per-session completion flag).
        # Direct/wizard runs (no workflow_run_id) have no stable run identity
        # across re-triggers, so resume is skipped for those.
        #
        # Matched by run_checkpoints.call_key -- (opportunity, tag, window) --
        # NOT by "any session on this run_id". A workflow run may legitimately
        # create several DIFFERENT audits: the dual-track template's Track A
        # and Track B are separate invocations sharing one run_id (see
        # connect_labs/audit/visit_cluster_duplicate_detection.py's header),
        # and a long-lived creator run (Muac Picture Audit) creates a fresh
        # audit every time the button is pressed. Matching on run_id alone
        # made every one of those after the first a silent no-op that
        # reported the FIRST audit's sessions as its own.
        # -------------------------------------------------------------------------
        resumed_from_existing = False
        existing_flw_usernames: set = set()
        if workflow_run_id:
            # Keyed off the PARSED criteria, because that is the object
            # create_audit_session persists onto the session (see its
            # criteria_dict branch) -- keying off the raw dict here would risk
            # comparing a normalised value against an unnormalised one.
            #
            # One key per opportunity this call SPANS, not just opportunities[0]:
            # a multi-opp per_flw call files each FLW's session under that FLW's
            # own opportunity (see flw_opportunity_ids), so a call whose first
            # opportunity happened to contribute no FLWs would otherwise fail to
            # recognise the sessions it created under the others -- and
            # duplicate every one of them on re-entry.
            this_call_criteria = {
                "tag": session_tag,
                "start_date": audit_criteria.start_date,
                "end_date": audit_criteria.end_date,
            }
            this_call_keys = {call_key(oid, this_call_criteria) for oid in opportunity_ids}
            existing_sessions: list = []
            search_opp_ids = opportunity_ids  # check every opp in this run
            for _oid in search_opp_ids:
                try:
                    existing_sessions.extend(_data_access_for_opp(_oid).get_sessions_by_workflow_run(workflow_run_id))
                except Exception as _exc:
                    logger.warning("[AuditCreation] resume check failed for opp %s: %s", _oid, _exc)
            # Deduplicate by id (a session may be returned by more than one opp scope),
            # then keep only the sessions THIS call would have created.
            seen: set[int] = set()
            deduped = []
            for s in existing_sessions:
                if s.id in seen or session_key(s) not in this_call_keys:
                    continue
                seen.add(s.id)
                deduped.append(s)
            existing_sessions = deduped

            if existing_sessions:
                logger.info(
                    "[AuditCreation] Resuming: found %d existing session(s) for workflow_run_id=%s"
                    " — skipping session creation",
                    len(existing_sessions),
                    workflow_run_id,
                )
                sessions_created = [
                    {
                        "id": s.id,
                        "title": s.data.get("title", ""),
                        "visits": len(s.data.get("visit_ids", [])),
                        "images": s.data.get("image_count", 0),
                    }
                    for s in existing_sessions
                ]
                if enable_duplicate_detection:
                    for s in existing_sessions:
                        if s.data.get("visit_clusters"):
                            try:
                                s_opp_id = int(s.data.get("opportunity_id") or opp_id)
                                blob_meta_by_id = {
                                    img["blob_id"]: {
                                        "visit_id": int(vid_str),
                                        "question_id": img.get("question_id", ""),
                                    }
                                    for vid_str, imgs in s.data.get("visit_images", {}).items()
                                    for img in imgs
                                    if img.get("blob_id")
                                }
                                dup_detection_targets.append(
                                    {
                                        "session": s,
                                        "data_access": _data_access_for_opp(s_opp_id),
                                        "opp_id": s_opp_id,
                                        "clusters": s.data["visit_clusters"],
                                        "blob_meta_by_id": blob_meta_by_id,
                                    }
                                )
                            except (ValueError, KeyError, TypeError) as _exc:
                                logger.warning(
                                    "[AuditCreation] Skipping session %s in resume dup-detection rebuild: %s",
                                    s.id,
                                    _exc,
                                )
                resumed_from_existing = True
                # Per-FLW granularity checkpoints per FLW, not per call. A call
                # that died PART of the way through creation (the process was
                # killed between two of its FLWs) has some sessions and not
                # others; treating "some exist" as "this call is done" would
                # strand every FLW it never reached, permanently. Non-per-FLW
                # granularities produce a single session, so for them existence
                # really is completion.
                existing_flw_usernames = {s.flw_username for s in existing_sessions} if is_per_flw else set()

        # Fetch FLW display names for use in session titles. Loaded lazily,
        # because a resume that turns out to have nothing left to create
        # shouldn't pay for a per-opportunity name fetch it won't use.
        flw_display_names = {}
        _names_loaded = False

        def _load_flw_display_names():
            nonlocal _names_loaded
            if _names_loaded:
                return
            _names_loaded = True
            try:
                name_opp_ids = set(visits_by_opp) if is_multi_opp_per_flw else {opp_id}
                for name_opp_id in name_opp_ids:
                    flw_display_names.update(_data_access_for_opp(name_opp_id).get_flw_names(name_opp_id))
                logger.info(f"[AuditCreation] Loaded {len(flw_display_names)} FLW display names")
            except Exception as e:
                logger.warning(f"[AuditCreation] Failed to load FLW names, using usernames: {e}")

        # id -> name lookup for opportunity_name when a session's real opportunity
        # (flw_opportunity_ids) isn't opportunities[0].
        opp_names_by_id = {o["id"]: o.get("name") for o in opportunities}

        if is_per_flw:
            # Create one session per FLW
            # If flw_visit_ids is provided, use it; otherwise group from extracted images
            if flw_visit_ids and selected_flw_user_ids:
                # Use provided FLW grouping
                flw_groups = {flw_id: flw_visit_ids.get(flw_id, []) for flw_id in selected_flw_user_ids}
            else:
                # Group visits by username from image data
                flw_groups = {}
                for visit_id_str, images in all_visit_images.items():
                    if not images:
                        continue
                    # Get username from first image of this visit
                    flw_username = images[0].get("username", "Unknown")
                    visit_id = int(visit_id_str)
                    if flw_username not in flw_groups:
                        flw_groups[flw_username] = []
                    flw_groups[flw_username].append(visit_id)
                logger.info(f"[AuditCreation] Grouped visits into {len(flw_groups)} FLWs from image data")

            # Whatever a prior invocation already created for this call is kept
            # (its sessions are already in sessions_created, and the review
            # stages resume them from their own completion flags); only the
            # FLWs it never reached are created now.
            if existing_flw_usernames and "" not in existing_flw_usernames:
                flw_groups = {f: v for f, v in flw_groups.items() if f not in existing_flw_usernames}
                logger.info(
                    "[AuditCreation] Resuming per-FLW creation: %d FLW(s) already have a session, %d to create",
                    len(existing_flw_usernames),
                    len(flw_groups),
                )
            elif existing_flw_usernames:
                # At least one existing session's FLW can't be identified
                # (flw_username reads the username off the session's first
                # image, and this one has none). Filling in "the rest" would
                # then mean guessing, and guessing wrong duplicates a real
                # FLW's audit -- worse than leaving a gap a human can close by
                # firing a fresh run. Fall back to creating nothing, which is
                # how this call behaved before per-FLW resume existed.
                logger.warning(
                    "[AuditCreation] Resuming call with %d session(s) whose FLW is unidentifiable; "
                    "skipping creation entirely rather than risk duplicates",
                    len(existing_sessions),
                )
                flw_groups = {}
            if flw_groups:
                _load_flw_display_names()

            total_flws = len(flw_groups)
            for idx, (flw_id, flw_visit_list) in enumerate(flw_groups.items()):
                if not flw_visit_list:
                    continue

                # Filter images to this FLW's visits
                flw_images = {str(vid): all_visit_images.get(str(vid), []) for vid in flw_visit_list}

                # Use display name if available, fallback to username
                flw_display_name = flw_display_names.get(flw_id, flw_id)
                flw_title = f"{flw_display_name} - {session_title}" if session_title else flw_display_name

                flw_clusters = (
                    build_flw_visit_clusters(
                        flw_visit_list,
                        visit_meta_by_id,
                        flw_images,
                        enable_time_gap=bool(criteria.get("enable_time_gap")),
                        time_gap_minutes=criteria.get("time_gap_minutes", 10),
                        enable_distance=bool(criteria.get("enable_distance")),
                        distance_meters=criteria.get("distance_meters", 10),
                    )
                    if clustering_enabled
                    else []
                )

                flw_opp_id = (
                    flw_opportunity_ids.get(flw_id, opp_id)
                    if (is_multi_opp_per_flw and flw_opportunity_ids)
                    else opp_id
                )
                flw_data_access = _data_access_for_opp(flw_opp_id) if is_multi_opp_per_flw else data_access

                session = flw_data_access.create_audit_session(
                    username=username,
                    visit_ids=flw_visit_list,
                    title=flw_title,
                    tag=session_tag,
                    opportunity_id=flw_opp_id,
                    criteria=audit_criteria,
                    opportunity_name=opp_names_by_id.get(flw_opp_id) if opportunities else None,
                    visit_images=flw_images,
                    related_fields=related_fields,
                    workflow_run_id=workflow_run_id,
                    pass_threshold=session_pass_threshold,
                    visit_clusters=flw_clusters,
                    has_ai_reviewer=has_ai_agent,
                )

                if enable_duplicate_detection and flw_clusters:
                    blob_meta_by_id = {}
                    for vid_str, imgs in flw_images.items():
                        for img in imgs:
                            bid = img.get("blob_id")
                            if bid:
                                blob_meta_by_id[bid] = {
                                    "visit_id": int(vid_str),
                                    "question_id": img.get("question_id", ""),
                                }
                    dup_detection_targets.append(
                        {
                            "session": session,
                            "data_access": flw_data_access,
                            "opp_id": flw_opp_id,
                            "clusters": flw_clusters,
                            "blob_meta_by_id": blob_meta_by_id,
                        }
                    )

                sessions_created.append(
                    {
                        "id": session.id,
                        "title": flw_title,
                        "visits": len(flw_visit_list),
                        "images": sum(len(imgs) for imgs in flw_images.values()),
                    }
                )

                set_task_progress(
                    self,
                    f"Stage {current_stage}/{total_stages}: Created session {idx + 1}/{total_flws}",
                    current_stage=current_stage,
                    total_stages=total_stages,
                    stage_name="Creating sessions",
                    processed=idx + 1,
                    total=total_flws,
                )
                _relay(idx + 1, total_flws, f"Creating audits · {idx + 1}/{total_flws} field workers")

            logger.info(
                f"[AuditCreation] Stage 'create sessions' took {_stage_took():.1f}s: "
                f"{len(sessions_created)} per-FLW sessions"
            )
        elif not resumed_from_existing and not is_per_flw:
            # Non-per-FLW granularities produce ONE session, so an existing one
            # really does mean this call finished creating.
            _load_flw_display_names()
            # Create single combined session
            opp_name = opportunities[0].get("name") if opportunities else ""
            combined_title = f"{opp_name} - {session_title}" if session_title else opp_name

            session = data_access.create_audit_session(
                username=username,
                visit_ids=visit_ids,
                title=combined_title,
                tag=session_tag,
                opportunity_id=opp_id,
                criteria=audit_criteria,
                opportunity_name=opp_name,
                visit_images=all_visit_images,
                related_fields=related_fields,
                workflow_run_id=workflow_run_id,
                pass_threshold=session_pass_threshold,
                has_ai_reviewer=has_ai_agent,
            )

            sessions_created.append(
                {
                    "id": session.id,
                    "title": combined_title,
                    "visits": len(visit_ids),
                    "images": image_count,
                }
            )

            logger.info(
                f"[AuditCreation] Stage 'create sessions' took {_stage_took():.1f}s: " f"combined session {session.id}"
            )

        current_stage += 1

        # =========================================================================
        # STAGE 4 (optional): Run AI Review Agent
        # =========================================================================
        ai_review_results = None
        if has_ai_agent and sessions_created:
            msg = f"Stage {current_stage}/{total_stages}: Running AI review..."
            set_task_progress(
                self, msg, current_stage=current_stage, total_stages=total_stages, stage_name="AI Review"
            )
            _update_job_progress(
                data_access,
                task_id,
                username,
                status="running",
                current_stage=current_stage,
                total_stages=total_stages,
                stage_name="AI Review",
                message=msg,
            )

            # Progress callback for AI review
            _ai_review_stage = current_stage
            # Throttle persisted job-record writes — each is an API call, and AI review
            # can fire the callback once per image. The list page polls every 2s, so a
            # ~1.5s cadence keeps it live without flooding the API. Celery task state
            # (set_task_progress) is cheap, so update that on every callback.
            _ai_review_last_write = {"at": 0.0}

            def on_ai_review_progress(processed: int, total: int, message: str):
                set_task_progress(
                    self,
                    f"Stage {_ai_review_stage}/{total_stages}: {message}",
                    current_stage=_ai_review_stage,
                    total_stages=total_stages,
                    stage_name="AI Review",
                    processed=processed,
                    total=total,
                )
                _relay(processed, total, f"AI review · {message}")
                now = time.time()
                if processed >= total or now - _ai_review_last_write["at"] >= 1.5:
                    _ai_review_last_write["at"] = now
                    _update_job_progress(
                        data_access,
                        task_id,
                        username,
                        status="running",
                        current_stage=_ai_review_stage,
                        total_stages=total_stages,
                        stage_name="AI Review",
                        message=message,
                        processed=processed,
                        total=total,
                    )

            try:
                ai_review_results = _run_ai_review_on_sessions(
                    data_access=data_access,
                    session_ids=[s["id"] for s in sessions_created],
                    access_token=access_token,
                    opp_id=opp_id,
                    ai_agent_id=ai_agent_id,
                    auto_apply_actions=ai_auto_apply_actions,
                    ai_reviewers=ai_reviewers,
                    progress_callback=on_ai_review_progress,
                    cancel_key=cancel_key,
                    log_tag=str(task_id or "")[:8],
                )
                logger.info(
                    f"[AuditCreation] Stage 'AI review' took {_stage_took():.1f}s "
                    f"(total run {time.monotonic() - _run_started_at:.1f}s): {ai_review_results}"
                )
                if ai_review_results.get("cancelled"):
                    _update_job_progress(
                        data_access,
                        task_id,
                        username,
                        status="running",
                        current_stage=current_stage,
                        total_stages=total_stages,
                        stage_name="AI Review",
                        message=(
                            f"AI review stopped by user after "
                            f"{ai_review_results.get('total_reviewed', 0)} image(s) — "
                            "sessions are still available for manual review."
                        ),
                    )
            except Exception as e:
                logger.warning(f"[AuditCreation] AI review failed (non-fatal): {e}")
                ai_review_results = {"error": str(e)}

            current_stage += 1

        # =========================================================================
        # STAGE 5 (optional): Duplicate-photo detection (per FLW, per day, per type)
        # =========================================================================
        duplicate_results = None
        if detect_duplicates and sessions_created:
            msg = f"Stage {current_stage}/{total_stages}: Detecting duplicate photos..."
            set_task_progress(
                self, msg, current_stage=current_stage, total_stages=total_stages, stage_name="Duplicate Detection"
            )
            _update_job_progress(
                data_access,
                task_id,
                username,
                status="running",
                current_stage=current_stage,
                total_stages=total_stages,
                stage_name="Duplicate Detection",
                message=msg,
            )
            try:
                duplicate_results = _run_duplicate_detection_on_sessions(
                    data_access=data_access,
                    session_ids=[s["id"] for s in sessions_created],
                    access_token=access_token,
                    cancel_key=cancel_key,
                    progress_callback=lambda p, t, m: (
                        set_task_progress(
                            self,
                            f"Stage {current_stage}/{total_stages}: {m}",
                            current_stage=current_stage,
                            total_stages=total_stages,
                            stage_name="Duplicate Detection",
                            processed=p,
                            total=t,
                        ),
                        _relay(p, t, f"Duplicate detection · {m}"),
                    ),
                )
                logger.info(f"[AuditCreation] Duplicate detection complete: {duplicate_results}")
            except Exception as e:
                logger.warning(f"[AuditCreation] Duplicate detection failed (non-fatal): {e}")
                duplicate_results = {"error": str(e)}

            current_stage += 1

        # =========================================================================
        # STAGE 6 (optional): Duplicate detection over visit-clustering groupings
        # =========================================================================
        dup_detection_results = None
        if enable_duplicate_detection and dup_detection_targets:
            msg = f"Stage {current_stage}/{total_stages}: Checking for duplicates..."
            set_task_progress(
                self,
                msg,
                current_stage=current_stage,
                total_stages=total_stages,
                stage_name="Visit-Cluster Duplicate Detection",
            )
            _update_job_progress(
                data_access,
                task_id,
                username,
                status="running",
                current_stage=current_stage,
                total_stages=total_stages,
                stage_name="Visit-Cluster Duplicate Detection",
                message=msg,
            )

            _dd_stage = current_stage

            def on_dup_detection_progress(processed, total, message):
                set_task_progress(
                    self,
                    f"Stage {_dd_stage}/{total_stages}: {message}",
                    current_stage=_dd_stage,
                    total_stages=total_stages,
                    stage_name="Visit-Cluster Duplicate Detection",
                    processed=processed,
                    total=total,
                )
                _relay(processed, total, f"Duplicate detection · {message}")

            try:
                dup_detection_results = run_grouping_duplicate_detection(
                    dup_detection_targets,
                    get_signed_url=lambda blob_id, oid: _data_access_for_opp(oid).get_attachment_signed_url(
                        blob_id, oid
                    ),
                    progress_callback=on_dup_detection_progress,
                    cancel_key=cancel_key,
                )
                logger.info(f"[AuditCreation] Duplicate detection complete: {dup_detection_results}")
            except Exception as e:
                logger.warning(f"[AuditCreation] Duplicate detection failed (non-fatal): {e}")
                dup_detection_results = {"error": str(e)}

            current_stage += 1

        # Mark complete
        result = {
            "success": True,
            "sessions": sessions_created,
            "total_visits": sum(s["visits"] for s in sessions_created),
            "total_images": sum(s["images"] for s in sessions_created),
            "workflow_run_id": workflow_run_id,
        }
        if ai_review_results:
            result["ai_review"] = ai_review_results
        if duplicate_results:
            result["duplicate_detection"] = duplicate_results

        # Surface any duplicate-detection failures/skips in the run summary. A
        # wholesale Stage-5 exception is stored as {"error": ...}; a partial
        # failure produces a "note" from _run_duplicate_detection_on_sessions.
        duplicate_note = ""
        if duplicate_results:
            if duplicate_results.get("error"):
                duplicate_note = f"Duplicate detection failed: {duplicate_results['error']}"
            else:
                duplicate_note = duplicate_results.get("note") or ""
        if duplicate_note:
            result["duplicate_detection_note"] = duplicate_note

        # Same idea for the visit-clustering-grouping stage -- otherwise its
        # outcome is only visible by drilling into result[...], and with the
        # external endpoint not deployed yet, the expected first-run outcome
        # (every grouping skipped for a missing signed URL) would otherwise
        # look identical to a normal "nothing to check" run.
        dd_note = ""
        if dup_detection_results:
            if dup_detection_results.get("error"):
                dd_note = f"Visit-cluster duplicate detection failed: {dup_detection_results['error']}"
            else:
                dd_warnings = []
                if dup_detection_results.get("cancelled"):
                    dd_warnings.append("stopped by user before all groupings were checked")
                if dup_detection_results.get("errors"):
                    dd_warnings.append(f"{dup_detection_results['errors']} grouping(s) failed the duplicate check")
                if dup_detection_results.get("groupings_skipped"):
                    dd_warnings.append(f"{dup_detection_results['groupings_skipped']} grouping(s) skipped")
                if dup_detection_results.get("skipped_over_limit"):
                    dd_warnings.append(f"{dup_detection_results['skipped_over_limit']} image(s) skipped over the cap")
                if dd_warnings:
                    dd_note = "Visit-cluster duplicate detection: " + "; ".join(dd_warnings) + "."
        if dd_note:
            result["visit_cluster_duplicate_detection_note"] = dd_note

        completion_message = "Audit creation complete"
        if duplicate_note:
            completion_message += f" · {duplicate_note}"
        if dd_note:
            completion_message += f" · {dd_note}"
        # Distinct key from PR #1070's result["duplicate_detection"]
        # (day/FLW/type-bucketed) -- these are two independent optional stages
        # gated by different criteria flags, never the same data.
        if dup_detection_results:
            result["visit_cluster_duplicate_detection"] = dup_detection_results

        set_task_progress(
            self,
            "Complete",
            is_complete=True,
            current_stage=total_stages,
            total_stages=total_stages,
            stage_name="Complete",
            result=result,
        )

        # Update job record to completed
        _update_job_progress(
            data_access,
            task_id,
            username,
            status="completed",
            current_stage=total_stages,
            total_stages=total_stages,
            stage_name="Complete",
            message=completion_message,
            result=result,
        )

        for cached_data_access in _opp_data_access_cache.values():
            cached_data_access.close()

        logger.info(
            f"[AuditCreation] Complete: {len(sessions_created)} sessions, "
            f"{result['total_visits']} visits, {result['total_images']} images"
        )

        return result

    except Exception as e:
        logger.error(f"[AuditCreation] Failed: {e}", exc_info=True)
        set_task_progress(
            self,
            f"Failed: {str(e)}",
            is_complete=True,
            error=str(e),
        )

        for cached_data_access in locals().get("_opp_data_access_cache", {}).values():
            try:
                cached_data_access.close()
            except Exception:
                pass

        # Try to update job record to failed
        try:
            mock_request = create_mock_request(access_token, opp_id)
            err_data_access = AuditDataAccess(opportunity_id=opp_id, request=mock_request)
            _update_job_progress(
                err_data_access,
                task_id,
                username,
                status="failed",
                error=str(e),
            )
            err_data_access.close()
        except Exception:
            pass

        raise
