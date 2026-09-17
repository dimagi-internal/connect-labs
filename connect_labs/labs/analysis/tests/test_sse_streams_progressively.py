"""A stream must reach the client while it is still being produced.

This is the property #1859 set out to give labs and #1888 had to take back, and
it is the whole reason `_with_heartbeat` returns an async generator: Django's
ASGI handler drains a SYNC iterator in full before sending any of it --
`StreamingHttpResponse.__aiter__` falls back to
`await sync_to_async(list)(self.streaming_content)` and warns while doing it.
A sync body therefore arrives as one batch at the end, however carefully each
event was yielded, and every labs SSE view behaved that way until #1899.

Measured against real Django under real uvicorn, six events half a second
apart: the sync wrapper delivered all six at 3.04s; this one delivers them at
0.51 / 1.01 / 1.52 / 2.02 / 2.53 / 3.03.

The tests here pin the property WITHOUT a server, because a test that needs
uvicorn does not run in CI: what Django keys on is whether the body it is
handed is an async iterator, so that is what is asserted, alongside the
producer/consumer interleaving that makes the body worth streaming.
"""

import asyncio

import pytest

from connect_labs.labs.analysis.sse_streaming import BaseSSEStreamView


class _View(BaseSSEStreamView):
    """Bare instantiation — `_with_heartbeat` touches no request state."""


def test_the_body_django_receives_is_an_async_iterator():
    """The one property that decides batch-at-the-end versus streaming.

    Asserted on the object rather than on arrival times because this is exactly
    what `StreamingHttpResponse.__aiter__` branches on. A sync generator here
    means every SSE view on labs silently stops streaming again.
    """
    stream = _View()._with_heartbeat((f"data: {i}\n\n" for i in range(3)), interval=5)
    assert hasattr(stream, "__anext__"), (
        "the wrapper handed Django a SYNC iterator: Django will drain it with "
        "sync_to_async(list) before sending a byte, and nothing will stream (#1859/#1888)"
    )
    asyncio.run(stream.aclose())


def test_events_reach_the_consumer_before_the_producer_has_finished():
    """Interleaving, not just ordering: the consumer sees row N while the
    producer is still working on row N+1."""
    produced, seen = [], []

    def source():
        for i in range(6):
            produced.append(i)
            yield f"data: {i}\n\n"

    async def drive():
        stream = _View()._with_heartbeat(source(), interval=5)
        # chunk_size is 100, so a 6-row generator would be drained in one pull.
        # The point is the shape, so force one row per hand-off.
        async for item in stream:
            seen.append((item, len(produced)))

    view = _View()
    view.chunk_size = 1
    _View.chunk_size = 1
    try:
        asyncio.run(drive())
    finally:
        _View.chunk_size = BaseSSEStreamView.chunk_size

    assert [item for item, _ in seen] == [f"data: {i}\n\n" for i in range(6)]
    # The consumer saw the first event while the producer had made 1, not 6.
    assert seen[0][1] == 1, f"the whole generator ran before the first event was seen: {seen}"


def test_a_slow_generator_sends_heartbeats_while_the_consumer_waits():
    """The heartbeat exists to keep the ALB and the browser from timing out
    during a long blocking read, and it is only worth anything if it goes out
    BEFORE the read finishes — which is the same property as above."""

    def slow():
        yield "data: first\n\n"
        # Blocking, in the worker thread, as a real pipeline read is.
        import time

        time.sleep(0.6)
        yield "data: second\n\n"

    async def drive():
        stream = _View()._with_heartbeat(slow(), interval=0.1)
        return [item async for item in stream]

    view_chunk = BaseSSEStreamView.chunk_size
    BaseSSEStreamView.chunk_size = 1
    try:
        items = asyncio.run(drive())
    finally:
        BaseSSEStreamView.chunk_size = view_chunk

    assert ": heartbeat\n\n" in items, f"no heartbeat during a 0.6s blocking read: {items}"
    data = [i for i in items if i != ": heartbeat\n\n"]
    assert data == ["data: first\n\n", "data: second\n\n"]
    # The heartbeats sit BETWEEN the two events, not after both.
    assert items.index(": heartbeat\n\n") < items.index("data: second\n\n")


