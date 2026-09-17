"""What a re-land of #1859 must not do. Two costs, neither of them behaviour.

On 2026-09-16 #1859 replaced this wrapper's `queue.Queue(maxsize=100)` — fed by a
blocking `put(..., timeout=1)` — with an unbounded `asyncio.Queue()` fed by
`put_nowait`, one `loop.call_soon_threadsafe` per row. It was correct about the
thing it set out to fix (Django's ASGI handler drains a *sync* iterator in full
before sending any of it — `StreamingHttpResponse.__aiter__` falls back to
`await sync_to_async(list)(...)`, with a warning saying so — so no SSE view has
ever actually streamed, and none does today). It was merged with **all eight
checks green, including the full pytest suite**, took down every pipeline-backed
dashboard on labs for roughly fifteen hours, and was reverted in #1888.

TWO THINGS WENT WRONG, and this file pins both:

1. **Pacing.** The producer thread no longer waited for the consumer: measured,
   it ran 20,000 rows ahead of a consumer that had read one, against ~100 here.

2. **One event-loop callback per row.** Every other request on that worker
   shares that loop, which is the direct explanation for why one heavy read
   took out unrelated requests.

NOT memory, and an earlier version of this docstring said memory three times.
Django materialises the whole sync body anyway (see the `__aiter__` fallback
above), so this bound does not reduce peak memory and may well raise it versus a
design that frees each chunk as it is sent. What the bound buys is pacing. The
distinction matters because a re-lander told "unbounded queue → OOM" can
reasonably conclude that a design which frees as it goes is therefore safe, drop
the bound, and reproduce defect 2 exactly.

**The revert restored the bound and deleted the tests that came with #1859
(128 lines, all about progressiveness), leaving nothing asserting either cost.**
So re-landing the same shape — and it will be re-landed, because the problem
#1859 fixed is real and still here — passes CI exactly as it did the first time.
This file is what fails instead.

Both assertions are COSTS, not behaviours: every behavioural property of this
wrapper is identical with an unbounded push queue. A design that streams *and*
passes this file exists — pull chunks from the generator in a worker thread
(one hand-off per chunk, at most one chunk in flight) rather than pushing each
row at the loop.
"""

import asyncio
import threading
import time

from connect_labs.labs.analysis.sse_streaming import BaseSSEStreamView

# The wrapper's own bound. Not imported, on purpose: a test that reads the
# constant it is checking passes when someone changes the constant, which is the
# regression. 100 is asserted here as a fact about the design.
DECLARED_MAXSIZE = 100

# Generous headroom over DECLARED_MAXSIZE (queue + the item in flight + the
# consumer's own lookahead) while still an order of magnitude below TOTAL_ITEMS,
# so the assertion cannot be satisfied by an unbounded producer that merely
# happens to be slow.
RUNAWAY_THRESHOLD = 500
TOTAL_ITEMS = 5000


class _View(BaseSSEStreamView):
    """Bare instantiation — `_with_heartbeat` touches no request state."""


def _counting_generator(counter, produced_enough):
    """Yields TOTAL_ITEMS, recording how far the producer thread actually got."""
    for i in range(TOTAL_ITEMS):
        counter["produced"] += 1
        if counter["produced"] >= RUNAWAY_THRESHOLD:
            # Let the test stop waiting the moment the bound is already broken,
            # instead of sitting out the full sleep for a result it has.
            produced_enough.set()
        yield f"data: item-{i}\n\n"


def test_producer_stops_when_the_consumer_stops_reading():
    """A stalled consumer must stall the producer. Defect 1.

    With the bounded queue the producer fills ~100 slots and blocks in `put`.
    With #1859's unbounded queue it drains all 5000 into the process as fast as
    the generator can produce them.
    """
    counter = {"produced": 0}
    produced_enough = threading.Event()
    view = _View()
    stream = view._with_heartbeat(_counting_generator(counter, produced_enough), interval=0.05)

    # Pull exactly one event, which starts the producer thread, then stop reading.
    first = next(stream)
    assert first == "data: item-0\n\n"

    # Wait up to 3s, returning early if the bound is already visibly broken.
    produced_enough.wait(timeout=3.0)

    produced = counter["produced"]
    stream.close()

    assert produced < RUNAWAY_THRESHOLD, (
        f"producer ran {produced} items ahead of a consumer that read ONE. "
        f"The queue's bound (maxsize={DECLARED_MAXSIZE}) is not applying backpressure. "
        f"An unbounded queue lets a pipeline read run at full speed regardless of the "
        f"client (#1859, reverted in #1888)."
    )


