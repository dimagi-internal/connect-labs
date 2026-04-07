"""
Base classes and utilities for Server-Sent Events (SSE) streaming views.

Provides reusable infrastructure for streaming analysis progress to the frontend.
Includes support for both AnalysisPipeline streaming and Celery task progress streaming.
"""

import json
import logging
import queue
import threading
import time
from collections.abc import Callable, Generator

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, StreamingHttpResponse
from django.views import View

logger = logging.getLogger(__name__)


def send_sse_event(message: str, data: dict | None = None, error: str | None = None) -> str:
    """
    Format a message as a Server-Sent Event.

    Args:
        message: Status message to display
        data: Optional data payload (signals completion if present)
        error: Optional error message

    Returns:
        Formatted SSE event string

    Example:
        >>> send_sse_event("Processing data...")
        'data: {"message": "Processing data...", "complete": false}\\n\\n'

        >>> send_sse_event("Complete", data={"count": 100})
        'data: {"message": "Complete", "complete": true, "data": {"count": 100}}\\n\\n'
    """
    event = {"message": message, "complete": data is not None}
    if data:
        event["data"] = data
    if error:
        event["error"] = error
    return f"data: {json.dumps(event)}\n\n"


class BaseSSEStreamView(LoginRequiredMixin, View):
    """
    Base view for Server-Sent Events (SSE) streaming endpoints.

    Provides common SSE setup, authentication, and error handling.
    Subclasses must implement stream_data() to yield SSE events.

    Features:
    - Automatic authentication check
    - Proper SSE headers (Cache-Control, X-Accel-Buffering)
    - StreamingHttpResponse setup
    - Error handling structure

    Example:
        class MyStreamView(BaseSSEStreamView):
            def stream_data(self, request) -> Generator[str, None, None]:
                yield send_sse_event("Starting...")
                # ... do work ...
                yield send_sse_event("Complete!", data={"result": 123})
    """

    heartbeat_enabled = True
    heartbeat_interval = 20  # seconds between heartbeat comments

    def get(self, request, **kwargs):
        """
        Handle GET request and return streaming response.

        Returns:
            StreamingHttpResponse with text/event-stream content type
        """
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated"}, status=401)

        generator = self.stream_data(request)
        if self.heartbeat_enabled:
            generator = self._with_heartbeat(generator)

        response = StreamingHttpResponse(
            generator,
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # Disable nginx buffering
        return response

    def _with_heartbeat(self, generator, interval=None):
        """Wrap a generator with periodic SSE heartbeat comments.

        Prevents ALB/browser timeouts during long-running blocking operations
        (JSON pagination, data processing) by sending SSE comment lines every
        ``interval`` seconds when the generator isn't yielding real data.

        SSE comment format ``: heartbeat\\n\\n`` keeps the TCP connection
        alive but does not trigger EventSource.onmessage on the frontend.

        Set ``heartbeat_enabled = False`` on a subclass to disable.
        """
        if interval is None:
            interval = self.heartbeat_interval

        data_queue: queue.Queue = queue.Queue(maxsize=100)
        stop_event = threading.Event()

        def _producer():
            try:
                for item in generator:
                    if stop_event.is_set():
                        break
                    while not stop_event.is_set():
                        try:
                            data_queue.put(("data", item), timeout=1)
                            break
                        except queue.Full:
                            continue
            except Exception as e:  # noqa: BLE001
                try:
                    data_queue.put(("error", e), timeout=1)
                except queue.Full:
                    pass
            finally:
                try:
                    data_queue.put(("done", None), timeout=1)
                except queue.Full:
                    pass
                try:
                    generator.close()
                except (GeneratorExit, RuntimeError):
                    pass

        thread = threading.Thread(target=_producer, daemon=True)
        thread.start()

        try:
            while True:
                try:
                    msg_type, value = data_queue.get(timeout=interval)
                    if msg_type == "data":
                        yield value
                    elif msg_type == "done":
                        break
                    elif msg_type == "error":
                        raise value
                except queue.Empty:
                    # No data for `interval` seconds — send SSE comment to keep alive
                    yield ": heartbeat\n\n"
        finally:
            stop_event.set()
            thread.join(timeout=2)

    def stream_data(self, request) -> Generator[str, None, None]:
        """
        Generator that yields SSE events.

        Must be implemented by subclasses.
        Yield strings formatted with send_sse_event().

        Args:
            request: HttpRequest object

        Yields:
            Formatted SSE event strings

        Raises:
            NotImplementedError: If not implemented by subclass
        """
        raise NotImplementedError("Subclasses must implement stream_data()")


class AnalysisPipelineSSEMixin:
    """
    Mixin for SSE views that use AnalysisPipeline.

    Provides common pipeline streaming logic and event conversion.
    Converts AnalysisPipeline events to SSE format.

    Stores result and cache status as instance variables for easy access:
    - self._pipeline_result: The analysis result object
    - self._pipeline_from_cache: Whether the result came from cache

    Example:
        class MyStreamView(AnalysisPipelineSSEMixin, BaseSSEStreamView):
            def stream_data(self, request):
                pipeline = AnalysisPipeline(request)
                stream = pipeline.stream_analysis(config)

                # Stream all progress events as SSE
                yield from self.stream_pipeline_events(stream)

                # Result is now available in self._pipeline_result
                result = self._pipeline_result
                if result:
                    yield send_sse_event("Complete", data={"count": len(result.rows)})
    """

    def __init__(self, *args, **kwargs):
        """Initialize mixin state."""
        super().__init__(*args, **kwargs)
        self._pipeline_result = None
        self._pipeline_from_cache = False

    def stream_pipeline_events(
        self,
        pipeline_stream: Generator,
        send_sse_func: Callable[[str, dict | None, str | None], str] = send_sse_event,
    ) -> Generator[str, None, None]:
        """
        Convert AnalysisPipeline stream events to SSE events.

        Processes all pipeline events (STATUS, DOWNLOAD, RESULT) and yields
        formatted SSE events. Stores the final result in self._pipeline_result
        and cache status in self._pipeline_from_cache.

        Fetch progress events are yielded once per page from the v2 paginated API
        (up to 1000 rows per page). Each event is immediately converted to an SSE
        event for real-time UI updates.

        Args:
            pipeline_stream: Generator from pipeline.stream_analysis()
            send_sse_func: SSE formatting function (defaults to send_sse_event)

        Yields:
            Formatted SSE event strings

        Side Effects:
            Sets self._pipeline_result and self._pipeline_from_cache
        """
        from commcare_connect.labs.analysis.pipeline import EVENT_DOWNLOAD, EVENT_RESULT, EVENT_STATUS

        self._pipeline_result = None
        self._pipeline_from_cache = False

        for event_type, event_data in pipeline_stream:
            if event_type == EVENT_STATUS:
                message = event_data.get("message", "Processing...")
                self._pipeline_from_cache = self._pipeline_from_cache or "cache" in message.lower()
                logger.debug(f"[SSE Mixin] Status event: {message}")
                yield send_sse_func(message)

            elif event_type == EVENT_DOWNLOAD:
                # Fetch progress event - yield immediately for real-time UI updates
                # Each page (up to 1000 rows) from the paginated API triggers one event.
                rows_so_far = event_data.get("rows", 0)
                expected_count = event_data.get("total", 0)
                if expected_count > 0:
                    pct = int(rows_so_far / expected_count * 100)
                    message = f"Fetching visits: {rows_so_far:,} / {expected_count:,} rows ({pct}%)"
                else:
                    message = f"Fetching visits: {rows_so_far:,} rows..."
                logger.debug(f"[SSE Mixin] Fetch progress: {message}")
                yield send_sse_func(message)

            elif event_type == EVENT_RESULT:
                logger.debug("[SSE Mixin] Received result event")
                self._pipeline_result = event_data
                break


class CeleryTaskStreamView(BaseSSEStreamView):
    """
    Base view for streaming Celery task progress via SSE.

    Polls Celery task state and streams progress updates to the frontend.
    Subclasses must implement get_task_id() to extract the task ID from the request.

    Features:
    - Automatic Celery state polling
    - Standard progress data structure (status, message, stage_name, current_stage, etc.)
    - Configurable poll interval
    - Handles SUCCESS, FAILURE, PROGRESS, PENDING states

    Progress data structure:
    {
        "status": "running" | "pending" | "completed" | "failed",
        "message": "Human-readable progress message",
        "stage_name": "Current stage name",
        "current_stage": 1,
        "total_stages": 4,
        "processed": 50,  # Items processed in current stage
        "total": 100,     # Total items in current stage
        "result": {...},  # Only on completion
        "error": "...",   # Only on failure
    }

    Example:
        class MyTaskStreamView(CeleryTaskStreamView):
            def get_task_id(self, request) -> str:
                return self.kwargs.get("task_id")
    """

    poll_interval: float = 0.5  # Seconds between Celery state polls

    def get_task_id(self, request) -> str:
        """
        Extract the Celery task ID from the request.

        Must be implemented by subclasses.

        Args:
            request: HttpRequest object

        Returns:
            Celery task ID string

        Raises:
            NotImplementedError: If not implemented by subclass
        """
        raise NotImplementedError("Subclasses must implement get_task_id()")

    def build_progress_data(self, state: str, info: dict) -> dict:
        """
        Build standard progress data from Celery task state.

        Args:
            state: Celery task state (PENDING, PROGRESS, SUCCESS, FAILURE, etc.)
            info: Task info/meta dict from result.info

        Returns:
            Standard progress data dict
        """
        if state == "PENDING":
            return {
                "status": "pending",
                "message": "Waiting to start...",
            }
        elif state == "PROGRESS":
            return {
                "status": "running",
                "message": info.get("message", "Processing..."),
                "stage_name": info.get("stage_name", ""),
                "current_stage": info.get("current_stage", 1),
                "total_stages": info.get("total_stages", 4),
                "processed": info.get("processed", 0),
                "total": info.get("total", 0),
            }
        elif state == "SUCCESS":
            # When set_task_progress is called with is_complete=True, the result is nested
            # under info['result']. When the task returns naturally, info IS the result.
            if isinstance(info, dict):
                # Check for nested result from set_task_progress(is_complete=True)
                task_result = info.get("result", info)
            else:
                task_result = {}
            return {
                "status": "completed",
                "message": "Complete",
                "result": task_result,
            }
        elif state == "FAILURE":
            error_msg = str(info) if info else "Unknown error"
            return {
                "status": "failed",
                "message": f"Failed: {error_msg}",
                "error": error_msg,
            }
        else:
            return {
                "status": state.lower(),
                "message": f"Status: {state}",
            }

    def stream_data(self, request) -> Generator[str, None, None]:
        """
        Stream Celery task progress as SSE events.

        Polls Celery task state at poll_interval and yields progress updates.
        Only yields when state changes to reduce bandwidth.
        Terminates on SUCCESS or FAILURE.

        Args:
            request: HttpRequest object

        Yields:
            Formatted SSE event strings with progress data
        """
        from celery.result import AsyncResult

        task_id = self.get_task_id(request)
        result = AsyncResult(task_id)
        last_state_json = None

        while True:
            try:
                state = result.state
                # result.info may be an exception object if task failed, so check it's a dict
                info = result.info if isinstance(result.info, dict) else {}

                progress_data = self.build_progress_data(state, info)
                current_json = json.dumps(progress_data)

                # Only send if state changed
                if current_json != last_state_json:
                    yield f"data: {current_json}\n\n"
                    last_state_json = current_json

                # Terminate on final states
                if state in ("SUCCESS", "FAILURE"):
                    break

                time.sleep(self.poll_interval)

            except GeneratorExit:
                break
            except Exception as e:
                logger.error(f"[CeleryTaskStream] Error: {e}")
                yield f"data: {json.dumps({'status': 'error', 'error': str(e)})}\n\n"
                break
