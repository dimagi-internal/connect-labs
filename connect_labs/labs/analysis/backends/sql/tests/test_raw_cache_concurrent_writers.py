"""Two writers of one raw slot can never leave a short copy that reads as complete.

Production, opp 2230, 2026-10-24 23:08 UTC: a scheduled workflow run (W2) and a
scheduled audit (W5) both missed the cold user_visits slot within two seconds and
both paginated the export (2,643 visits = two pages of 2,500). W2 finalized first
and its finalize deleted every row in the slot that was not its own -- including
W5's first page, still under W5's sentinel. W5 then inserted its last 143 rows and
finalized, deleting W2's complete copy and labelling its 143 survivors
``visit_count=2643`` (what it had downloaded). ``has_valid_raw_cache`` compared the
label, so reports and audits read 143 visits as the whole opportunity until the TTL
-- or a visit-cache hold -- ran out. The same shape was found on opps 765, 524, 874,
2154, 2155, 2157 and 1488.

These tests drive the real ``SQLCacheManager`` through that interleaving and its
mirror image, and pin the four guards that now make the outcome impossible:

1. a finalize deletes only finalized rows and labels what it promotes, refusing to
   publish when rows it walked are missing;
2. a loser on a cold slot waits for the leader's rows instead of walking beside it;
3. validity reads the rows, so an already-short slot is a miss and rebuilds;
4. the visit-cache hold never reports "held" for, or extends, a short slot.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.db import connections, transaction
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.backend import SQLBackend
from connect_labs.labs.analysis.backends.sql.cache import RawCacheWriteRefused, SQLCacheManager
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

OPP = 2230
PAGE = 5  # stands in for page_size=2500
TOTAL = 7  # stands in for 2,643: one full page and a short last one


def _visits(first, n):
    return [{"id": first + i, "username": f"flw{i % 3}", "visit_date": "2026-10-24"} for i in range(n)]


PAGE_ONE = _visits(1000, PAGE)
PAGE_TWO = _visits(1000 + PAGE, TOTAL - PAGE)


def _slot():
    return RawVisitCache.objects.filter(opportunity_id=OPP, pipeline_id=SQLCacheManager(OPP).raw_slot_id)


def _finalized_labels():
    return sorted(set(_slot().filter(visit_count__gt=0).values_list("visit_count", flat=True)))


def _seed(label, rows, *, expires_in=timedelta(minutes=30)):
    """A finalized generation labelled `label` that actually holds `rows` rows."""
    manager = SQLCacheManager(OPP)
    RawVisitCache.objects.bulk_create(
        [
            RawVisitCache(
                opportunity_id=OPP,
                pipeline_id=manager.raw_slot_id,
                visit_count=label,
                expires_at=timezone.now() + expires_in,
                visit_id=str(1000 + i),
                username="flw0",
            )
            for i in range(rows)
        ]
    )
    return manager


def _records(n, first_id=1000):
    return [{"id": first_id + i, "username": f"flw{i % 3}", "visit_date": "2026-10-24"} for i in range(n)]


def _counting_client(pages):
    client = MagicMock()
    client.paginate.side_effect = lambda *a, **k: iter(pages)
    client.__enter__ = lambda s: client
    client.__exit__ = lambda s, *a: False
    return client


def _export(client):
    return patch("connect_labs.labs.integrations.connect.factory.get_export_client", return_value=client)


class _Claim:
    """claim_raw_rebuild stand-in: refuses the first `refusals` claims, then leads."""

    def __init__(self, refusals):
        self.refusals = refusals
        self.calls = 0

    def __call__(self, opp, slot):
        self.calls += 1
        leader = self.calls > self.refusals
        claim = MagicMock()
        claim.__enter__ = lambda s: leader
        claim.__exit__ = lambda s, *a: False
        return claim


# --- 1. The writers ------------------------------------------------------------------


@pytest.mark.django_db
class TestTwoWritersOfOneSlot:
    def test_the_incident_interleaving_ends_with_every_visit_under_a_true_label(self):
        """W2 and W5 walk the cold slot at once; W2 finalizes between W5's two pages.

        Before the fix W2's finalize deleted W5's first page and W5's finalize then
        deleted W2's copy, leaving 2 rows labelled 7 -- a copy has_valid_raw_cache(7)
        accepted."""
        w2, w5 = SQLCacheManager(OPP), SQLCacheManager(OPP)
        w2.store_raw_visits_start(0)
        w5.store_raw_visits_start(0)
        w2.store_raw_visits_batch(PAGE_ONE)
        w5.store_raw_visits_batch(PAGE_ONE)
        w2.store_raw_visits_batch(PAGE_TWO)
        w2_published = w2.store_raw_visits_finalize(TOTAL)
        w5.store_raw_visits_batch(PAGE_TWO)
        w5_published = w5.store_raw_visits_finalize(TOTAL)

        assert _slot().count() == TOTAL
        assert w2_published == w5_published == TOTAL
        assert _finalized_labels() == [TOTAL]
        assert SQLCacheManager(OPP).raw_generation() == (TOTAL, TOTAL)
        assert SQLCacheManager(OPP).has_valid_raw_cache(TOTAL)

    def test_the_mirror_order_ends_the_same_way(self):
        """W5 finishes first and W2 second: still one complete generation."""
        w2, w5 = SQLCacheManager(OPP), SQLCacheManager(OPP)
        w2.store_raw_visits_start(0)
        w5.store_raw_visits_start(0)
        w2.store_raw_visits_batch(PAGE_ONE)
        w5.store_raw_visits_batch(PAGE_ONE)
        w5.store_raw_visits_batch(PAGE_TWO)
        w5.store_raw_visits_finalize(TOTAL)
        w2.store_raw_visits_batch(PAGE_TWO)
        w2.store_raw_visits_finalize(TOTAL)

        assert _slot().count() == TOTAL
        assert SQLCacheManager(OPP).raw_generation() == (TOTAL, TOTAL)
        assert sorted(_slot().values_list("visit_id", flat=True)) == sorted(str(v["id"]) for v in PAGE_ONE + PAGE_TWO)

    def test_a_finalize_leaves_a_peers_in_progress_rows_alone(self):
        w2, w5 = SQLCacheManager(OPP), SQLCacheManager(OPP)
        w2.store_raw_visits_start(0)
        w5.store_raw_visits_start(0)
        w5.store_raw_visits_batch(PAGE_ONE)
        w2.store_raw_visits_batch(PAGE_ONE + PAGE_TWO)
        w2.store_raw_visits_finalize(TOTAL)

        assert _slot().filter(visit_count=w5._pending_visit_count).count() == PAGE

    def test_a_writer_that_lost_rows_publishes_nothing_and_keeps_the_prior_copy(self):
        """Rows a writer walked went missing (the expiry cleanup takes a walk that
        outlives its TTL, or an older writer still deleting sentinels). It used to
        label its survivors with what it had walked."""
        _seed(label=6, rows=6)
        writer = SQLCacheManager(OPP)
        writer.store_raw_visits_start(0)
        writer.store_raw_visits_batch(PAGE_ONE)
        writer.store_raw_visits_batch(PAGE_TWO)
        _slot().filter(visit_count=writer._pending_visit_count, visit_id__in=["1000", "1001", "1002"]).delete()

        with pytest.raises(RawCacheWriteRefused) as refused:
            writer.store_raw_visits_finalize(TOTAL)

        assert (refused.value.walked, refused.value.held, refused.value.peer_published) == (TOTAL, 4, False)
        assert SQLCacheManager(OPP).raw_generation() == (6, 6)  # the prior copy, untouched
        assert _slot().filter(visit_count__lt=0).count() == 0  # the refused rows are gone

    def test_the_streaming_walker_serves_the_copy_that_stands_when_its_write_is_refused(self):
        """A refusal on the stream path never reports its own walk as complete."""
        backend = SQLBackend()
        client = _counting_client([_records(PAGE), _records(TOTAL - PAGE, first_id=1000 + PAGE)])
        real_batch = SQLCacheManager.store_raw_visits_batch
        calls = []

        def batch_then_lose_a_row(self, visits):
            stored = real_batch(self, visits)
            calls.append(1)
            if len(calls) == 2:
                # A peer's complete copy lands, and one of our rows disappears.
                _slot().filter(visit_count=self._pending_visit_count).first().delete()
                peer = SQLCacheManager(OPP)
                peer.store_raw_visits_start(0)
                peer.store_raw_visits_batch(_records(TOTAL))
                peer.store_raw_visits_finalize(TOTAL)
            return stored

        with _export(client), patch.object(SQLCacheManager, "store_raw_visits_batch", batch_then_lose_a_row):
            events = list(backend.stream_raw_visits(OPP, "t", expected_visit_count=TOTAL))

        assert events[-1] == ("cached", TOTAL)
        assert SQLCacheManager(OPP).raw_generation() == (TOTAL, TOTAL)

    def test_store_raw_visits_labels_its_rows_by_their_count_and_spares_sentinels(self):
        streaming = SQLCacheManager(OPP)
        streaming.store_raw_visits_start(0)
        streaming.store_raw_visits_batch(PAGE_ONE)
        SQLCacheManager(OPP).store_raw_visits(PAGE_TWO, visit_count=TOTAL)

        assert SQLCacheManager(OPP).raw_generation() == (TOTAL - PAGE, TOTAL - PAGE)
        assert _slot().filter(visit_count=streaming._pending_visit_count).count() == PAGE


@pytest.mark.django_db(transaction=True)
def test_two_finalizes_of_the_same_generation_at_the_same_instant_keep_one_complete_copy():
    """The only way two finalizes can still meet: both inside their transactions at
    once. The later one collides on the unique key, stands down, and says so."""
    first_done = threading.Event()
    release_first = threading.Event()
    outcome = {}

    def first_writer():
        try:
            writer = SQLCacheManager(OPP)
            writer.store_raw_visits_start(0)
            writer.store_raw_visits_batch(PAGE_ONE + PAGE_TWO)
            with transaction.atomic():
                writer.store_raw_visits_finalize(TOTAL)
                first_done.set()
                release_first.wait(timeout=10)
        finally:
            connections.close_all()

    second = SQLCacheManager(OPP)
    second.store_raw_visits_start(0)
    second.store_raw_visits_batch(PAGE_ONE + PAGE_TWO)

    t = threading.Thread(target=first_writer)
    t.start()
    assert first_done.wait(timeout=10)
    # The second finalize blocks on the first's uncommitted rows until it commits.
    threading.Timer(0.5, release_first.set).start()
    try:
        second.store_raw_visits_finalize(TOTAL)
    except RawCacheWriteRefused as e:
        outcome["refused"] = e
    t.join(timeout=10)

    assert outcome["refused"].peer_published is True
    assert _slot().count() == TOTAL
    assert SQLCacheManager(OPP).raw_generation() == (TOTAL, TOTAL)


# --- 3. Validity reads the rows ------------------------------------------------------


@pytest.mark.django_db
class TestAShortSlotIsAMiss:
    def test_rows_below_the_label_are_not_a_valid_cache_even_with_no_expected_count(self):
        manager = _seed(label=2643, rows=143)
        assert manager.has_valid_raw_cache(0) is False
        assert manager.has_valid_raw_cache(2643) is False
        assert manager.whole_raw_visit_count() == 0

    def test_a_whole_slot_is_still_valid(self):
        manager = _seed(label=TOTAL, rows=TOTAL)
        assert manager.has_valid_raw_cache(0) and manager.has_valid_raw_cache(TOTAL)
        assert not manager.has_valid_raw_cache(TOTAL + 1)

    def test_a_short_slot_is_rebuilt_on_the_next_read_by_a_headless_caller(self):
        """opp 2230 today: 143 rows under 2,643. A headless caller passes no count, so
        the label was never even compared; any unexpired rows were served."""
        _seed(label=TOTAL, rows=2)
        client = _counting_client([_records(TOTAL)])
        with _export(client):
            rows = SQLBackend().fetch_raw_visits(OPP, "t", expected_visit_count=None)

        assert client.paginate.call_count == 1
        assert len(rows) == TOTAL
        assert SQLCacheManager(OPP).raw_generation() == (TOTAL, TOTAL)

    def test_a_short_slot_cannot_anchor_a_top_up(self):
        """The top-up sizes its gap from the label and re-stamps the survivors as if
        they were all there; only a full walk recovers the lost rows."""
        manager = _seed(label=TOTAL, rows=2)
        assert manager.get_raw_delta_anchor() is None

        client = _counting_client([_records(TOTAL + 3)])
        with _export(client):
            list(SQLBackend().stream_raw_visits(OPP, "t", expected_visit_count=TOTAL + 3))

        assert "last_id" not in (client.paginate.call_args.kwargs.get("params") or {})
        assert SQLCacheManager(OPP).raw_generation() == (TOTAL + 3, TOTAL + 3)

    def test_a_top_up_onto_a_short_base_rolls_back(self):
        manager = _seed(label=TOTAL, rows=2)
        manager.store_raw_visits_append_start(timezone.now() + timedelta(minutes=30))
        manager.store_raw_visits_batch(_records(2, first_id=5000))
        assert manager.store_raw_visits_append_finalize(TOTAL, TOTAL + 2) == 0
        assert _finalized_labels() == [TOTAL]
        assert _slot().count() == 2

    def test_a_short_slot_is_never_lent_to_a_loser(self):
        _seed(label=TOTAL, rows=2, expires_in=timedelta(minutes=-5))
        lent = SQLBackend()._lend_cache_during_peer_rebuild(
            SQLCacheManager(OPP), OPP, None, skip_form_json=True, count_only=True
        )
        assert lent is None

    def test_a_short_slot_is_never_extended(self):
        manager = _seed(label=TOTAL, rows=2, expires_in=timedelta(minutes=1))
        assert manager.extend_raw_cache_ttl(180) == 0
        assert _slot().first().expires_at < timezone.now() + timedelta(minutes=2)


# --- 2. One walk per cold slot -------------------------------------------------------


@pytest.mark.django_db
class TestALoserOnAColdSlotWaits:
    def _leader_lands(self, _seconds):
        # While the loser sleeps, the leader's walk finalizes.
        if not _slot().exists():
            SQLCacheManager(OPP).store_raw_visits(_records(TOTAL), TOTAL)

    def test_the_stream_loser_serves_the_leaders_rows_and_never_walks(self):
        client = _counting_client([_records(TOTAL)])
        with (
            _export(client),
            patch("connect_labs.labs.analysis.backends.sql.backend.claim_raw_rebuild", _Claim(refusals=10**6)),
            patch("connect_labs.labs.analysis.backends.sql.backend.time.sleep", side_effect=self._leader_lands),
        ):
            events = list(SQLBackend().stream_raw_visits(OPP, "t", expected_visit_count=TOTAL))

        assert client.paginate.call_count == 0
        assert events == [("waiting", 0), ("cached", TOTAL)]

    def test_the_list_loser_serves_the_leaders_rows_and_never_walks(self):
        """The audit's path: filter_visits_for_audit -> fetch_raw_visits."""
        client = _counting_client([_records(TOTAL)])
        with (
            _export(client),
            patch("connect_labs.labs.analysis.backends.sql.backend.claim_raw_rebuild", _Claim(refusals=10**6)),
            patch("connect_labs.labs.analysis.backends.sql.backend.time.sleep", side_effect=self._leader_lands),
        ):
            rows = SQLBackend().fetch_raw_visits(OPP, "t", expected_visit_count=None)

        assert client.paginate.call_count == 0
        assert len(rows) == TOTAL

    def test_a_loser_that_becomes_leader_after_the_wait_walks_once(self):
        """The peer's walk failed and released the lock: the next claim leads and walks."""
        client = _counting_client([_records(TOTAL)])
        with (
            _export(client),
            patch("connect_labs.labs.analysis.backends.sql.backend.claim_raw_rebuild", _Claim(refusals=1)),
            patch("connect_labs.labs.analysis.backends.sql.backend.time.sleep") as sleep,
        ):
            events = list(SQLBackend().stream_raw_visits(OPP, "t", expected_visit_count=TOTAL))

        assert client.paginate.call_count == 1
        assert sleep.call_count == 1
        assert events[0] == ("waiting", 0) and events[-1] == ("complete", TOTAL)

    def test_a_loser_walks_itself_once_the_wait_runs_out(self):
        client = _counting_client([_records(TOTAL)])
        with (
            _export(client),
            patch("connect_labs.labs.analysis.backends.sql.backend.claim_raw_rebuild", _Claim(refusals=10**6)),
            patch("connect_labs.labs.analysis.backends.sql.backend.COLD_SLOT_PEER_WAIT_SECONDS", 0),
            patch("connect_labs.labs.analysis.backends.sql.backend.time.sleep") as sleep,
        ):
            rows = SQLBackend().fetch_raw_visits(OPP, "t", expected_visit_count=TOTAL)

        assert client.paginate.call_count == 1
        assert len(rows) == TOTAL
        sleep.assert_not_called()