def test_a_reading_consumer_still_gets_every_event_in_order():
    """The bound must not cost correctness — otherwise the fix for it is to remove it."""
    view = _View()
    expected = [f"data: item-{i}\n\n" for i in range(250)]  # > DECLARED_MAXSIZE

    def _source():
        # A real generator, not iter(list): the producer's `finally` calls
        # .close() on whatever it was handed, which a list_iterator does not have.
        yield from expected

    got = [chunk for chunk in view._with_heartbeat(_source(), interval=5) if chunk != ": heartbeat\n\n"]
    assert got == expected


def test_an_exception_from_the_generator_reaches_the_consumer():
    """`stream_data` relies on this to emit its SSE Error event.

    Worth pinning next to the bound: #1888's diagnosis turned on the fact that
    the error event never arrived, which was the tell that the process was being
    killed rather than raising. If this path ever breaks for an ordinary reason,
    that tell stops working.
    """
    boom = RuntimeError("pipeline exploded")

    def _raises():
        yield "data: first\n\n"
        raise boom

    view = _View()
    stream = view._with_heartbeat(_raises(), interval=5)
    assert next(stream) == "data: first\n\n"
    try:
        next(stream)
    except RuntimeError as exc:
        assert exc is boom
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("the generator's exception never reached the consumer")


def test_the_wrapper_terminates_promptly_once_the_consumer_closes():
    """A stalled producer must not outlive the response — it holds the queue."""
    counter = {"produced": 0}
    produced_enough = threading.Event()
    view = _View()
    before = threading.active_count()
    stream = view._with_heartbeat(_counting_generator(counter, produced_enough), interval=0.05)
    next(stream)
    stream.close()

    deadline = time.monotonic() + 5.0
    while threading.active_count() > before and time.monotonic() < deadline:
        time.sleep(0.05)
    assert threading.active_count() <= before, "the producer thread outlived the closed stream"


# --- defect 2: the event loop is shared, and a callback per row floods it ----

# One hand-off per this many rows is the line between "chunked" and "per row".
# A chunked pull at chunk=100 measures 0.01; #1859's push measures 1.0. Anything
# under this bound is a design that hands over batches, whatever its shape.
MAX_LOOP_CALLBACKS_PER_ROW = 0.1


def _consume(stream):
    """Iterate `stream` whether it is a sync or an async generator.

    Written for both on purpose: the sync wrapper is what ships today, and the
    whole point of this file is to still apply after someone makes it async.
    """
    if hasattr(stream, "__anext__"):

        async def drain():
            return [item async for item in stream]

        return asyncio.run(drain())
    return list(stream)


def test_the_wrapper_does_not_schedule_an_event_loop_callback_per_row():
    """Defect 2, the one that explains the blast radius.

    `loop.call_soon_threadsafe` takes the loop's lock and writes its self-pipe to
    wake the selector. Every other request on that worker shares that loop, so a
    per-row callback from one heavy read is felt by requests that have nothing to
    do with it. #1859 scheduled 20,001 callbacks for 20,000 rows.

    The sync wrapper that ships today schedules none — it never touches a loop —
    so this passes trivially now and is here for what replaces it.
    """
    rows = 2000
    view = _View()
    calls = {"n": 0}

    async def drive():
        loop = asyncio.get_running_loop()
        original = loop.call_soon_threadsafe

        def counting(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        loop.call_soon_threadsafe = counting
        try:
            stream = view._with_heartbeat((f"data: item-{i}\n\n" for i in range(rows)), interval=5)
            # A sync generator consumed inside the coroutine blocks this loop,
            # which is fine: we are counting schedules, not measuring latency.
            return len(_consume(stream))
        finally:
            loop.call_soon_threadsafe = original

    delivered = asyncio.run(drive())
    assert delivered == rows, f"the wrapper dropped events: {delivered} of {rows}"
    per_row = calls["n"] / rows
    assert per_row <= MAX_LOOP_CALLBACKS_PER_ROW, (
        f"{calls['n']} event-loop callbacks for {rows} rows ({per_row:.3f} per row). "
        f"Hand rows over in batches, not one at a time — every other request on this "
        f"worker shares the loop (#1859, reverted in #1888)."
    )
