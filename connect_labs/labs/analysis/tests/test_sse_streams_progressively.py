"""An SSE body must reach the client while it is still being produced.

Django's ASGI handler CONSUMES A SYNCHRONOUS ITERATOR IN FULL before sending any
of it -- `ASGIHandler.send_response` warns "StreamingHttpResponse must consume
synchronous iterators in order to serve them asynchronously". So a view that
hands Django a sync generator does not stream: it builds one response body
slowly and delivers it at the end.

That is not a theoretical concern. It cost every SSE view in labs its progress
reporting, silently, for as long as they have existed: a 41-second cold pipeline
read on production delivered all eleven of its progress events in a single batch
at 41s, which on screen is indistinguishable from a page that has hung -- the
exact condition the events exist to rule out. Measured locally against uvicorn on
the same generator, sync delivered six events at 6.0s and async delivered them at
0.0/1.0/2.0/3.0/4.0/5.0s.

`BaseSSEStreamView` therefore serves an ASYNC iterator. These tests pin that,
because the failure is invisible in every other check we have: the response is
correct, the events are all present and in order, the status is 200, and only the
TIMING -- which no ordinary view test looks at -- is wrong.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from connect_labs.labs.analysis.sse_streaming import BaseSSEStreamView, send_sse_event


class _Stream(BaseSSEStreamView):
    heartbeat_interval = 0.05

    def stream_data(self, request):
        yield send_sse_event("first")
        yield send_sse_event("second")
        yield send_sse_event("Complete", data={"ok": True})


def _drain(view_cls, **attrs):
    view = view_cls()
    for k, v in attrs.items():
        setattr(view, k, v)

    async def run():
        return [chunk async for chunk in view._astream(view.stream_data(None))]

    return asyncio.run(run())


def test_the_wrapper_is_an_async_generator():
    """The whole point: a sync one is drained before a byte is sent."""
    assert inspect.isasyncgenfunction(BaseSSEStreamView._astream)


def test_every_event_survives_the_wrapper_in_order():
    chunks = _drain(_Stream)
    messages = [c for c in chunks if c.startswith("data: ")]
    assert len(messages) == 3
    assert '"first"' in messages[0]
    assert '"second"' in messages[1]
    assert '"ok": true' in messages[2]


def test_a_slow_producer_yields_before_it_finishes():
    """The regression itself: output must not wait for the generator to end."""

    class _Slow(BaseSSEStreamView):
        heartbeat_interval = 5  # long, so nothing here is a heartbeat

        def stream_data(self, request):
            yield send_sse_event("early")
            import time

            time.sleep(0.4)
            yield send_sse_event("Complete", data={"ok": True})

    view = _Slow()

    async def run():
        seen = []
        agen = view._astream(view.stream_data(None))
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        async for chunk in agen:
            seen.append((loop.time() - t0, chunk))
        return seen

    seen = asyncio.run(run())
    assert len(seen) == 2
    early_at, _ = seen[0]
    late_at, _ = seen[1]
    # The first event must arrive well before the sleep the second one waits on.
    assert early_at < 0.2, f"first event was withheld for {early_at:.2f}s"
    assert late_at >= 0.4


def test_heartbeats_fill_a_silence_and_stop_when_disabled():
    class _Quiet(BaseSSEStreamView):
        heartbeat_interval = 0.05

        def stream_data(self, request):
            import time

            time.sleep(0.3)
            yield send_sse_event("Complete", data={"ok": True})

    beats = [c for c in _drain(_Quiet) if c.startswith(":")]
    assert beats, "a silent producer sent no heartbeat, so the connection can idle out"

    quiet = [c for c in _drain(_Quiet, heartbeat_enabled=False) if c.startswith(":")]
    assert quiet == []


def test_a_failing_producer_raises_rather_than_ending_the_stream_cleanly():
    """A swallowed error reads as a complete, empty result."""

    class _Boom(BaseSSEStreamView):
        heartbeat_interval = 5

        def stream_data(self, request):
            yield send_sse_event("first")
            raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        _drain(_Boom)
