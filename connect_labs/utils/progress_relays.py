"""Generic in-process registry of progress-relay callbacks keyed by a run id.

Purpose
-------
When one Celery task runs a *child* task **eagerly in the same process**
(``child.apply(...)`` rather than ``child.delay(...)``), the parent wants the
child's fine-grained progress (per-item ticks) forwarded up so a parent-owned UI
row can render a gliding bar. The obvious way — pass a ``progress_callback``
closure into ``child.apply(kwargs=...)`` — **does not work**: Celery's eager path
serializes task kwargs and chokes on a function, so the whole call throws.

This registry is the serialization-free channel. The parent registers a relay
callback keyed by a stable ``run_id`` *before* invoking the child, and the child
looks it up by the same ``run_id`` and forwards ticks to it. No closure ever
crosses the Celery boundary.

Correctness contract
--------------------
- Register/pop must be paired (use ``try/finally``) so a failed child never
  leaks a relay.
- This only works when parent and child share a process (the eager ``.apply()``
  case). Across a real worker boundary the child is in a different process and
  ``get_relay`` returns ``None`` — which is the safe no-op, not a crash.

This started life as ``AUDIT_PROGRESS_RELAYS`` in ``connect_labs.audit.tasks``
(audit-only). It is domain-neutral here so any in-process fan-out can reuse it.
"""

from collections.abc import Callable

# run_id -> relay callback. Deliberately a plain module-level dict: the whole
# point is a single in-process channel, and progress is best-effort — losing a
# tick on a race is cosmetic, never a correctness issue.
_RELAYS: dict = {}


def register_relay(run_id, callback: Callable) -> None:
    """Register ``callback`` as the progress relay for ``run_id``.

    Call this immediately before invoking the eager child, and pair it with
    :func:`pop_relay` in a ``finally`` block.
    """
    if run_id is not None:
        _RELAYS[run_id] = callback


def get_relay(run_id) -> Callable | None:
    """Return the relay registered for ``run_id``, or ``None`` if there is none
    (including when ``run_id`` is ``None`` or the child runs out-of-process)."""
    if run_id is None:
        return None
    return _RELAYS.get(run_id)


def pop_relay(run_id) -> None:
    """Remove the relay for ``run_id`` (idempotent). Always call in a ``finally``."""
    _RELAYS.pop(run_id, None)