# --- 4. The visit-cache hold ---------------------------------------------------------


def _real_slot_factory(client):
    """`ensure_visit_cache`'s wiring with the real cache manager and backend, headless
    (no request, so no expected count), and a pipeline run that does nothing."""

    def factory(access_token, owner_scope, sources=None):
        backend = SQLBackend()

        def manager(opp, pid):
            return SQLCacheManager(opp)

        def fetch(opp, pid):
            with _export(client):
                return len(backend.fetch_raw_visits(opp, "t", expected_visit_count=0, skip_form_json=True))

        def run(opp, pid):
            return {"rows": [], "metadata": {"from_cache": True}}

        return manager, fetch, run

    return factory


class _Definition:
    opportunity_ids = [OPP]
    pipeline_sources = [{"pipeline_id": 20314, "alias": "visits"}]
    data = {}


class _DAO:
    access_token = "t"

    def get_definition(self, definition_id):
        return _Definition()


@pytest.mark.django_db
class TestTheVisitCacheHold:
    def test_a_short_slot_is_rebuilt_not_reported_held(self):
        from connect_labs.workflow import visit_cache as vc

        _seed(label=TOTAL, rows=2)
        client = _counting_client([_records(TOTAL)])
        report = vc.ensure_visit_cache(_DAO(), 1, opportunity_id=OPP, slot_factory=_real_slot_factory(client))

        [slot] = report["opportunities"][0]["slots"]
        assert slot["raw"] == "refreshed"
        assert slot["raw_visits"] == TOTAL
        assert client.paginate.call_count == 1
        assert SQLCacheManager(OPP).raw_generation() == (TOTAL, TOTAL)

    def test_a_short_slot_is_not_held_by_a_later_batch(self):
        from connect_labs.workflow import visit_cache as vc

        _seed(label=TOTAL, rows=2)
        held = vc.extend_if_held(_DAO(), 1, opportunity_id=OPP, slot_factory=_real_slot_factory(None))
        assert held is None
