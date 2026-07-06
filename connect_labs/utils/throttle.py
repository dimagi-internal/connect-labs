"""Tiny rate-limiter for side effects that fire far more often than they need to.

The canonical use is persisting long-running-job progress: a handler may tick
per-item (hundreds of times), but the UI only needs a fresh snapshot every
second or so, and each persist is a real (rate-limited, network) write to the
LabsRecord API. ``throttled`` wraps the persist so it runs at most once per
``interval`` seconds, with a ``force=True`` escape hatch for the terminal write.
"""

import time
from collections.abc import Callable


def throttled(fn: Callable, interval: float = 1.5) -> Callable:
    """Wrap ``fn`` so it runs at most once per ``interval`` seconds.

    The returned callable forwards ``*args``/``**kwargs`` to ``fn`` and returns
    ``fn``'s result on a real call, else ``None``. The first call always fires.
    Pass ``force=True`` to bypass the throttle (use it for the final tick so the
    last state is never dropped).

    >>> writes = []
    >>> persist = throttled(lambda v: writes.append(v), interval=1.5)
    >>> persist("a")            # fires (first call)
    >>> persist("b")            # throttled — dropped
    >>> persist("z", force=True)  # forced — fires
    >>> writes
    ['a', 'z']

    Not thread-safe by design — intended for a single job's sequential tick loop,
    where progress is best-effort and a lost tick is cosmetic.
    """
    # Prime the clock in the past so the first call always fires.
    last = [time.monotonic() - interval]

    def call(*args, force: bool = False, **kwargs):
        now = time.monotonic()
        if force or (now - last[0]) >= interval:
            last[0] = now
            return fn(*args, **kwargs)
        return None

    return call
