"""Run analysis-cache work against a customer's database instead of labs'.

The pipeline layer stages a source's rows into ``labs_raw_visit_cache`` and keeps what it computes
in three more cache tables, all through the DEFAULT database connection (the ORM, ``transaction``,
the raw SQL in ``query_builder``, the advisory locks). For a project whose data lives in a
customer's database none of that may land in labs, so for the length of one cache operation the
calling thread's ``default`` connection IS the customer's. The pipeline code is unchanged: it asks
for the default connection and gets that one.

What keeps this safe:

* it is thread-local (Django's connections are), so no other request or worker sees it;
* it wraps ONLY the backend's cache operations (``RedirectedBackend``), never the project gate, the
  membership checks or the page, all of which read labs' own tables and run outside it;
* the original connection is put back in a ``finally``, whatever happens inside;
* a nested redirect is refused rather than guessed at.
"""

from __future__ import annotations

import functools
import threading
from contextlib import contextmanager

from django.db import DEFAULT_DB_ALIAS, connections

_state = threading.local()


class RedirectError(RuntimeError):
    pass


@contextmanager
def default_connection_is(alias: str):
    """For this thread, inside the block, ``connections["default"]`` is ``connections[alias]``."""
    if getattr(_state, "active", None):
        raise RedirectError("a connection redirect is already active in this thread")
    original = connections[DEFAULT_DB_ALIAS]
    replacement = connections[alias]
    _state.active = alias
    connections[DEFAULT_DB_ALIAS] = replacement
    try:
        yield replacement
    finally:
        connections[DEFAULT_DB_ALIAS] = original
        _state.active = None


def active_alias() -> str | None:
    return getattr(_state, "active", None)


class RedirectedBackend:
    """A pipeline backend whose every call runs against the customer's database.

    Wraps each public method of the real backend. A method that returns a generator would keep
    running after the redirect ended, so those are refused here: the ``hq_store`` path only uses the
    calls that return their result (``process_and_cache`` and the cached-result reads).
    """

    def __init__(self, backend, alias: str):
        self._backend = backend
        self._alias = alias

    def __getattr__(self, name):
        attr = getattr(self._backend, name)
        if not callable(attr) or name.startswith("_"):
            return attr

        @functools.wraps(attr)
        def call(*args, **kwargs):
            with default_connection_is(self._alias):
                result = attr(*args, **kwargs)
                if hasattr(result, "__next__"):
                    raise RedirectError(f"{name} returned a generator, which would outlive the redirect")
                return result

        return call
