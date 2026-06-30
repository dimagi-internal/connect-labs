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


# ---------------------------------------------------------------------------
# ASGI boundary close — the PRIMARY, comprehensive close point.
#
# ``server._closing_connections`` only wraps two named callables, so any new
# MCP DB entrypoint that forgets the wrapper would leak again. The ASGI
# middleware ``config.asgi._ClosingConnectionsApp`` wraps the WHOLE ``/mcp``
# mount, so it closes this request's connections at the request boundary
# regardless of which handler opened them. These tests pin that contract.
# ---------------------------------------------------------------------------


def _collect_send(sent):
    async def send(message):
        sent.append(message)

    return send


async def _noop_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def test_asgi_middleware_closes_connections_after_http_request():
    """After the wrapped app finishes an http request, the middleware closes
    this request's DB connections in a thread-sensitive executor."""
    import anyio

    from config import asgi

    sent: list = []

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    with mock.patch.object(asgi, "connections") as conns:
        app = asgi._ClosingConnectionsApp(inner)

        async def _run():
            await app({"type": "http"}, _noop_receive, _collect_send(sent))

        anyio.run(_run)

    # The whole response was delegated to the inner app...
    assert {m["type"] for m in sent} == {"http.response.start", "http.response.body"}
    # ...and the boundary close ran exactly once.
    conns.close_all.assert_called_once_with()


def test_asgi_middleware_closes_connections_even_on_exception():
    """If the wrapped MCP app raises, the boundary close still runs — a failing
    tool/auth path must not leak its connection."""
    import anyio

    from config import asgi

    async def inner(scope, receive, send):
        raise RuntimeError("mcp handler blew up")

    with mock.patch.object(asgi, "connections") as conns:
        app = asgi._ClosingConnectionsApp(inner)

        async def _run():
            await app({"type": "http"}, _noop_receive, _collect_send([]))

        with pytest.raises(RuntimeError, match="mcp handler blew up"):
            anyio.run(_run)

    conns.close_all.assert_called_once_with()


def test_asgi_middleware_skips_non_http_scopes():
    """Only http scopes get a boundary close. websocket/lifespan scopes pass
    through untouched (lifespan especially must not have its connections
    yanked mid-startup)."""
    import anyio

    from config import asgi

    for scope_type in ("websocket", "lifespan"):
        with mock.patch.object(asgi, "connections") as conns:
            seen = {}

            async def inner(scope, receive, send):
                seen["type"] = scope["type"]

            app = asgi._ClosingConnectionsApp(inner)

            async def _run():
                await app({"type": scope_type}, _noop_receive, _collect_send([]))

            anyio.run(_run)

        assert seen["type"] == scope_type
        conns.close_all.assert_not_called()
