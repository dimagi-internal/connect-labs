"""Cost + correctness properties of WorkflowRunView's per-opportunity worker fetch.

Measured 2026-08-26 on /labs/workflow/13234/run/: eleven SEQUENTIAL calls to
/export/opportunity/<id>/user_data/, ~350-580ms each, 4.3s of a 6.3s page. See #1301.
"""

import threading
import time
from unittest import mock

from connect_labs.utils import request_telemetry
from connect_labs.utils.request_telemetry import RequestStats, current_stats
from connect_labs.workflow.views import WorkflowRunView


def _view_with(opp_ids, get_workers):
    """Drive just the worker-fetch block of get_context_data."""
    view = WorkflowRunView()
    data_access = mock.Mock()
    data_access.get_workers.side_effect = get_workers
    return view, data_access


def _run_worker_block(data_access, effective_opp_ids):
    """Calls the REAL view method — never a copy of it.

    An earlier version of this file mirrored the implementation inline. That is
    worthless: it would keep passing against a re-serialised view, and it did in
    fact pass against a version of the fix whose context copying was broken.
    """
    return WorkflowRunView._load_workers(data_access, effective_opp_ids)


def test_worker_fetches_run_concurrently_not_serially():
    """The cost property. Eleven 100ms fetches must not take 1.1s.

    Asserting on the returned roster cannot catch a regression to a serial loop --
    the same workers come back either way, just slower. Assert on elapsed time.
    """
    opp_ids = list(range(1, 12))

    def slow(oid):
        time.sleep(0.1)
        return [{"username": f"u{oid}"}]

    started = time.perf_counter()
    workers = _run_worker_block(_view_with(opp_ids, slow)[1], opp_ids)
    elapsed = time.perf_counter() - started

    assert len(workers) == 11
    # Serial would be ~1.1s; 8 at a time is ~0.2s. Generous bound so this is not
    # flaky on a loaded machine, but far below serial.
    assert elapsed < 0.6, f"worker fetches look serial: {elapsed:.2f}s for 11 x 100ms"


def test_worker_fetches_use_distinct_threads():
    """Corroborates the timing assertion without depending on the clock."""
    opp_ids = list(range(1, 12))
    seen = set()
    lock = threading.Lock()

    def record(oid):
        with lock:
            seen.add(threading.get_ident())
        time.sleep(0.05)
        return []

    _run_worker_block(_view_with(opp_ids, record)[1], opp_ids)
    assert len(seen) > 1, "all fetches ran on one thread — the pool is not being used"


def test_roster_keeps_original_opportunity_order():
    """as_completed yields by completion time. The runner renders this list as
    given, so consuming that order directly would shuffle the roster between loads
    for no reason a user could understand."""
    opp_ids = [1, 2, 3]

    def staggered(oid):
        # Reverse the completion order relative to the input order.
        time.sleep({1: 0.15, 2: 0.10, 3: 0.01}[oid])
        return [{"username": f"u{oid}"}]

    workers = _run_worker_block(_view_with(opp_ids, staggered)[1], opp_ids)
    assert [w["opportunity_id"] for w in workers] == [1, 2, 3]


def test_one_failing_opportunity_does_not_lose_the_others():
    opp_ids = [1, 2, 3]

    def flaky(oid):
        if oid == 2:
            raise RuntimeError("Connect unavailable")
        return [{"username": f"u{oid}"}]

    workers = _run_worker_block(_view_with(opp_ids, flaky)[1], opp_ids)
    assert [w["opportunity_id"] for w in workers] == [1, 3]


def test_outbound_calls_are_still_counted_from_pool_threads():
    """The trap this fix could easily have introduced.

    request_telemetry tracks outbound calls in a ContextVar, and
    ThreadPoolExecutor does NOT propagate context to its workers. Submitting these
    bare would make every worker fetch invisible to the telemetry -- re-creating,
    in a new place, exactly the blind spot #1300 removed. copy_context() is what
    keeps them counted, and this is the assertion that notices if it is dropped.
    """
    opp_ids = [1, 2, 3]

    def calls_out(oid):
        request_telemetry.record_outbound_call("connect.dimagi.com", 12.0)
        return []

    token = request_telemetry._stats.set(RequestStats())
    try:
        _run_worker_block(_view_with(opp_ids, calls_out)[1], opp_ids)
        stats = current_stats()
        assert stats.outbound_calls == 3, (
            f"expected 3 counted outbound calls, got {stats.outbound_calls} — "
            "context is not propagating into the pool threads"
        )
        assert stats.outbound_by_host["connect.dimagi.com"] == 3
    finally:
        request_telemetry._stats.reset(token)
