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

from commcare_connect.utils.celery import set_task_progress
from config import celery_app

logger = logging.getLogger(__name__)


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


def _run_ai_review_on_sessions(
    data_access,
    session_ids: list[int],
    ai_agent_id: str,
    access_token: str,
    opp_id: int,
    progress_callback=None,
    auto_apply_actions: list[str] | None = None,
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
    from commcare_connect.labs.ai_review_agents.registry import get_agent

    # Get the agent
    agent = get_agent(ai_agent_id)
    logger.info(f"[AIReview] Running agent '{ai_agent_id}' on {len(session_ids)} sessions")
    logger.info(f"[AIReview] Session IDs to process: {session_ids}")

    # Whether this agent needs a related-field reading value to operate
    requires_reading = getattr(agent, "requires_reading", True)

    # Which AI verdicts should pre-tag a human result (e.g. overzoomed -> fail).
    # Empty when the auditor chose "flag only", so nothing is pre-tagged.
    ai_to_human_result = _build_ai_to_human_result(agent, auto_apply_actions)
    logger.info(f"[AIReview] Auto-apply map for '{ai_agent_id}': {ai_to_human_result}")

    # First pass: count only images that will actually be reviewed
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
                        related_fields = image_data.get("related_fields", [])
                        has_reading = any(rf.get("value") for rf in related_fields)
                        # Agents that don't require a reading count any image with a blob_id
                        if (has_reading or not requires_reading) and image_data.get("blob_id"):
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
            session = data_access.get_audit_session(session_id)
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

            # Phase 1: collect reviewable work items, skip-count the rest.
            # (reading extraction is cheap and done single-threaded)
            work_items = []  # (visit_id_str, blob_id, reading, question_id)
            for visit_id_str, images in visit_images.items():
                logger.debug(f"[AIReview] Visit {visit_id_str}: {len(images)} images")
                for image_data in images:
                    blob_id = image_data.get("blob_id")
                    if not blob_id:
                        continue
                    related_fields = image_data.get("related_fields", [])
                    reading = None
                    question_id = image_data.get("question_id", "")
                    for rf in related_fields:
                        if rf.get("value"):
                            reading = str(rf.get("value"))
                            question_id = rf.get("path") or question_id
                            break
                    if not reading and requires_reading:
                        logger.debug(f"[AIReview] Skipping blob={blob_id}: no reading value and agent requires one")
                        total_skipped += 1
                        images_processed += 1
                        continue
                    work_items.append((visit_id_str, blob_id, reading, question_id))

            # Phase 2: fetch + AI-review all images in parallel.
            # Both the Connect image download and the ML classification call are HTTP-bound,
            # so concurrent workers cut wall-clock time roughly proportional to worker count.
            # httpx.Client (used by both data_access and the agent) is thread-safe.
            def _fetch_and_review(item):
                v_id, b_id, rdg, q_id = item
                try:
                    img_bytes = data_access.download_image_from_connect(b_id, opp_id)
                    if not img_bytes:
                        return (v_id, b_id, q_id, rdg, None, None, True)  # skipped
                except Exception as exc:
                    logger.warning(f"[AIReview] Failed to fetch image {b_id}: {exc}")
                    return (v_id, b_id, q_id, rdg, None, None, True)  # skipped

                from commcare_connect.labs.ai_review_agents.types import ReviewContext

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
                try:
                    rv = agent.review(ctx)
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

                return (v_id, b_id, q_id, rdg, ai_r, ai_n, False)  # not skipped

            with ThreadPoolExecutor(max_workers=5) as pool:
                fut_map = {pool.submit(_fetch_and_review, item): item for item in work_items}
                for fut in as_completed(fut_map):
                    try:
                        visit_id_str, blob_id, question_id, reading, ai_result, ai_notes, skipped = fut.result()
                    except Exception as exc:
                        failed_item = fut_map.get(fut)
                        blob_hint = failed_item[1] if failed_item else "unknown"
                        logger.warning(f"[AIReview] Unexpected error reviewing image {blob_hint}: {exc}")
                        total_errors += 1
                        images_processed += 1
                        continue

                    images_processed += 1
                    if skipped:
                        total_skipped += 1
                    else:
                        total_reviewed += 1
                        if ai_result == "match":
                            total_passed += 1
                            logger.debug(f"[AIReview] PASS: blob={blob_id}, reading={reading}")
                        elif ai_result == "no_match":
                            total_failed += 1
                            logger.debug(f"[AIReview] FAIL: blob={blob_id}, reading={reading}")
                        else:
                            total_errors += 1
                            logger.error(f"[AIReview] ERROR: blob={blob_id}, reason={ai_notes!r}")

                        # Persist AI result for all outcomes so the classification label
                        # is always available to display in the tile footer. human_result is
                        # None unless this verdict was opted into auto-apply at creation time.
                        human_result = ai_to_human_result.get(ai_result)
                        session.set_assessment(
                            visit_id=int(visit_id_str),
                            blob_id=blob_id,
                            question_id=question_id,
                            result=human_result,
                            notes="",
                            ai_result=ai_result,
                            ai_notes=ai_notes,
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

    return {
        "agent_id": ai_agent_id,
        "agent_name": agent.name,
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
    template_overrides: dict | None = None,
    workflow_run_id: int | None = None,
    ai_agent_id: str | None = None,
    ai_auto_apply_actions: list[str] | None = None,
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
        template_overrides: Values to override in criteria (from workflow)
        workflow_run_id: Workflow run ID if triggered from workflow (sessions will link to it)
        ai_agent_id: Optional AI review agent to run after creation
        ai_auto_apply_actions: Which AI verdicts the auditor chose to pre-tag as human
            results. None = legacy per-agent default; a list (possibly empty) selects
            exactly which action keys auto-apply. See ``_build_ai_to_human_result``.

    Returns:
        Result dict with session_ids, etc.
    """
    from commcare_connect.audit.data_access import AuditCriteria, AuditDataAccess, create_mock_request

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
    has_ai_agent = bool(ai_agent_id)
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

    try:
        # Initialize data access
        mock_request = create_mock_request(access_token, opp_id)
        data_access = AuditDataAccess(opportunity_id=opp_id, request=mock_request)

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

        all_visit_images = data_access.extract_images_for_visits(
            visit_ids, opp_id, related_fields=related_fields, progress_callback=on_extraction_progress
        )
        image_count = sum(len(imgs) for imgs in all_visit_images.values())
        logger.info(f"[AuditCreation] Extracted {image_count} images from {len(visit_ids)} visits")

        current_stage += 1

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

        # Fetch FLW display names for use in session titles
        flw_display_names = {}
        try:
            flw_display_names = data_access.get_flw_names(opp_id)
            logger.info(f"[AuditCreation] Loaded {len(flw_display_names)} FLW display names")
        except Exception as e:
            logger.warning(f"[AuditCreation] Failed to load FLW names, using usernames: {e}")

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

                session = data_access.create_audit_session(
                    username=username,
                    visit_ids=flw_visit_list,
                    title=flw_title,
                    tag=session_tag,
                    opportunity_id=opp_id,
                    criteria=audit_criteria,
                    opportunity_name=opportunities[0].get("name") if opportunities else None,
                    visit_images=flw_images,
                    related_fields=related_fields,
                    workflow_run_id=workflow_run_id,
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

            try:
                ai_review_results = _run_ai_review_on_sessions(
                    data_access=data_access,
                    session_ids=[s["id"] for s in sessions_created],
                    ai_agent_id=ai_agent_id,
                    access_token=access_token,
                    opp_id=opp_id,
                    progress_callback=on_ai_review_progress,
                    auto_apply_actions=ai_auto_apply_actions,
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

        data_access.close()

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
