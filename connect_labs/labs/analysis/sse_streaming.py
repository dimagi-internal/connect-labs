"""
Base classes and utilities for Server-Sent Events (SSE) streaming views.

Provides reusable infrastructure for streaming analysis progress to the frontend.
Includes support for both AnalysisPipeline streaming and Celery task progress streaming.
"""

import asyncio
import json
import logging
import queue
import threading
import time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connections
from django.http import JsonResponse, StreamingHttpResponse
from django.views import View

logger = logging.getLogger(__name__)


def _drain(iterator, size):
    """Pull up to `size` items. Returns `(items, done, error)`, never raising.

    The error is RETURNED rather than raised so the items produced before it can
    still be yielded -- a subclass that fails on row 900 of 1000 has already
    written 899 useful rows, and the SSE error event it yields is the 900th.
    """
    items = []
    for _ in range(size):
        try:
            items.append(next(iterator))
        except StopIteration:
            return items, True, None
        except Exception as exc:  # noqa: BLE001 -- carried to the consumer intact
            return items, True, exc
    return items, False, None


def _close_generator(generator):
    """Close the generator and release this thread's DB connections."""
    try:
        generator.close()
    except (GeneratorExit, RuntimeError):
        pass
    finally:
        connections.close_all()


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
    # Rows handed to the event loop per callback. Raising it costs latency (a
    # chunk is yielded together); lowering it costs loop traffic. A view that
    # emits a handful of large progress events should set 1.
    chunk_size = 100

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
        """Wrap a generator so the response streams, with periodic heartbeats.

        Returns an ASYNC generator, which is the whole point: Django's ASGI
        handler drains a *sync* iterator in full before sending any of it --
        `StreamingHttpResponse.__aiter__` falls back to
        `await sync_to_async(list)(self.streaming_content)` and warns that it is
        doing so -- so a sync body arrives as one batch at the end however
        carefully it was yielded. Six events half a second apart arrived
        together at 3.04s; through here they arrive at 0.51 .. 3.03.

        HOW, AND WHY NOT THE OBVIOUS WAY. #1859 made this async by PUSHING each
        row at the event loop from a producer thread (`call_soon_threadsafe` per
        row, unbounded `asyncio.Queue`). It streamed, and it took every
        pipeline-backed dashboard down for fifteen hours (reverted in #1888) for
        two reasons, neither of them memory:

          * the producer no longer waited for the consumer -- 20,000 rows ahead
            of a client that had read one;
          * one event-loop callback per row, and every other request on the
            worker shares that loop.

        So this PULLS instead. The generator only advances when the consumer
        asks for more, which makes backpressure structural rather than a queue
        policy -- at most one chunk exists ahead of the reader, and there is no
        queue at all -- and rows are handed over `chunk_size` at a time, so the
        loop sees one callback per chunk instead of one per row (0.010 per row
        at the default, against 1.000).

        `connect_labs/labs/analysis/tests/test_sse_backpressure.py` fails any
        future version that loses either property.

        THE THREAD MODEL IS UNCHANGED from the sync wrapper this replaces: one
        worker thread per stream, which is why the executor is per-request and
        single-worker rather than the loop's shared default pool. That also
        keeps the generator on ONE thread, so thread-locals inside it (Django's
        DB connections, most of all) behave exactly as they did -- and it gives
        one place to close them, which a shared pool would not.

        Set `heartbeat_enabled = False` on a subclass to disable the heartbeat;
        set `LABS_SSE_ASYNC_STREAMING=False` to fall back to the pre-#1859 sync
        wrapper without a revert.
        """
        if interval is None:
            interval = self.heartbeat_interval
        if not getattr(settings, "LABS_SSE_ASYNC_STREAMING", True):
            return self._with_heartbeat_sync(generator, interval)
        return self._astream(generator, interval)

    async def _astream(self, generator, interval):
        """Pull chunks from `generator` in a worker thread; yield them as SSE."""
        loop = asyncio.get_running_loop()
        # Single worker: the generator is resumed on one thread for its whole
        # life, as it was under the sync wrapper's dedicated producer thread.
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sse-stream")
        iterator = iter(generator)
        try:
            while True:
                chunk = loop.run_in_executor(executor, _drain, iterator, self.chunk_size)
                while True:
                    try:
                        # shield: a heartbeat timeout must not cancel the work
                        # already in flight, only interrupt our wait for it.
                        items, done, error = await asyncio.wait_for(asyncio.shield(chunk), interval)
                        break
                    except TimeoutError:
                        yield ": heartbeat\n\n"
                for item in items:
                    yield item
                if error is not None:
                    # Raised in the CONSUMER, as the sync wrapper did. Whatever
                    # the generator produced before failing was yielded above.
                    raise error
                if done:
                    return
        finally:
            # Close the generator on the thread that ran it, and release that
            # thread's DB connections: this is not a request thread, so nothing
            # else will (#667/#669 -- the leak that exhausted RDS's slots).
            try:
                await loop.run_in_executor(executor, _close_generator, generator)
            except RuntimeError:  # loop already closing -- the client went away
                _close_generator(generator)
            executor.shutdown(wait=False)

    def _with_heartbeat_sync(self, generator, interval):
        """The pre-#1859 wrapper, kept as the `LABS_SSE_ASYNC_STREAMING=False` path.

        It does not stream -- see `_with_heartbeat` -- so it is a way to take the
        async path out of service without a revert and a deploy, not a supported
        mode.
        """
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
