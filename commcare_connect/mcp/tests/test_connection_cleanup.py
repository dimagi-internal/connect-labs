"""Regression tests for the MCP DB-connection leak (issue #667).

The FastMCP server is mounted in ``config.asgi`` as a Starlette sub-app OUTSIDE
Django's ``ASGIHandler``. Django's ``close_old_connections`` is driven by the
``request_started`` / ``request_finished`` signals, which only Django's request
handling emits — so an MCP request never triggers them. Each MCP call resolves
the PAT (``_verify_pat_sync``) and runs a tool (``_run_registry_tool``) in an
asgiref worker thread that opens a thread-local Django connection; with
``CONN_MAX_AGE>0`` that connection is kept open for reuse but, with no
request-finished signal, is never closed. It then sits ``idle`` on RDS forever,
leaking one connection per worker thread until the instance runs out of slots.

``_closing_connections`` restores per-request cleanup: it wraps the sync core
and closes the thread's connections in a ``finally``, in the same worker thread
that opened them. It is applied ONLY at the ``sync_to_async`` boundary (the
production path) — never around the sync core the in-process test bridge calls
directly on pytest-django's transactional connection.
"""

from __future__ import annotations

from unittest import mock

import pytest

from commcare_connect.mcp import server


def test_closing_connections_closes_after_success():
    sentinel = object()
    with mock.patch.object(server, "connections") as conns:
        wrapped = server._closing_connections(lambda: sentinel)
        result = wrapped()
    assert result is sentinel
    conns.close_all.assert_called_once_with()


def test_closing_connections_closes_after_exception():
    def boom():
        raise ValueError("handler blew up")

    with mock.patch.object(server, "connections") as conns:
        wrapped = server._closing_connections(boom)
        with pytest.raises(ValueError, match="handler blew up"):
            wrapped()
    # Cleanup must run even when the wrapped call raises — otherwise a failing
    # tool would leak its connection.
    conns.close_all.assert_called_once_with()


def test_closing_connections_passes_through_args_and_kwargs():
    with mock.patch.object(server, "connections"):
        wrapped = server._closing_connections(lambda a, b=0: a + b)
        assert wrapped(2, b=3) == 5


def test_closing_connections_is_transparent_wrapper():
    """functools.wraps keeps the wrapper transparent, so the production
    boundaries stay readable in tracebacks/introspection."""
    with mock.patch.object(server, "connections"):
        wrapped = server._closing_connections(server._verify_pat_sync)
    assert wrapped.__name__ == "_verify_pat_sync"
    assert wrapped.__wrapped__ is server._verify_pat_sync
