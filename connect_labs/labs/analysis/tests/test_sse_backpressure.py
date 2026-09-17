"""The backpressure in `_with_heartbeat` is a MEMORY bound, and nothing tested it.

On 2026-09-16 #1859 replaced this wrapper's `queue.Queue(maxsize=100)` — fed by a
blocking `put(..., timeout=1)` — with an unbounded `asyncio.Queue()` fed by
`put_nowait`. The change was correct about the thing it set out to fix (Django's
ASGI handler drains a *sync* iterator in full before sending any of it, so no SSE
view had ever actually streamed), and it was merged with **all eight checks
green, including the full pytest suite**.

It took down every pipeline-backed dashboard on labs for roughly fifteen hours
and was reverted in #1888. The producer thread no longer waited for the
consumer, so an entire pipeline read was held in the process until the client
drained it; the worker was OOM-killed mid-response, which is why the SSE `Error`
event that `stream_data` carefully yields never arrived — the process was killed,
not raised.

**The revert restored the bound and deleted the tests that came with #1859
(128 lines, all about progressiveness), leaving nothing at all asserting the
bound.** So re-landing the same change — and it will be re-landed, because the
problem #1859 fixed is real and still here — passes CI exactly as it did the
first time. This file is the check that fails instead.

It deliberately asserts a COST, not a behaviour: every behavioural property of
this wrapper is identical with an unbounded queue. What differs is only how much
the producer is allowed to run ahead of a consumer that is not reading, and that
is the whole defect.
"""

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
    """A stalled consumer must stall the producer — this is the OOM guard.

    With the bounded queue the producer fills ~100 slots and blocks in `put`.
    With #1859's unbounded queue it drains all 5000 into the process, which is
    the failure that killed the workers.
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
        f"The queue's bound (maxsize={DECLARED_MAXSIZE}) is not applying backpressure: "
        f"an unbounded queue holds the whole response in memory and OOM-kills the worker "
        f"(#1859, reverted in #1888)."
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
