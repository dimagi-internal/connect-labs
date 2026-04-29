"""
Celery tasks for workflow job execution.

Provides async job execution for workflows with:
- Multi-stage support (pipeline + processing)
- Incremental result persistence
- Progress streaming via SSE
- Job handler registry for different job types
"""

import logging
from datetime import datetime

from commcare_connect.utils.celery import set_task_progress
from config import celery_app

logger = logging.getLogger(__name__)


# =============================================================================
# Job Handler Registry
# =============================================================================

JOB_HANDLERS = {}


def register_job_handler(job_type: str):
    """
    Decorator to register a job handler.

    Usage:
        @register_job_handler("scale_validation")
        def handle_scale_validation_job(job_config, access_token, progress_callback):
            ...
    """

    def decorator(func):
        JOB_HANDLERS[job_type] = func
        return func

    return decorator


# =============================================================================
# State Management Helpers
# =============================================================================


def _create_mock_request(access_token: str, opportunity_id: int):
    """Create mock request object for data access in Celery task."""
    import time

    class MockRequest:
        def __init__(self, access_token, opportunity_id):
            self.session = {
                "labs_oauth": {
                    "access_token": access_token,
                    "expires_at": time.time() + 3600,
                }
            }
            self.labs_context = {
                "opportunity_id": opportunity_id,
            }
            self.user = None
            # Mock GET/POST query dicts for pipeline execution
            self.GET = {}
            self.POST = {}

    return MockRequest(access_token, opportunity_id)


def _update_job_state(run_id: int, access_token: str, opportunity_id: int, job_state_updates: dict):
    """
    Update job metadata in workflow run state.

    State path: instance.state.active_job
    """
    from commcare_connect.workflow.data_access import WorkflowDataAccess

    try:
        mock_request = _create_mock_request(access_token, opportunity_id)
        data_access = WorkflowDataAccess(
            request=mock_request,
            access_token=access_token,
            opportunity_id=opportunity_id,
        )

        # Get current run
        run = data_access.get_run(run_id)
        if not run:
            logger.error(f"Run {run_id} not found, cannot update job state")
            data_access.close()
            return

        # Get current active_job state
        current_state = run.data.get("state", {})
        current_job = current_state.get("active_job", {})

        # Merge updates
        updated_job = {**current_job, **job_state_updates}

        # Update run state
        data_access.update_run_state(run_id, {"active_job": updated_job})
        data_access.close()

    except Exception as e:
        logger.error(f"Failed to update job state for run {run_id}: {e}", exc_info=True)


def _save_item_result(run_id: int, access_token: str, opportunity_id: int, item_result: dict):
    """
    Save individual item result to workflow run state.

    State path: instance.state.validation_results[item_id]
    """
    from commcare_connect.workflow.data_access import WorkflowDataAccess

    try:
        mock_request = _create_mock_request(access_token, opportunity_id)
        data_access = WorkflowDataAccess(
            request=mock_request,
            access_token=access_token,
            opportunity_id=opportunity_id,
        )

        # Get current run
        run = data_access.get_run(run_id)
        if not run:
            logger.error(f"Run {run_id} not found, cannot save item result")
            data_access.close()
            return

        # Get current validation_results
        current_state = run.data.get("state", {})
        validation_results = current_state.get("validation_results", {})

        # Add this result
        item_id = str(item_result.get("id", "unknown"))
        validation_results[item_id] = item_result

        # Update run state
        data_access.update_run_state(run_id, {"validation_results": validation_results})
        data_access.close()

    except Exception as e:
        logger.error(f"Failed to save item result for run {run_id}: {e}", exc_info=True)


# =============================================================================
# Main Job Execution Task
# =============================================================================


