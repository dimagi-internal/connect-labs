"""Gunicorn worker class for the labs web tier: uvicorn, with an optional concurrency valve.

``docker/start`` serves ``config.asgi:application`` under gunicorn's
``uvicorn.workers.UvicornWorker``. That worker leaves uvicorn's
``limit_concurrency`` at its default of ``None``, so each worker process accepts
an unbounded number of in-flight requests. Combined with the rest of the stack
that is how overload becomes a 500 rather than a 503:

* Django's ASGI handler runs each request inside its own
  ``ThreadSensitiveContext`` (``django/core/handlers/asgi.py``), so every
  concurrent request gets its own thread and therefore its own thread-local DB
  connection.
* ``ATOMIC_REQUESTS = True`` (``config/settings/base.py``) pins that connection
  in an open transaction for the whole request.

So peak RDS connections from the web tier tracks peak concurrent requests, and
nothing caps the latter. When the instance runs out of slots the failure lands in
``csrf.py`` reading the session — *before* any view is entered, which is why no
view-level ``except`` can catch it (#1060: 39 tracebacks, all
``FATAL: remaining connection slots are reserved for roles with the SUPERUSER
attribute``). Celery shares the same instance, so a web-tier flood degrades
background jobs too.

**What this class does and does not buy you.** Read uvicorn's trip condition
before choosing a value (``uvicorn/protocols/http/httptools_impl.py``, same in
``h11_impl.py``)::

    if self.limit_concurrency is not None and (
        len(self.connections) >= self.limit_concurrency or len(self.tasks) >= self.limit_concurrency
    ):
        app = service_unavailable

``self.connections`` is the set of **open TCP sockets** on the worker, which
under HTTP keep-alive includes sockets sitting idle between requests (browsers
hold several per host; the ALB pools its own). So the limit bounds
``max(open sockets, running tasks)`` per worker — a blunt overload valve, **not**
a DB-connection governor. A value tight enough to bound DB connections would
503 legitimate traffic from a few idle tabs; a value safe for keep-alive traffic
is too loose to bound connections precisely. It cannot be both, which is why the
value is a deployment decision and why this ships **defaulted off** (see #1152).

Off by default means importing this module changes nothing: with
``WEB_LIMIT_CONCURRENCY`` unset the config handed to uvicorn is byte-for-byte
what ``UvicornWorker`` already used. Turning the valve on — or back off — is an
env-var edit in ``deploy/task-definitions/web.json``, reviewable in the diff, no
code change and no image rebuild on either path.
"""

from __future__ import annotations

import fcntl
import logging
import os
import sys

from gunicorn.arbiter import Arbiter
from uvicorn.server import Server
from uvicorn.workers import UvicornWorker

logger = logging.getLogger("uvicorn.error")

#: Max ``max(open sockets, in-flight requests)`` per worker before uvicorn answers
#: 503. Unset or ``0`` disables the valve (the default, and the pre-existing
#: behaviour). Remember there are ``WEB_CONCURRENCY`` worker processes, so the
#: tier-wide ceiling is this value times that one.
LIMIT_CONCURRENCY_ENV = "WEB_LIMIT_CONCURRENCY"


