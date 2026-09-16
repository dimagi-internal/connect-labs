"""
Base classes and utilities for Server-Sent Events (SSE) streaming views.

Provides reusable infrastructure for streaming analysis progress to the frontend.
Includes support for both AnalysisPipeline streaming and Celery task progress streaming.
"""

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable, Generator

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, StreamingHttpResponse
from django.views import View

logger = logging.getLogger(__name__)


def build_task_progress(state: str, info: dict | None) -> dict:
    """Translate a Celery task ``(state, info)`` into the canonical flat progress
    dict consumed by the frontend (see ``TaskProgress`` in ``static/js/task-progress.ts``).

    This is the ONE place that maps Celery meta to the UI shape. Every consumer —
    the SSE stream views and the JSON poll endpoints, for both workflow jobs and
    audit creation — must go through here so the shape can never drift between
    transports (it used to be copy-pasted in four places).

    Shape::

        {"status": "pending|running|completed|failed|cancelled|<state>",
         "message": str,
         # running only:
         "stage_name": str, "current_stage": int, "total_stages": int,
         "processed": int, "total": int, "item_result"?: dict,
         # completed only:
         "result": dict,
         # failed only:
         "error": str}

    ``total_stages`` defaults to 1 (single-stage → the stage indicator hides), not
    a guessed 4 — tasks that genuinely have stages set it explicitly via
    ``set_task_progress``.
    """
    # On FAILURE, info is typically the exception (not a dict) and carries the real
    # message — keep the raw value for that branch before normalizing to a dict.
    meta = info if isinstance(info, dict) else {}
    if state == "PENDING":
        return {"status": "pending", "message": "Waiting to start..."}
    if state == "PROGRESS":
        out = {
            "status": "running",
            "message": meta.get("message", "Processing..."),
            "stage_name": meta.get("stage_name", ""),
            "current_stage": meta.get("current_stage", 1),
            "total_stages": meta.get("total_stages", 1),
            "processed": meta.get("processed", 0),
            "total": meta.get("total", 0),
        }
        # Per-item payload for live row updates (e.g. per-opp / per-FLW rows).
        if meta.get("item_result") is not None:
            out["item_result"] = meta["item_result"]
        return out
    if state == "SUCCESS":
        # set_task_progress(is_complete=True) nests the payload under info['result'];
        # a naturally-returned task makes info itself the result.
        return {"status": "completed", "message": "Complete", "result": meta.get("result", meta)}
    if state == "FAILURE":
        error_msg = str(info) if info else "Unknown error"
        return {"status": "failed", "message": f"Failed: {error_msg}", "error": error_msg}
    if state == "REVOKED":
        return {"status": "cancelled", "message": "Cancelled"}
    return {"status": state.lower(), "message": f"Status: {state}"}


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

        # URL kwargs (e.g. definition_id, run_id) are already on self.kwargs
        # courtesy of Django's View.dispatch(). Subclasses that need them
        # in stream_data can read self.kwargs.get("definition_id"). We do
        # NOT forward **kwargs to stream_data() here because doing so would
        # break the many existing subclasses whose stream_data signature
        # is just (self, request).

        # ALWAYS the async wrapper, even with heartbeats off. Django's ASGI
        # handler CONSUMES A SYNCHRONOUS ITERATOR IN FULL before it sends any of
        # it (`ASGIHandler.send_response`, which warns "StreamingHttpResponse must
        # consume synchronous iterators in order to serve them asynchronously"),
        # so a sync generator here is not a stream at all -- it is a slow way to
        # build one response body. Measured against a local uvicorn on the same
        # generator: sync yielded all six events at 6.0s, async yielded them at
        # 0.0/1.0/2.0/3.0/4.0/5.0s. On labs that silently cost every SSE view its
        # progress -- an 41s cold pipeline read delivered its eleven progress
        # events in one batch at 41s, which is indistinguishable from a page that
        # is simply hanging, and is exactly what the events exist to prevent.
        response = StreamingHttpResponse(
            self._astream(self.stream_data(request)),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # Disable nginx buffering
        return response

    async def _astream(self, generator, interval=None):
        """Serve a sync generator as a real stream, with periodic heartbeats.

        Two jobs, and the first one is why this is `async`. See `get()`: Django
        drains a synchronous iterator completely before sending a byte, so the
        response body has to be an ASYNC iterator for any of it to reach the
        client early. The generator itself stays synchronous -- subclasses write
        plain `yield` code, and it runs on the producer thread below exactly as
        it did before -- so nothing about database access or thread affinity
        changes here.

        The second job is the heartbeat: SSE comment lines (``: heartbeat``)
        every ``interval`` seconds of silence, which keep the connection alive
        across a long blocking step without triggering `EventSource.onmessage`.
        Set ``heartbeat_enabled = False`` on a subclass to stop emitting them;
        the wrapper still applies, because it is what makes streaming work.

        The queue is an `asyncio.Queue` fed with `call_soon_threadsafe`, not a
        `queue.Queue` awaited in a worker: `asyncio.to_thread` would park a pool
        thread per connection for the whole wait, and the default executor is
        `min(32, cpu + 4)` -- five threads on the 1-vCPU web task, so a sixth
        concurrent stream would have blocked on nothing at all.
        """
        if interval is None:
            interval = self.heartbeat_interval

        loop = asyncio.get_running_loop()
        data_queue: asyncio.Queue = asyncio.Queue()
        stop_event = threading.Event()

        def _emit(item):
            # The producer runs on a plain thread; this is the only safe way to
            # hand it to the loop.
            loop.call_soon_threadsafe(data_queue.put_nowait, item)

        def _producer():
            try:
                for item in generator:
                    if stop_event.is_set():
                        break
                    _emit(("data", item))
            except Exception as e:  # noqa: BLE001
                _emit(("error", e))
            finally:
                _emit(("done", None))
                try:
                    generator.close()
                except (GeneratorExit, RuntimeError):
                    pass

        thread = threading.Thread(target=_producer, daemon=True)
        thread.start()

        try:
            while True:
                try:
                    msg_type, value = await asyncio.wait_for(data_queue.get(), timeout=interval)
                except TimeoutError:
                    # Nothing for `interval` seconds. The comment keeps the
                    # connection warm without reaching onmessage.
                    if self.heartbeat_enabled:
                        yield ": heartbeat\n\n"
                    continue
                if msg_type == "data":
                    yield value
                elif msg_type == "done":
                    break
                elif msg_type == "error":
                    raise value
        finally:
            # The producer is a daemon and may be parked in a blocking read, so
            # this asks it to stop and does not wait on it from the event loop.
            stop_event.set()

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
        raise_on_error: bool = False,
    ) -> Generator[str, None, None]:
        """
        Convert AnalysisPipeline stream events to SSE events.

        Processes all pipeline events (STATUS, DOWNLOAD, RESULT, ERROR) and
        yields formatted SSE events. Stores the final result in
        self._pipeline_result and cache status in self._pipeline_from_cache.

        Fetch progress events are yielded once per page from the v2 paginated API
        (up to 1000 rows per page). Each event is immediately converted to an SSE
        event for real-time UI updates.

        Args:
            pipeline_stream: Generator from pipeline.stream_analysis()
            send_sse_func: SSE formatting function (defaults to send_sse_event)
            raise_on_error: If True, an EVENT_ERROR from the pipeline (its own
                internal try/except caught something and gave up) re-raises the
                original exception here instead of being silently dropped.
                Defaults to False to preserve the historical behavior for
                existing callers (labs/admin, configurable_ui,
                mbw_monitoring, custom_analysis/rutf, custom_analysis/kmc) that
                have never handled a raised exception from this call — before
                this flag existed, EVENT_ERROR matched none of the branches
                below and was silently discarded, leaving
                self._pipeline_result as None with no indication anything
                failed (self._pipeline_from_cache could even end up True, as a
                side effect of an unrelated earlier "checking ... cache..."
                status message containing the word "cache"). Callers that DO
                already wrap this call in a try/except (e.g. workflow/views.py
                per-opp pipeline execution) should pass True so a real failure
                surfaces as a real error instead of a fake empty success.

        Yields:
            Formatted SSE event strings

        Side Effects:
            Sets self._pipeline_result and self._pipeline_from_cache

        Raises:
            The original pipeline exception, if raise_on_error=True and the
            pipeline yielded an EVENT_ERROR.
        """
        from connect_labs.labs.analysis.pipeline import EVENT_DOWNLOAD, EVENT_ERROR, EVENT_RESULT, EVENT_STATUS

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
                # Each page from the paginated API triggers one event (page size set in ExportAPIClient).
                rows_so_far = event_data.get("rows", 0)
                # `total` is None for sources that don't pre-announce a row count
                # (cchq_forms streams page-by-page with no upfront tally). Treat
                # None and missing identically — show progress without a denominator.
                expected_count = event_data.get("total") or 0
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

            elif event_type == EVENT_ERROR:
                message = event_data.get("message", "Pipeline error")
                logger.warning(f"[SSE Mixin] Error event: {message}")
                if raise_on_error:
                    exc = event_data.get("exception")
                    raise exc if exc is not None else RuntimeError(message)


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
        # Delegates to the module-level canonical translation so the shape stays
        # identical across every SSE and poll consumer.
        return build_task_progress(state, info)

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