@celery_app.task(bind=True)
def run_workflow_job(
    self,
    job_config: dict,
    access_token: str,
    run_id: int,
    opportunity_id: int,
) -> dict:
    """
    Execute a multi-stage workflow job asynchronously.

    Stage 1 (optional): Execute pipeline to fetch/process data
    Stage 2: Run job handler (API calls, validation, etc.)

    Results are saved incrementally to workflow run state.
    Progress can be streamed via SSE endpoint.

    Args:
        job_config: Job configuration dict
        access_token: OAuth token for API calls
        run_id: Workflow run ID to save results to
        opportunity_id: Opportunity ID for context

    Returns:
        Job results dict
    """
    job_type = job_config.get("job_type")
    handler = JOB_HANDLERS.get(job_type)

    if not handler:
        error_msg = f"Unknown job type: {job_type}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # Server-side multi-pipeline fetch path (preferred for templates whose
    # pipelines produce large result sets — avoids the FE → BE round trip
    # of all pipeline rows). When the FE sets server_fetch_pipelines=True
    # we resolve the run's workflow definition_id, then fetch every
    # pipeline alias defined on that workflow via WorkflowDataAccess. The
    # job handler then sees the same job_config["pipeline_data"] shape
    # whether it came from the FE or the server.
    #
    # Why this matters: MBW V2 on a 85k-visit opportunity produced a
    # 65 MB POST body that exceeded labs prod's 50 MB upload limit. Even
    # raised, the cost of round-tripping pipeline rows through the
    # browser is wasteful when the BE just generated them moments ago
    # for the same SSE pipeline stream.
    if job_config.get("server_fetch_pipelines") and not job_config.get("pipeline_data"):
        from commcare_connect.workflow.data_access import WorkflowDataAccess

        mock_request = _create_mock_request(access_token, opportunity_id)
        wf_access = WorkflowDataAccess(request=mock_request)
        try:
            run = wf_access.get_run(run_id)
            if run and run.definition_id:
                logger.info(
                    f"[WorkflowJob] server_fetch_pipelines=True: loading pipelines for "
                    f"definition {run.definition_id} (run {run_id}, opp {opportunity_id})"
                )
                job_config["pipeline_data"] = wf_access.get_pipeline_data(run.definition_id, opportunity_id)
                aliases = list(job_config["pipeline_data"].keys())
                row_counts = {a: len(job_config["pipeline_data"][a].get("rows", [])) for a in aliases}
                logger.info(f"[WorkflowJob] Server-side pipeline fetch complete: {row_counts}")
            else:
                logger.warning(
                    f"[WorkflowJob] server_fetch_pipelines=True but run {run_id} has no "
                    f"definition_id; falling through to legacy paths"
                )
        finally:
            wf_access.close()

    # Check if records are passed directly from UI (preferred - allows filtering)
    records = job_config.get("records", [])
    records_from_ui = len(records) > 0

    # Only need pipeline stage if records not provided
    pipeline_source = job_config.get("pipeline_source", {})
    needs_pipeline_stage = not records_from_ui and bool(pipeline_source.get("pipeline_id"))
    total_stages = 2 if needs_pipeline_stage else 1

    logger.info(
        f"[WorkflowJob] Starting job: type={job_type}, run={run_id}, "
        f"records_from_ui={len(records) if records_from_ui else 'no'}, stages={total_stages}"
    )

    # Initialize job state
    _update_job_state(
        run_id,
        access_token,
        opportunity_id,
        {
            "job_id": self.request.id,
            "job_type": job_type,
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "current_stage": 1,
            "total_stages": total_stages,
            "stage_name": "Loading pipeline data" if needs_pipeline_stage else "Processing",
            "processed": 0,
            "total": 0,
        },
    )

    # =========================================================================
    # STAGE 1: Pipeline Execution (only if records not provided by UI)
    # =========================================================================
    if needs_pipeline_stage:
        pipeline_id = pipeline_source["pipeline_id"]
        logger.info(f"[WorkflowJob] Stage 1: Executing pipeline {pipeline_id}")

        def pipeline_progress(message: str):
            """Stream pipeline progress."""
            stage_msg = f"Stage 1/{total_stages}: {message}"
            set_task_progress(
                self,
                stage_msg,
                current_stage=1,
                total_stages=total_stages,
                stage_name="Loading pipeline data",
            )

        pipeline_progress("Connecting to data source...")

        try:
            from commcare_connect.workflow.data_access import PipelineDataAccess

            mock_request = _create_mock_request(access_token, opportunity_id)
            pipeline_access = PipelineDataAccess(
                request=mock_request,
                access_token=access_token,
                opportunity_id=opportunity_id,
            )

            result = pipeline_access.execute_pipeline(pipeline_id, opportunity_id)
            records = result.get("rows", [])
            pipeline_access.close()

            pipeline_progress(f"Loaded {len(records)} records")
            logger.info(f"[WorkflowJob] Pipeline loaded {len(records)} records")

            # Save pipeline data to state
            _update_job_state(
                run_id,
                access_token,
                opportunity_id,
                {
                    "pipeline_loaded": True,
                    "pipeline_record_count": len(records),
                },
            )

        except Exception as e:
            logger.error(f"[WorkflowJob] Pipeline execution failed: {e}", exc_info=True)
            _update_job_state(
                run_id,
                access_token,
                opportunity_id,
                {
                    "status": "failed",
                    "error": f"Pipeline error: {e}",
                    "failed_at": datetime.now().isoformat(),
                },
            )
            raise
    elif records_from_ui:
        logger.info(f"[WorkflowJob] Using {len(records)} records from UI (skipping pipeline stage)")

    # =========================================================================
    # STAGE 2: Processing (API calls, validation, etc.)
    # Note: If records came from UI, this is actually Stage 1 (single stage job)
    # =========================================================================
    processing_stage = 2 if needs_pipeline_stage else 1
    total = len(records)

    logger.info(f"[WorkflowJob] Stage {processing_stage}: Processing {total} records")

    _update_job_state(
        run_id,
        access_token,
        opportunity_id,
        {
            "current_stage": processing_stage,
            "stage_name": "Processing",
            "processed": 0,
            "total": total,
        },
    )

    def progress_callback(
        message: str,
        processed: int = 0,
        total: int = 0,
        item_result: dict | None = None,
    ):
        """Progress callback for job handlers."""
        stage_msg = f"Stage {processing_stage}/{total_stages}: {message}"
        extra_meta = {
            "current_stage": processing_stage,
            "total_stages": total_stages,
            "stage_name": "Processing",
            "processed": processed,
            "total": total,
        }

        if item_result:
            extra_meta["item_result"] = item_result

        set_task_progress(self, stage_msg, **extra_meta)

        # Update job state with progress
        _update_job_state(
            run_id,
            access_token,
            opportunity_id,
            {
                "processed": processed,
                "total": total,
            },
        )

        # Save individual item result
        if item_result:
            _save_item_result(run_id, access_token, opportunity_id, item_result)

    try:
        # Pass records and opportunity_id to handler
        job_config["records"] = records
        job_config["opportunity_id"] = opportunity_id
        results = handler(job_config, access_token, progress_callback)

        # Mark complete
        _update_job_state(
            run_id,
            access_token,
            opportunity_id,
            {
                "status": "completed",
                "completed_at": datetime.now().isoformat(),
                "summary": {
                    "successful": results.get("successful", 0),
                    "failed": results.get("failed", 0),
                },
            },
        )

        logger.info(
            f"[WorkflowJob] Job complete: {results.get('successful', 0)} successful, "
            f"{results.get('failed', 0)} failed"
        )

        return results

    except Exception as e:
        logger.error(f"[WorkflowJob] Job failed: {e}", exc_info=True)
        _update_job_state(
            run_id,
            access_token,
            opportunity_id,
            {
                "status": "failed",
                "error": str(e),
                "failed_at": datetime.now().isoformat(),
            },
        )
        raise


