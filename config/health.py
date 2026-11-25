"""``/health/``, answered in the ASGI layer so a busy Django cannot starve it.

The ALB checks the labs target group every 5s with a 4s timeout and evicts a
task after 3 failures (``labs-jj-target-group``: about 15s). Answered by the
Django view (``config/views.py::health_check``), a check queued behind the
middleware stack and every slow request on the worker like any page, so a
burst of slow work could make a working task look dead and ECS would replace
it, turning slowness into 502s. Health checks are also most of labs' requests,
so taking them off Django's path is cheap capacity too.

``HealthMiddleware`` answers GET/HEAD ``/health/`` on the event loop, before
routing, with no database and no session. It still fails when the process is
genuinely stuck, on two signals:

* the event loop is blocked: nothing answers at all and the ALB times out;
* the thread pool makes no progress: ``heartbeat()`` runs a no-op through
  ``sync_to_async(thread_sensitive=False)`` (the loop's default executor) every
  ``BEAT_S``; when the last one finished more than ``STALL_S`` ago the check
  answers 503.

The heartbeat deliberately does NOT use ``thread_sensitive=True``. In labs that
is the single thread every MCP tool call and metadata endpoint funnels through,
and one long tool call (a pipeline run) legitimately holds it for minutes; a
check on it would evict healthy tasks.

Before the heartbeat has started (no lifespan: tests, ``manage.py``) the check
answers 200. The body is the Django view's: the deploy workflow and
``tools/ddd_demo.py`` read ``git_sha`` from it. Modelled on canopy-web's
``apps/common/health.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

PATHS = ("/health/",)
BEAT_S = 5.0
#: Seconds without a finished heartbeat before the check fails.
STALL_S = float(os.environ.get("LABS_HEALTH_STALL_S", "60"))

#: monotonic time the last heartbeat finished; None until the heartbeat starts.
_last_beat: float | None = None


def _noop() -> None:
    return None


def stalled_for(now: float | None = None) -> float:
    """Seconds since the last heartbeat finished (0 before it has started)."""
    if _last_beat is None:
        return 0.0
    return max(0.0, (time.monotonic() if now is None else now) - _last_beat)


def body() -> tuple[int, dict]:
    stalled = stalled_for()
    if stalled > STALL_S:
        return 503, {"status": "stalled", "stalled_s": int(stalled), "git_sha": os.environ.get("GIT_SHA", "unknown")}
    return 200, {"status": "OK", "git_sha": os.environ.get("GIT_SHA", "unknown")}


async def _beat_forever() -> None:
    global _last_beat
    while True:
        await asyncio.sleep(BEAT_S)
        try:
            await sync_to_async(_noop, thread_sensitive=False)()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a heartbeat never stops beating
            logger.exception("health heartbeat failed")
            continue
        _last_beat = time.monotonic()


@contextlib.asynccontextmanager
async def heartbeat():
    """Run the heartbeat for as long as the context is open (the ASGI lifespan)."""
    global _last_beat
    _last_beat = time.monotonic()
    task = asyncio.create_task(_beat_forever(), name="health-heartbeat")
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        _last_beat = None


class HealthMiddleware:
    """Answer ``/health/`` before anything else in the ASGI app sees the request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") not in PATHS or scope.get("method") not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return
        status, payload = body()
        content = json.dumps(payload).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(content)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else content})
