"""FastMCP bridge for progress notifications (connect-labs#1220).

The transport-agnostic half (``NULL_PROGRESS``, ``safe_call``) lives in
``connect_labs.labs.progress`` so domain code does not depend on the MCP layer.
This module holds the part that needs FastMCP: turning the protocol's
``notifications/progress`` — which is what resets a client's idle-output timer —
into something the legacy registry's *synchronous* handlers can call from the
worker thread they run in.
"""

from __future__ import annotations

import asyncio
import logging

from connect_labs.labs.progress import NULL_PROGRESS, ProgressCallback, safe_call

__all__ = ["NULL_PROGRESS", "ProgressCallback", "safe_call", "make_thread_safe_reporter"]

logger = logging.getLogger(__name__)


def make_thread_safe_reporter(ctx, loop: asyncio.AbstractEventLoop) -> ProgressCallback:
    """Bridge FastMCP's async ``ctx.report_progress`` to a sync worker thread.

    The registry's handlers are synchronous and run under ``sync_to_async`` in a
    worker thread, so they cannot await. ``run_coroutine_threadsafe`` schedules
    the notification back onto ``loop`` — the loop that owns the MCP session —
    and we deliberately do NOT wait on the returned future: blocking the worker
    on delivery would make progress reporting a latency cost on the very calls
    it exists to keep alive.

    ``ctx.report_progress`` is itself a no-op when the client sent no
    ``progressToken``, so this is safe to install unconditionally.
    """

    def _report(progress: float, total: float | None = None, message: str | None = None) -> None:
        coro = ctx.report_progress(progress, total, message)
        try:
            asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception:  # noqa: BLE001 — telemetry must never fail a tool call
            # Close the coroutine we will now never await, so a dead session does
            # not also leave "coroutine was never awaited" warnings behind it.
            coro.close()
            logger.debug("progress notification could not be scheduled", exc_info=True)

    return _report