def test_the_kill_switch_falls_back_to_the_sync_wrapper(settings):
    """`LABS_SSE_ASYNC_STREAMING=False` must take the async path out of service
    without a revert — which is what the first attempt at this needed."""
    settings.LABS_SSE_ASYNC_STREAMING = False
    stream = _View()._with_heartbeat((f"data: {i}\n\n" for i in range(3)), interval=5)
    assert not hasattr(stream, "__anext__"), "the kill switch did not fall back"
    assert [i for i in stream if i != ": heartbeat\n\n"] == [f"data: {i}\n\n" for i in range(3)]


@pytest.mark.django_db
def test_the_worker_thread_does_not_leave_a_db_connection_open():
    """The generator runs off-request, so nothing else closes what it opened.

    Labs has already exhausted RDS's connection slots once this way (#667/#669),
    from ASGI sub-apps that bypass Django's request signals. A stream is the
    same shape of hazard: a thread that touches the ORM and is not a request.

    The worker's connection has to be captured INSIDE the generator and checked
    afterwards: `connections` is thread-local, so a test that inspects it from
    the test thread can never see the one that matters and passes regardless.
    """
    from django.db import DEFAULT_DB_ALIAS, connections

    captured = {}

    def source():
        # `connections[alias]`, not the `connection` PROXY: the proxy
        # re-resolves per thread on every attribute access, so holding it and
        # reading `.connection` back on the test thread inspects the TEST
        # thread's connection and the assertion means nothing.
        wrapper = connections[DEFAULT_DB_ALIAS]
        with wrapper.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        captured["wrapper"] = wrapper
        captured["open_during"] = wrapper.connection is not None
        yield "data: queried\n\n"

    async def drive():
        stream = _View()._with_heartbeat(source(), interval=5)
        return [item async for item in stream]

    items = asyncio.run(drive())
    assert items == ["data: queried\n\n"]
    assert captured["open_during"], "the generator never actually opened a connection"
    assert captured["wrapper"].connection is None, (
        "the stream's worker thread still holds a DB connection after the response ended. "
        "Nothing else will close it -- this is not a request thread (#667/#669)."
    )


def test_events_already_produced_survive_a_BaseException():
    """Losing the batch in hand is worse than the failure that caused it.

    `_drain` runs in a worker thread. If an exception escapes it, the future
    raises, the items already pulled are discarded, and the client gets nothing
    -- not even the events produced before the failure. Catching only
    `Exception` did exactly that, which was a regression against the wrapper
    this replaced: run both against a generator that raises `CancelledError`
    after its first event and the old one delivered that event while the new
    one delivered none.

    `CancelledError` is the realistic case rather than a contrived one: it is a
    BaseException in 3.8+, and a stream is exactly where cancellation lands.
    """
    delivered = []

    def one_then_cancelled():
        yield "data: first\n\n"
        raise asyncio.CancelledError()

    async def drive():
        stream = _View()._with_heartbeat(one_then_cancelled(), interval=5)
        with pytest.raises(asyncio.CancelledError):
            async for item in stream:
                delivered.append(item)

    asyncio.run(drive())
    assert delivered == ["data: first\n\n"], (
        "the event produced before the failure was dropped: a BaseException "
        "escaping the worker discards the whole batch in hand"
    )


def test_a_close_from_the_consumer_is_not_swallowed_as_an_error():
    """`GeneratorExit` is the consumer closing us, not the generator failing.

    Caught and reported as an error it would make close() a no-op, and the
    worker thread would keep pulling from a stream nobody is reading.
    """
    closed = {"yes": False}

    def source():
        try:
            for i in range(10_000):
                yield f"data: {i}\n\n"
        except GeneratorExit:
            closed["yes"] = True
            raise

    async def drive():
        stream = _View()._with_heartbeat(source(), interval=5)
        await stream.__anext__()
        await stream.aclose()

    asyncio.run(drive())
    assert closed["yes"], "the generator was never told the consumer had gone"
