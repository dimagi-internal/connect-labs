"""Filling a cold cache off the request: the one protocol every reader shares.

**A read answers from the cache, or it says a fill is under way. It never fills.** A cold
cache on a big opportunity takes minutes to fill and pins a CPU while it does. Run inside
a web request, that work outlives the viewer and competes with every other request on a
1-vCPU task: on 2026-11-03 two viewers of one report held a web task at 100% for most of
17 minutes (#2810). The protocol:

    the endpoint  claims the slot and queues a Celery task, then answers 202
                  `{status: "warming", message, progress, elapsed_seconds, retry_after_ms}`
    the task      fills the cache, says what it is doing, leaves an outcome
    the client    asks again until the answer stops being 202
                  (components/workflow/warming.ts, used by the runner, and by the sandbox
                  host for every read a render makes, so no render has to know)

A slot is one fill. Concurrent readers share it: the first claims it, the rest watch.

`progress` is `TaskProgress` (static/js/task-progress.ts), from the same
`build_task_progress` that translates a Celery job's state, so a fill and a job say the
same thing in the same fields. It lives here rather than in Celery's result backend
because a reader joins a fill by what it fills, and may arrive before the task has started.

**A dead worker cannot strand a reader.** A running task's claim is short and kept alive
by a heartbeat, so a task killed by a deploy loses its claim within minutes and the next
poll starts a new one. A claim that has not started yet lasts longer, because it may be
waiting its turn on a busy worker.

**An outcome breaks the loop.** A fill can finish and leave the reader's data still
missing: one export failed, or an opportunity really has no visits. An endpoint that
simply warmed again would poll forever, so a finished slot keeps its outcome for a while
and the endpoint answers from it. Reading a failure with `take_outcome` spends it, so
that the reader's Retry really does try again.

Design: docs/superpowers/specs/2026-11-03-one-loading-path-design.md
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from django.core.cache import cache
from django.http import JsonResponse

from connect_labs.labs.analysis.sse_streaming import build_task_progress

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

    def __init__(self, key: str, token: str | None = None):
        """`token` is the claim a task was queued with (`queue_once`). Without one, the
        slot acts for whichever fill holds it."""
        self.key = key
        self.outcome_key = key + ":outcome"
        self.token = token

    def claim(self) -> str | None:
        """Take the slot, and return its token. None means a fill is already under way."""
        token = uuid.uuid4().hex
        taken = cache.add(
            self.key,
            {"token": token, "message": None, "started_at": time.time(), "running": False},
            QUEUED_SECONDS,
        )
        if not taken:
            return None
        self.token = token
        return token

    def queue_once(self, enqueue: Callable[[str], Any]) -> None:
        """Have `enqueue(token)` queue the fill, if no fill is under way yet.

        If it cannot be queued, no task is coming, so no claim may stand: the next read
        hears why instead of polling for ten minutes on nothing.
        """
        token = self.claim()
        if token is None:
            return
        try:
            enqueue(token)
        except Exception as exc:
            self.finish(f"the background fill could not be queued: {exc}")
            raise

    def state(self) -> dict[str, Any] | None:
        """What the fill under way is doing, or None when there is none."""
        found = cache.get(self.key)
        if found is None or isinstance(found, dict):
            return found
        # A claim written before this format, still live across a deploy.
        return {"message": None, "started_at": None}

    def superseded(self) -> bool:
        """This task's claim lapsed while it queued and a newer fill holds the slot.
        That fill will do the work; this one must neither repeat it nor release its claim."""
        current = self.state()
        return bool(current and self.token and current.get("token") != self.token)

    def report(self, message: str, **progress: Any) -> None:
        """Say what the task is doing now. A reader sees it on its next poll.

        `progress` takes what `set_task_progress` takes (`stage_name`, `current_stage`,
        `total_stages`, `processed`, `total`), and what it leaves out reads as unknown.
        """
        self._write({"message": str(message)[:200], "progress": progress})

    def _write(self, changes: dict[str, Any]) -> None:
        if self.superseded():
            return
        current = self.state() or {"started_at": time.time()}
        cache.set(
            self.key,
            {**current, "token": self.token or current.get("token"), "running": True, **changes},
            RUNNING_SECONDS,
        )

    @contextmanager
    def running(self) -> Iterator[dict[str, Any]]:
        """Hold the claim while the body runs, then release it and leave the outcome.

        Yields the outcome: set its `error` to report a failure, and any other key to
        leave it for the reader. An exception out of the body is reported too, and still
        raised.

        The heartbeat is a thread because the slow part is one long blocking download
        that has nothing to tick from. If the process dies, so does the thread, and the
        claim lapses by itself: that is the point.
        """
        self._write({})
        stop = threading.Event()

        def beat():
            while not stop.wait(HEARTBEAT_SECONDS):
                try:
                    # Only the expiry: rewriting the value could undo a stage just
                    # reported, and a lapsed key must stay lapsed.
                    cache.touch(self.key, RUNNING_SECONDS)
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
            stop.set()
            thread.join(timeout=5)
            self.finish(outcome["error"], **{k: v for k, v in outcome.items() if k != "error"})

    def finish(self, error: str | None = None, **details: Any) -> None:
        """Release the claim and leave what happened for the next reader."""
        if self.superseded():
            return
        cache.set(self.outcome_key, {"error": error[:500] if error else None, **details}, OUTCOME_SECONDS)
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


def warming_progress(state: dict[str, Any] | None, message: str) -> dict[str, Any]:
    """What a fill is doing, in the shape every kind of progress takes. `message` is the
    wording until the task has its own."""
    state = state or {}
    wording = state.get("message") or message
    started = state.get("started_at")
    return {
        "message": wording,
        "progress": build_task_progress("PROGRESS", {**(state.get("progress") or {}), "message": wording}),
        "elapsed_seconds": max(0, int(time.time() - started)) if started else 0,
        "retry_after_ms": RETRY_AFTER_MS,
    }


def warming_response(request, state: dict[str, Any] | None, message: str) -> JsonResponse:
    """The 202 a reader polls on.

    A reader that neither polls nor is the runner reads `error`. That happens only with
    LABS_WORKFLOW_SANDBOX=off, the emergency switch, so the emergency path says something
    true instead of "no figures in the answer". `error` beside a 202 means nothing to a
    reader that does poll.
    """
    body: dict[str, Any] = {"status": "warming", **warming_progress(state, message)}
    if not request.headers.get("x-labs-sandbox-for"):
        body["error"] = f"{body['message']} Reload this page in a few minutes."
    return JsonResponse(body, status=202)