# =============================================================================
# Job Handlers
# =============================================================================


def _fetch_image_from_connect(access_token: str, opportunity_id: int, blob_id: str) -> bytes:
    """
    Fetch image bytes from Connect API.

    Uses the same endpoint pattern as AuditDataAccess.download_image_from_connect.

    Args:
        access_token: OAuth token for Connect API
        opportunity_id: Opportunity ID for image context
        blob_id: Blob ID of the image

    Returns:
        Image bytes

    Raises:
        Exception: If image fetch fails
    """
    import httpx
    from django.conf import settings

    production_url = settings.CONNECT_PRODUCTION_URL.rstrip("/")
    url = f"{production_url}/export/opportunity/{opportunity_id}/image/"

    logger.info(f"[ImageFetch] Fetching image blob_id={blob_id} from opportunity={opportunity_id}")
    logger.debug(f"[ImageFetch] URL: {url}")

    try:
        with httpx.Client(
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=120.0,  # Match AuditDataAccess timeout
        ) as client:
            response = client.get(
                url,
                params={"blob_id": blob_id},
            )

            if response.status_code == 401:
                logger.error("[ImageFetch] Authentication failed (401) - token may be expired")
                raise Exception("Authentication failed - OAuth token may have expired")

            if response.status_code == 404:
                logger.error(f"[ImageFetch] Image not found (404) - blob_id={blob_id}")
                raise Exception(f"Image not found: blob_id={blob_id}")

            response.raise_for_status()

            content_length = len(response.content)
            logger.info(f"[ImageFetch] Successfully fetched image: {content_length} bytes")

            return response.content

    except httpx.TimeoutException as e:
        logger.error(f"[ImageFetch] Timeout fetching image blob_id={blob_id}: {e}")
        raise Exception(f"Timeout fetching image: {e}")
    except httpx.HTTPStatusError as e:
        logger.error(f"[ImageFetch] HTTP error {e.response.status_code} fetching image: {e}")
        raise Exception(f"HTTP error {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        logger.error(f"[ImageFetch] Unexpected error fetching image blob_id={blob_id}: {e}", exc_info=True)
        raise


def _get_blob_id_from_images(record: dict, image_filename: str) -> str | None:
    """
    Look up the actual blob_id (UUID) from the images array by matching filename.

    The form field contains the filename (e.g., "1769423067340.jpg"), but the
    API needs the blob_id UUID. The images array contains both.

    Args:
        record: Record dict that should contain an 'images' array
        image_filename: The filename to look up

    Returns:
        The blob_id UUID if found, or None
    """
    images = record.get("images", [])
    if not images or not image_filename:
        return None

    # Handle case where images might be a single dict instead of list
    if isinstance(images, dict):
        images = [images]

    for image in images:
        if isinstance(image, dict):
            if image.get("name") == image_filename:
                return image.get("blob_id")

    # If no exact match, try partial match (filename might not include path)
    filename_only = image_filename.split("/")[-1] if "/" in image_filename else image_filename
    for image in images:
        if isinstance(image, dict):
            img_name = image.get("name", "")
            if img_name == filename_only or img_name.endswith(filename_only):
                return image.get("blob_id")

    return None


@register_job_handler("scale_validation")
def handle_scale_validation_job(job_config: dict, access_token: str, progress_callback) -> dict:
    """
    Handle scale validation job - validate weight readings for multiple records.

    Uses ScaleValidationClient to validate that user-entered weight readings
    match what's shown in scale images.

    Args:
        job_config: Job configuration with params and records
        access_token: OAuth token for fetching images
        progress_callback: Callback for progress updates

    Returns:
        Results dict with successful/failed counts and item details
    """
    from commcare_connect.labs.ai_review_agents.agents.scale_validation import (
        ScaleValidationAgent as ScaleValidationClient,
    )
    from commcare_connect.labs.ai_review_agents.agents.scale_validation import ScaleValidationError

    params = job_config.get("params", {})
    image_filename_field = params.get("image_field", "scale_image_filename")
    reading_field = params.get("reading_field", "weight_reading")
    opportunity_id = job_config.get("opportunity_id")
    records = job_config.get("records", [])
    total = len(records)

    logger.info(f"[ScaleValidation] Processing {total} records for opportunity {opportunity_id}")

    if not opportunity_id:
        raise ValueError("opportunity_id required in job_config for scale validation")

    results = {
        "successful": 0,
        "failed": 0,
        "skipped": 0,
        "items": [],
        "errors": [],
    }

    with ScaleValidationClient() as validator:
        for i, record in enumerate(records):
            # Try multiple ID fields - pipeline data may use various field names
            record_id = (
                record.get("id")
                or record.get("visit_id")
                or record.get("beneficiary_case_id")
                or record.get("entity_id")
                or str(i)
            )

            try:
                # Get filename from form field, then look up actual blob_id UUID
                image_filename = record.get(image_filename_field)
                blob_id = _get_blob_id_from_images(record, image_filename) if image_filename else None
                reading = str(record.get(reading_field, ""))

                if not image_filename or not reading:
                    item_result = {
                        "id": record_id,
                        "status": "skipped",
                        "reason": "Missing image filename or weight reading",
                    }
                    results["items"].append(item_result)
                    results["skipped"] += 1

                    progress_callback(
                        f"Validating {i+1}/{total} (skipped)",
                        processed=i + 1,
                        total=total,
                        item_result=item_result,
                    )
                    continue

                if not blob_id:
                    item_result = {
                        "id": record_id,
                        "status": "skipped",
                        "reason": f"Could not find blob_id for image: {image_filename}",
                    }
                    results["items"].append(item_result)
                    results["skipped"] += 1

                    logger.warning(
                        f"[ScaleValidation] No blob_id found for filename '{image_filename}' "
                        f"in record {record_id}. Available images: {record.get('images', [])}"
                    )

                    progress_callback(
                        f"Validating {i+1}/{total} (skipped - no blob_id)",
                        processed=i + 1,
                        total=total,
                        item_result=item_result,
                    )
                    continue

                # Fetch image from Connect API
                image_bytes = _fetch_image_from_connect(access_token, opportunity_id, blob_id)

                # Validate reading against image
                api_result = validator.validate_reading(image_bytes, reading)

                item_result = {
                    "id": record_id,
                    "status": "validated",
                    "match": api_result.get("match"),
                    "reading": reading,
                }
                results["items"].append(item_result)
                results["successful"] += 1

                progress_callback(
                    f"Validating {i+1}/{total}",
                    processed=i + 1,
                    total=total,
                    item_result=item_result,
                )

            except ScaleValidationError as e:
                logger.warning(f"[ScaleValidation] Validation error for {record_id}: {e}")
                item_result = {
                    "id": record_id,
                    "status": "error",
                    "error": str(e),
                }
                results["items"].append(item_result)
                results["errors"].append({"id": record_id, "error": str(e)})
                results["failed"] += 1

                progress_callback(
                    f"Validating {i+1}/{total} (error)",
                    processed=i + 1,
                    total=total,
                    item_result=item_result,
                )

            except Exception as e:
                logger.error(f"[ScaleValidation] Unexpected error for {record_id}: {e}", exc_info=True)
                item_result = {
                    "id": record_id,
                    "status": "error",
                    "error": str(e),
                }
                results["items"].append(item_result)
                results["errors"].append({"id": record_id, "error": str(e)})
                results["failed"] += 1

                progress_callback(
                    f"Validating {i+1}/{total} (error)",
                    processed=i + 1,
                    total=total,
                    item_result=item_result,
                )

    logger.info(
        f"[ScaleValidation] Complete: {results['successful']} successful, "
        f"{results['failed']} failed, {results['skipped']} skipped"
    )

    return results


@register_job_handler("pipeline_only")
def handle_pipeline_only_job(job_config: dict, access_token: str, progress_callback) -> dict:
    """
    Handle pipeline-only job - just execute pipeline and save results.

    This is useful for workflows that only need to load data without
    additional processing.

    Args:
        job_config: Job configuration with pipeline_source
        access_token: OAuth token for API access
        progress_callback: Callback for progress updates

    Returns:
        Results dict with row count
    """
    records = job_config.get("records", [])
    total = len(records)

    progress_callback(f"Loaded {total} records", processed=total, total=total)

    return {
        "successful": total,
        "failed": 0,
        "items": [{"id": i, "status": "loaded"} for i in range(total)],
        "errors": [],
    }


# Import job handler modules to trigger registration
import commcare_connect.workflow.job_handlers  # noqa: F401, E402
