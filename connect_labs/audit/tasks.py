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
from connect_labs.audit.visit_clustering import build_flw_visit_clusters
from connect_labs.utils.celery import set_task_progress
from connect_labs.utils.progress_relays import _RELAYS as AUDIT_PROGRESS_RELAYS  # noqa: F401  (back-compat alias)
from connect_labs.utils.progress_relays import get_relay

logger = logging.getLogger(__name__)

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

    ai_notes = "; ".join(v.ai_notes for v in winning if v.ai_notes) or None
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


def _run_ai_review_on_sessions(
    data_access,
    session_ids: list[int],
    access_token: str,
    opp_id: int,
    ai_agent_id: str | None = None,
    auto_apply_actions: list[str] | None = None,
    ai_reviewers: dict | None = None,
    progress_callback=None,
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

    Returns:
        Dict with review results summary
    """
    from connect_labs.labs.ai_review_agents.registry import get_agent

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
        logger.info(f"[AIReview] Per-image-type review on {len(session_ids)} sessions: {ai_reviewers}")
    else:
        logger.info(f"[AIReview] Running agent '{ai_agent_id}' on {len(session_ids)} sessions")

    # First pass: count only images that have a reviewer AND meet its reading requirement
    total_images_to_review = 0
    session_image_counts = {}
    for session_id in session_ids:
        try:
            session = data_access.get_audit_session(session_id, try_multiple_opportunities=True)
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

    for session_id in session_ids:
        try:
            # Get session data
            session = data_access.get_audit_session(session_id, try_multiple_opportunities=True)
            if not session:
                logger.warning(f"[AIReview] Session {session_id} not found")
                continue

            # Get visit_images from session data
            # This contains the images and their related field data
            visit_images = session.data.get("visit_images", {})
            logger.info(
                f"[AIReview] Session {session_id}: found {len(visit_images)} visits with images, "
                f"data keys: {list(session.data.keys())}"
            )
            if not visit_images:
                logger.info(f"[AIReview] Session {session_id} has no visit_images")
                continue

            # Track if we made any updates to this session
            session_updated = False

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
                logger.debug(f"[AIReview] Visit {visit_id_str}: {len(images)} images")
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
                        logger.debug(f"[AIReview] Skipping blob={blob_id}: no reviewer has a reading it can use")
                        total_skipped += 1
                        images_processed += 1
                        continue
                    work_items.append((visit_id_str, blob_id, reading_by_field, question_id, image_qid))

            # Phase 2: fetch + AI-review all images in parallel.
            # Both the Connect image download and the ML classification call are HTTP-bound,
            # so concurrent workers cut wall-clock time roughly proportional to worker count.
            # httpx.Client (used by both data_access and the agent) is thread-safe.
            def _fetch_and_review(item):
                v_id, b_id, reading_by_field, q_id, img_qid = item
                resolved_reviewers = resolve(img_qid)
                try:
                    img_bytes = data_access.download_image_from_connect(b_id, opp_id)
                    if not img_bytes:
                        return FetchReviewOutcome(v_id, b_id, q_id, img_qid, None, None, None, None, True)
                except Exception as exc:
                    logger.warning(f"[AIReview] Failed to fetch image {b_id}: {exc}")
                    return FetchReviewOutcome(v_id, b_id, q_id, img_qid, None, None, None, None, True)

                from connect_labs.labs.ai_review_agents.types import ReviewContext

                # Each resolved reviewer runs independently on the same image, with
                # its OWN reading (see _reading_for) — a reviewer that requires a
                # reading and doesn't have one just skips itself rather than
                # blocking the others (see the work_items filter above, which only
                # drops the whole image when EVERY reviewer has nothing to work with).
                per_agent_results: list[ReviewerVerdict] = []
                for reviewer in resolved_reviewers:
                    rdg = _reading_for(reviewer.comparison_field, reading_by_field)
                    if reviewer.requires_reading and not rdg:
                        continue
                    ctx = ReviewContext(
                        images={"scale": img_bytes},
                        form_data={"reading": rdg} if rdg else {},
                        metadata={
                            "visit_id": v_id,
                            "blob_id": b_id,
                            "opportunity_id": opp_id,
                            "session_id": session_id,
                        },
                    )
                    ai_n = None
                    ai_c = None
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
                    except Exception as exc:
                        logger.exception(f"[AIReview] Agent raised exception for blob={b_id}")
                        ai_r = "error"
                        ai_n = str(exc)
                    # Per-reviewer trace, logged BEFORE combination collapses the losing
                    # verdicts away — otherwise "muac_overzoom passed but muac_match
                    # failed" is unrecoverable after the fact (see review finding).
                    logger.debug(
                        f"[AIReview] {reviewer.agent.agent_id}: visit={v_id}, blob={b_id}, "
                        f"reading={rdg}, result={ai_r}, notes={ai_n!r}"
                    )
                    per_agent_results.append(
                        ReviewerVerdict(reviewer.agent.agent_id, ai_r, ai_n, ai_c, reviewer.ai_to_human_map)
                    )

                if not per_agent_results:
                    return FetchReviewOutcome(v_id, b_id, q_id, img_qid, None, None, None, None, True)

                ai_result, ai_notes, ai_confidence, human_result = _combine_reviewer_results(per_agent_results)
                return FetchReviewOutcome(
                    v_id, b_id, q_id, img_qid, ai_result, ai_notes, ai_confidence, human_result, False
                )

            with ThreadPoolExecutor(max_workers=5) as pool:
                fut_map = {pool.submit(_fetch_and_review, item): item for item in work_items}
                for fut in as_completed(fut_map):
                    try:
                        outcome = fut.result()
                    except Exception as exc:
                        failed_item = fut_map.get(fut)
                        blob_hint = failed_item[1] if failed_item else "unknown"
                        logger.warning(f"[AIReview] Unexpected error reviewing image {blob_hint}: {exc}")
                        total_errors += 1
                        images_processed += 1
                        continue

                    images_processed += 1
                    if outcome.skipped:
                        total_skipped += 1
                    else:
                        total_reviewed += 1
                        if outcome.ai_result == "match":
                            total_passed += 1
                            logger.debug(f"[AIReview] PASS: blob={outcome.blob_id}")
                        elif outcome.ai_result == "no_match":
                            total_failed += 1
                            logger.debug(f"[AIReview] FAIL: blob={outcome.blob_id}")
                        else:
                            total_errors += 1
                            logger.error(f"[AIReview] ERROR: blob={outcome.blob_id}, reason={outcome.ai_notes!r}")

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
                        session_updated = True

                    if progress_callback:
                        progress_callback(
                            images_processed,
                            total_images_to_review,
                            f"Reviewed {images_processed}/{total_images_to_review} images "
                            f"({total_passed} passed, {total_failed} failed)",
                        )

            # Save session if we made any updates
            if session_updated:
                try:
                    # Debug: log the visit_results before saving
                    visit_results = session.data.get("visit_results", {})
                    assessment_count = sum(len(vr.get("assessments", {})) for vr in visit_results.values())
                    logger.info(
                        f"[AIReview] Saving session {session_id} with {assessment_count} assessments "
                        f"in {len(visit_results)} visits"
                    )
                    data_access.save_audit_session(session)
                    logger.info(f"[AIReview] Successfully saved AI results for session {session_id}")
                except Exception as e:
                    logger.warning(f"[AIReview] Failed to save session {session_id}: {e}")
            else:
                logger.info(f"[AIReview] No updates to save for session {session_id}")

        except Exception as e:
            logger.warning(f"[AIReview] Failed to process session {session_id}: {e}")

    logger.info(
        f"[AIReview] Complete: reviewed={total_reviewed}, "
        f"passed={total_passed}, failed={total_failed}, errors={total_errors}, skipped={total_skipped}"
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
    }


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

    Returns:
        Result dict with session_ids, etc.
    """
    # Apply template overrides
    if template_overrides:
        criteria = {**criteria, **template_overrides}

    opportunity_ids = [o["id"] for o in opportunities]
    opp_id = opportunity_ids[0] if opportunity_ids else None
    task_id = self.request.id

    logger.info(
        f"[AuditCreation] Starting async audit creation: "
        f"opportunities={opportunity_ids}, user={username}, task_id={task_id}"
    )

    # Parse criteria
    audit_criteria = AuditCriteria.from_dict(criteria)
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
    # Base stages: (fetch visits) + extract images + create sessions + (AI review)
    total_stages = 3 if needs_visit_fetch else 2
    if has_ai_agent:
        total_stages += 1  # Add AI review stage

    set_task_progress(
        self,
        "Initializing...",
        current_stage=1,
        total_stages=total_stages,
        stage_name="Initializing",
    )

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
            logger.info(f"[AuditCreation] Fetched {len(visit_ids)} visit IDs")

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
        logger.info(f"[AuditCreation] Extracted {image_count} images from {len(visit_ids)} visits")

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
        if is_audit_creation_cancelled(task_id):
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
        session_title = criteria.get("title", "")
        session_tag = criteria.get("tag", "")
        session_pass_threshold = criteria.get("pass_threshold", 100)

        # Fetch FLW display names for use in session titles
        flw_display_names = {}
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

            logger.info(f"[AuditCreation] Created {len(sessions_created)} per-FLW sessions")
        elif not is_per_flw:
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

            logger.info(f"[AuditCreation] Created combined session {session.id}")

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
                )
                logger.info(f"[AuditCreation] AI review complete: {ai_review_results}")
            except Exception as e:
                logger.warning(f"[AuditCreation] AI review failed (non-fatal): {e}")
                ai_review_results = {"error": str(e)}

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
            message="Audit creation complete",
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