def limit_concurrency_from_env(environ: dict[str, str] | None = None) -> int | None:
    """Parse ``WEB_LIMIT_CONCURRENCY`` into uvicorn's ``limit_concurrency``.

    Unset, empty, or ``0`` -> ``None`` (valve off). ``0`` is spelled out as a
    legitimate "off" so the var can stay in the task definition as documentation
    of the knob rather than vanishing when it is disabled.

    A malformed or negative value **raises**. It would be friendlier to warn and
    fall back to off, but this valve's whole job is to be load-bearing during an
    incident, and a typo that silently leaves it disabled tells the operator they
    are protected when they are not. Failing here surfaces as a failed ECS
    deployment that rolls back on health checks — a loud, contained failure —
    whereas the silent fallback is only discovered by the next outage.
    """
    raw = (os.environ if environ is None else environ).get(LIMIT_CONCURRENCY_ENV, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{LIMIT_CONCURRENCY_ENV} must be an integer, got {raw!r}") from None
    if value < 0:
        raise ValueError(f"{LIMIT_CONCURRENCY_ENV} must be >= 0 (0 disables the limit), got {value}")
    return value or None


# --------------------------------------------------------------------------------
# Memory recycling: retire a worker whose resident set has ratcheted up, AFTER its
# in-flight requests (open streams included) have finished.
#
# Why. A worker's RSS climbs with each heavy request and Python almost never hands
# it back. Measured from the request telemetry's ``rss_kb`` over the 3 days to
# 2026-11-24: workers start at ~440 MB, and after a heavy request (snapshot streams,
# exports; single requests peak at 2 GB) the MINIMUM across a task's workers
# settles at 700-1,000+ MB and stays there until the task is replaced (one task:
# 440 -> min 1,041 MB after a 1.97 GB peak). Three such workers fill the 4 GB task,
# which is what drove ~90% of the tier's autoscaling (labs-jj-web-mem70) — a
# scale-out that cannot help, because the new task does not free the old one's
# memory. See infra/README.md § labs-web-autoscaling.
#
# Why not gunicorn's ``--max-requests``: it recycles on a request COUNT, which says
# nothing about memory (most requests are tiny), so it either never fires or churns
# workers that are fine. This checks the thing itself.
#
# How it drains. Setting ``should_exit`` runs uvicorn's normal graceful shutdown:
# the worker stops accepting (its copy of the listening socket closes; siblings
# keep serving), idle keep-alive connections close, and a connection with a
# response still in progress — an SSE stream — is left to finish. Heartbeats to
# the gunicorn arbiter stop during the drain, so a stream that outlives the
# arbiter's ``--timeout`` (600s in docker/start) is killed then: that is the
# ceiling. The arbiter forks a fresh worker when this one exits.
#
# One at a time per task. If all three workers drained together the task would
# accept nothing (and fail its health check) until the slowest stream ended. A
# worker must hold an exclusive ``flock`` on a file in the container's /tmp to
# start draining; it keeps the lock until the process exits (the kernel releases
# it), so a sibling over the limit waits for the next check.

#: Resident-set ceiling per worker, in MB. Unset or ``0`` disables recycling.
RECYCLE_RSS_ENV = "WEB_RECYCLE_RSS_MB"
#: uvicorn ticks every 0.1s; check RSS every 10s. Reading /proc/self/statm is cheap.
RSS_CHECK_TICKS = 100
RECYCLE_LOCK_PATH = "/tmp/labs-web-recycle.lock"


def recycle_rss_kb_from_env(environ: dict[str, str] | None = None) -> int | None:
    """Parse ``WEB_RECYCLE_RSS_MB`` into a KB ceiling. Same rules as the valve above:
    blank or ``0`` is off, and a malformed or negative value raises at startup."""
    raw = (os.environ if environ is None else environ).get(RECYCLE_RSS_ENV, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{RECYCLE_RSS_ENV} must be an integer (MB), got {raw!r}") from None
    if value < 0:
        raise ValueError(f"{RECYCLE_RSS_ENV} must be >= 0 (0 disables recycling), got {value}")
    return value * 1024 or None


def current_rss_kb() -> int | None:
    """This process's resident set in KB from /proc/self/statm; None where unavailable."""
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return pages * os.sysconf("SC_PAGE_SIZE") // 1024


def claim_recycle_slot(path: str = RECYCLE_LOCK_PATH):
    """Take the task's single drain slot, or return None if a sibling holds it.

    The returned file object must stay referenced for the life of the process: the
    lock is released when it is closed, which the kernel does at exit."""
    f = open(path, "a")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    return f


class RecyclingServer(Server):
    """uvicorn ``Server`` that starts a graceful exit once its RSS passes a ceiling."""

    def __init__(self, config, rss_limit_kb: int, lock_path: str = RECYCLE_LOCK_PATH) -> None:
        super().__init__(config=config)
        self.rss_limit_kb = rss_limit_kb
        self.lock_path = lock_path
        self._recycle_lock = None

    async def on_tick(self, counter: int) -> bool:
        if counter % RSS_CHECK_TICKS == 0 and not self.should_exit:
            self.maybe_recycle()
        return await super().on_tick(counter)

    def maybe_recycle(self) -> bool:
        rss = current_rss_kb()
        if rss is None or rss < self.rss_limit_kb:
            return False
        lock = claim_recycle_slot(self.lock_path)
        if lock is None:
            return False
        self._recycle_lock = lock
        logger.warning(
            "web worker recycle: pid=%s rss_mb=%d over %s=%d; draining (in-flight requests finish first)",
            os.getpid(),
            rss // 1024,
            RECYCLE_RSS_ENV,
            self.rss_limit_kb // 1024,
        )
        self.should_exit = True
        return True


class LabsUvicornWorker(UvicornWorker):
    """``UvicornWorker`` with ``limit_concurrency`` and memory recycling wired to
    the environment.

    ``CONFIG_KWARGS`` is read by gunicorn when it builds each worker's uvicorn
    ``Config``, so the env is resolved once at import in the arbiter — which is
    correct here: ECS sets the variable before the process starts and it cannot
    change without a new task definition and therefore a new process. The same
    holds for ``RECYCLE_RSS_KB``.

    With ``WEB_RECYCLE_RSS_MB`` unset, ``_serve`` is exactly the parent's. When set,
    it mirrors ``UvicornWorker._serve`` with only the server class swapped
    (``test_uvicorn_worker.py`` pins the parent's body, so a uvicorn upgrade that
    changes it fails CI instead of silently diverging).
    """

    CONFIG_KWARGS = {
        **UvicornWorker.CONFIG_KWARGS,
        "limit_concurrency": limit_concurrency_from_env(),
    }
    RECYCLE_RSS_KB = recycle_rss_kb_from_env()

    async def _serve(self) -> None:
        if not self.RECYCLE_RSS_KB:
            return await super()._serve()
        self.config.app = self.wsgi
        server = RecyclingServer(config=self.config, rss_limit_kb=self.RECYCLE_RSS_KB)
        self._install_sigquit_handler()
        await server.serve(sockets=self.sockets)
        if not server.started:
            sys.exit(Arbiter.WORKER_BOOT_ERROR)
