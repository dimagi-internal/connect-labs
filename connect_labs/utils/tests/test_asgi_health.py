"""``/health/`` is answered in the ASGI layer, ahead of Django (``config/health.py``).

The point is that a busy Django cannot starve the ALB's check, so these pin
that the combined app answers it without reaching Django, that it still tells
the ALB the truth when the process stops making progress, and that the
production lifespan runs the heartbeat.
"""

from __future__ import annotations

import asyncio
import time

import anyio
import httpx
import pytest

from config import health


@pytest.fixture
def asgi_app():
    """A fresh combined app per test (see test_asgi_integration.asgi_app for why)."""
    from config.asgi import build_application

    return build_application()


@pytest.fixture(autouse=True)
def _no_heartbeat():
    health._last_beat = None
    yield
    health._last_beat = None


def _get(app, method="GET", path="/health/"):
    async def _run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            return await client.request(method, path)

    return anyio.run(_run)


class _Untouchable:
    """An inner app the middleware must not reach for a health check."""

    called = False

    async def __call__(self, scope, receive, send):
        type(self).called = True
        raise AssertionError("the health check reached the app behind the middleware")


def test_combined_app_answers_health_before_django(asgi_app, monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abc123")
    resp = _get(asgi_app)
    assert resp.status_code == 200
    assert resp.json() == {"status": "OK", "git_sha": "abc123"}
    # The Django view sets no cache-control; only the ASGI answer does.
    assert resp.headers["cache-control"] == "no-store"


def test_middleware_never_calls_the_inner_app():
    _Untouchable.called = False
    resp = _get(health.HealthMiddleware(_Untouchable()))
    assert resp.status_code == 200
    assert _Untouchable.called is False


def test_head_has_no_body():
    resp = _get(health.HealthMiddleware(_Untouchable()), method="HEAD")
    assert resp.status_code == 200
    assert resp.content == b""


def test_other_methods_and_paths_pass_through():
    for method, path in (("POST", "/health/"), ("GET", "/health"), ("GET", "/labs/")):
        _Untouchable.called = False
        with pytest.raises(AssertionError, match="reached the app"):
            _get(health.HealthMiddleware(_Untouchable()), method=method, path=path)
        assert _Untouchable.called is True


def test_a_stalled_thread_pool_answers_503(monkeypatch):
    health._last_beat = time.monotonic() - (health.STALL_S + 30)
    resp = _get(health.HealthMiddleware(_Untouchable()))
    assert resp.status_code == 503
    assert resp.json()["status"] == "stalled"


def test_a_recent_beat_answers_200():
    health._last_beat = time.monotonic() - 1
    assert _get(health.HealthMiddleware(_Untouchable())).status_code == 200


def test_heartbeat_beats_and_clears(monkeypatch):
    monkeypatch.setattr(health, "BEAT_S", 0.01)

    async def _run():
        async with health.heartbeat():
            first = health._last_beat
            await asyncio.sleep(0.1)
            assert health._last_beat is not None and health._last_beat > first
        assert health._last_beat is None

    anyio.run(_run)


@pytest.mark.django_db(transaction=True)
def test_production_lifespan_runs_the_heartbeat(asgi_app):
    """The app's lifespan (the one gunicorn enters) starts the heartbeat beside the MCP one."""

    async def _run():
        assert health._last_beat is None
        async with asgi_app.router.lifespan_context(asgi_app):
            assert health._last_beat is not None
        assert health._last_beat is None

    anyio.run(_run)
