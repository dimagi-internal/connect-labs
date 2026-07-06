"""Unit tests for the reusable async-progress primitives:
``throttled`` (rate-limited side effects) and ``progress_relays`` (the in-process
relay registry that generalizes the former audit-only ``AUDIT_PROGRESS_RELAYS``).
No Django/DB needed — these are pure Python.
"""

from connect_labs.utils.progress_relays import _RELAYS, get_relay, pop_relay, register_relay
from connect_labs.utils.throttle import throttled


class TestThrottled:
    def test_first_call_always_fires(self):
        writes = []
        f = throttled(writes.append, interval=100)  # huge interval
        f("a")
        assert writes == ["a"]

    def test_second_call_within_interval_is_dropped(self):
        writes = []
        f = throttled(writes.append, interval=100)
        f("a")
        f("b")  # within interval → dropped
        assert writes == ["a"]

    def test_force_bypasses_throttle(self):
        writes = []
        f = throttled(writes.append, interval=100)
        f("a")
        f("b")  # dropped
        f("z", force=True)  # forced → fires even though within interval
        assert writes == ["a", "z"]

    def test_forwards_args_and_returns_value_on_real_call(self):
        f = throttled(lambda x, y=0: x + y, interval=100)
        assert f(2, y=3) == 5  # first call fires
        assert f(9) is None  # throttled → None

    def test_zero_interval_never_throttles(self):
        writes = []
        f = throttled(writes.append, interval=0)
        f("a")
        f("b")
        f("c")
        assert writes == ["a", "b", "c"]


class TestProgressRelays:
    def teardown_method(self):
        _RELAYS.clear()

    def test_register_get_pop_roundtrip(self):
        register_relay(555, lambda: 42)
        relay = get_relay(555)
        assert relay is not None and relay() == 42
        pop_relay(555)
        assert get_relay(555) is None

    def test_none_run_id_is_safe_noop(self):
        register_relay(None, lambda: 1)  # ignored
        assert get_relay(None) is None
        assert _RELAYS == {}

    def test_pop_is_idempotent(self):
        pop_relay(999)  # never registered — must not raise
        register_relay(1, lambda: None)
        pop_relay(1)
        pop_relay(1)  # again — no error
        assert get_relay(1) is None

    def test_missing_key_returns_none(self):
        assert get_relay(123456) is None
