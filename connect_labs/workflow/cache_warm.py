"""Filling a cold cache off the request: the one protocol every reader shares.

A cold cache on a big opportunity takes minutes to fill and pins a CPU while it does.
Run inside a web request, that work outlives the load balancer's patience, keeps going
after the viewer has left, and competes with every other request on a 1-vCPU task. On
2026-11-03 two viewers of one report held a web task at 100% for most of 17 minutes
(#2810). So no request fills a cache. The protocol:

    the endpoint  claims the slot and queues a Celery task, then answers 202
                  `{status: "warming", message, elapsed_seconds, retry_after_ms}`
    the task      fills the cache, says what it is doing, leaves an outcome
    the client    asks again until the answer stops being 202
                  (components/workflow/warming.ts, which the sandbox host applies to
                  every read a render makes, so no render has to know)

A slot is one fill. Concurrent readers share it: the first claims it, the rest watch.

**A dead worker cannot strand a reader.** A running task's claim is short and kept alive
by a heartbeat, so a task killed by a deploy loses its claim within minutes and the next
poll starts a new one. A claim that has not started yet lasts longer, because it may be
waiting its turn on a busy worker.

**An outcome breaks the loop.** A fill can finish and leave the reader's data still
missing: one export failed, or an opportunity really has no visits. An endpoint that
simply warmed again would poll forever, so a finished slot keeps its outcome for a while
and the endpoint answers from it. Reading a failure with `take_outcome` spends it, so
that the reader's Retry really does try again.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from django.core.cache import cache
from django.http import JsonResponse

logger = logging.getLogger(__name__)

# How long a claim survives before a worker picks the task up.
QUEUED_SECONDS = 10 * 60
# How long a started task's claim survives without a heartbeat...
RUNNING_SECONDS = 3 * 60
# ...and how often the heartbeat renews it.
HEARTBEAT_SECONDS = 30
OUTCOME_SECONDS = 10 * 60
RETRY_AFTER_MS = 5000


class WarmSlot:
    """One background fill of one cache, keyed by what it fills."""

    def __init__(self, key: str):
        self.key = key
        self.outcome_key = key + ":outcome"

    def claim(self) -> bool:
        """Take the slot. False means another reader's fill is already under way."""
        return bool(
            cache.add(self.key, {"message": None, "started_at": time.time(), "running": False}, QUEUED_SECONDS)
        )

    def state(self) -> dict[str, Any] | None:
        """What the fill under way is doing, or None when there is none."""
        return cache.get(self.key)

    def report(self, message: str) -> None:
        """Say what the task is doing now. A reader sees it on its next poll."""
        self._write({"message": str(message)[:200]})

    def _write(self, changes: dict[str, Any]) -> None:
        current = cache.get(self.key) or {"started_at": time.time()}
        cache.set(self.key, {**current, "running": True, **changes}, RUNNING_SECONDS)

    @contextmanager
    def running(self) -> Iterator[dict[str, Any]]:
        """Hold the claim while the body runs, then release it and leave the outcome.

        Yields the outcome: set its `error` to report a failure. An exception out of the
        body is reported too, and still raised.

        The heartbeat is a thread because the slow part is one long blocking download
        that has nothing to tick from. If the process dies, so does the thread, and the
        claim lapses by itself: that is the point.
        """
        self._write({})
        stop = threading.Event()

        def beat():
            while not stop.wait(HEARTBEAT_SECONDS):
                try:
                    self._write({})
                except Exception:  # noqa: BLE001 -- a missed beat must never fail the work
                    logger.debug("warm heartbeat failed for %s", self.key, exc_info=True)

        thread = threading.Thread(target=beat, name="cache-warm-heartbeat", daemon=True)
        thread.start()
        outcome: dict[str, Any] = {"error": None}
        try:
            yield outcome
        except Exception as exc:
            outcome["error"] = str(exc) or type(exc).__name__
            raise
        finally:
            # Stopped first: a beat after the release would take the claim back.
            stop.set()
            thread.join(timeout=5)
            self.finish(outcome["error"])

    def finish(self, error: str | None = None) -> None:
        """Release the claim and leave what happened for the next reader."""
        cache.set(self.outcome_key, {"error": error[:500] if error else None}, OUTCOME_SECONDS)
        cache.delete(self.key)

    def outcome(self) -> dict[str, Any] | None:
        """How the last fill ended, if it ended recently. Leaves it in place."""
        return cache.get(self.outcome_key)

    def take_outcome(self) -> dict[str, Any] | None:
        """How the last fill ended, spent: the next reader to find it cold fills again."""
        found = cache.get(self.outcome_key)
        if found is not None:
            cache.delete(self.outcome_key)
        return found


def warming_response(request, state: dict[str, Any] | None, message: str) -> JsonResponse:
    """The 202 a reader polls on. `message` is the wording until the task has its own.

    A reader outside the sandbox host does not poll. That happens only with
    LABS_WORKFLOW_SANDBOX=off, the emergency switch, and its render reads `error`, so
    the emergency path says something true instead of "no figures in the answer".
    """
    state = state or {}
    started = state.get("started_at")
    wording = state.get("message") or message
    body: dict[str, Any] = {
        "status": "warming",
        "message": wording,
        "elapsed_seconds": max(0, int(time.time() - started)) if started else 0,
        "retry_after_ms": RETRY_AFTER_MS,
    }
    if not request.headers.get("x-labs-sandbox-for"):
        body["error"] = f"{wording} Reload this page in a few minutes."
    return JsonResponse(body, status=202)
