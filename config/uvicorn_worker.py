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

import os

from uvicorn.workers import UvicornWorker

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


class LabsUvicornWorker(UvicornWorker):
    """``UvicornWorker`` with ``limit_concurrency`` wired to the environment.

    ``CONFIG_KWARGS`` is read by gunicorn when it builds each worker's uvicorn
    ``Config``, so the env is resolved once at import in the arbiter — which is
    correct here: ECS sets the variable before the process starts and it cannot
    change without a new task definition and therefore a new process.
    """

    CONFIG_KWARGS = {
        **UvicornWorker.CONFIG_KWARGS,
        "limit_concurrency": limit_concurrency_from_env(),
    }
