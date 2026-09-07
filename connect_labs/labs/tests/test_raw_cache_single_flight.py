"""The RawVisitCache stampede guard (#1361).

Two halves, and they fail differently.

The **lock** half is a real PostgreSQL advisory lock, so it is tested against a real
second connection. A mocked lock would prove only that the code calls the function it
calls; the question that matters — does a second connection actually get refused while
the first holds it — is exactly the one mocks cannot answer.

The **policy** half is what a loser does with the refusal: lend the rows it already
has rather than start a second walk, and fall through to a normal rebuild when there
is nothing to lend. That half is about branch selection, so it is tested directly.
"""

import threading

import pytest
from django.db import connection, connections

from connect_labs.labs.analysis.backends.sql.single_flight import claim_raw_rebuild, raw_rebuild_lock_key

pytestmark = pytest.mark.django_db(transaction=True)


# --- The key ---------------------------------------------------------------------


def test_the_key_partitions_exactly_as_the_cache_does():
    """The lock must split on (opportunity, pipeline), the same pair as
    SQLCacheManager._raw_filter. Coarser and unrelated pipelines serialise against
    each other; finer and two readers of one slot both walk."""
    base = raw_rebuild_lock_key(2155, 42)
    assert raw_rebuild_lock_key(2155, 42) == base  # stable
    assert raw_rebuild_lock_key(2156, 42) != base  # different opportunity
    assert raw_rebuild_lock_key(2155, 43) != base  # different pipeline
    assert raw_rebuild_lock_key(2155, None) != base  # the legacy None-tagged slot


def test_the_key_fits_a_signed_64_bit_advisory_lock():
    """pg_try_advisory_lock(bigint) rejects anything outside int8; an unsigned hash
    would raise NumericValueOutOfRange at the point of use, i.e. in production."""
    for opp, pipeline in [(1, None), (2155, 42), (10**9, 10**6)]:
        key = raw_rebuild_lock_key(opp, pipeline)
        assert -(2**63) <= key < 2**63


def test_the_key_is_accepted_by_postgres():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [raw_rebuild_lock_key(2155, 42)])
        assert cursor.fetchone()[0] is True
        cursor.execute("SELECT pg_advisory_unlock(%s)", [raw_rebuild_lock_key(2155, 42)])


# --- The lock, against a real second connection ------------------------------------


def _claim_on_another_connection(opportunity_id, pipeline_id, result):
    """Run claim_raw_rebuild on a genuinely separate DB connection.

    A thread gets its own connection under Django, which is what makes this a real
    test of the lock rather than of the same session re-entering it — advisory locks
    are re-entrant within one session, so doing this in-process on one connection
    would report success and prove nothing.
    """
    try:
        with claim_raw_rebuild(opportunity_id, pipeline_id) as acquired:
            result.append(acquired)
    finally:
        connections.close_all()


def test_a_second_connection_is_refused_while_the_first_holds_it():
    result = []
    with claim_raw_rebuild(2155, 42) as first:
        assert first is True
        t = threading.Thread(target=_claim_on_another_connection, args=(2155, 42, result))
        t.start()
        t.join(timeout=10)
    assert result == [False], "a concurrent rebuild of the SAME slot must be refused"


def test_a_different_slot_is_not_blocked():
    """The whole point of keying on the pair: opportunity 2156 must not wait behind
    a rebuild of 2155."""
    result = []
    with claim_raw_rebuild(2155, 42) as first:
        assert first is True
        t = threading.Thread(target=_claim_on_another_connection, args=(2156, 42, result))
        t.start()
        t.join(timeout=10)
    assert result == [True]


def test_the_lock_is_released_on_the_way_out():
    with claim_raw_rebuild(2155, 42) as first:
        assert first is True

    result = []
    t = threading.Thread(target=_claim_on_another_connection, args=(2155, 42, result))
    t.start()
    t.join(timeout=10)
    assert result == [True], "the lock outlived its context manager"


def test_the_lock_is_released_even_when_the_body_raises():
    """A rebuild that throws must not wedge the slot until the connection closes —
    outside a web request (Celery) connections are long-lived, so a leaked lock would
    block that opportunity for the life of the worker."""
    with pytest.raises(RuntimeError):
        with claim_raw_rebuild(2155, 42) as first:
            assert first is True
            raise RuntimeError("rebuild blew up")

    result = []
    t = threading.Thread(target=_claim_on_another_connection, args=(2155, 42, result))
    t.start()
    t.join(timeout=10)
    assert result == [True]


def test_it_fails_open_when_the_lock_cannot_be_taken(monkeypatch):
    """A stampede is a performance failure; refusing to serve would be a correctness
    one. If the lock itself errors, the caller must behave exactly as it did before
    this guard existed."""
    import connect_labs.labs.analysis.backends.sql.single_flight as sf

    class _Boom:
        def __enter__(self):
            raise RuntimeError("no cursor for you")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        sf.connections, "__getitem__", lambda _self, _alias=None: type("C", (), {"cursor": lambda s: _Boom()})()
    )

    with sf.claim_raw_rebuild(2155, 42) as acquired:
        assert acquired is True, "must fail OPEN, not refuse the rebuild"
