"""A forced refresh walks each opportunity's visit export ONCE, not once per pipeline (#1926).

#1921 put every user_visits reader on one shared raw slot per opportunity, so an
UNFORCED page load walks the export once and every other visits pipeline is a cache
hit. ``?refresh=1`` bypassed all of that: the forced branch skipped both the validity
check and the single-flight lock, and the runner forwards the flag to every pipeline
stream. On 2026-09-18 one forced open of workflow 20934 made nine full walks of
31-56k visits in 22 minutes -- each of two pipelines over four opportunities, plus a
third pipeline over one of them.

A forced read is now satisfied by a slot that was fetched recently enough: at or
after this request's first forced read, or within FORCED_REFRESH_REUSE_MINUTES. The
first pipeline walks; the rest reuse what it just fetched. Concurrent forced walks of
one slot coalesce under the rebuild lock -- the loser waits for the leader's rows
instead of walking alongside it.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import RequestFactory
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.backend import SQLBackend
from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

pytestmark = pytest.mark.django_db

OPP = 2154
PIPELINE_A = 20931
PIPELINE_B = 20932


@pytest.fixture(autouse=True)
def _no_leftover_anomaly_flags():
    # The shrink-anomaly flag lives in Django's cache, which outlives a test.
    cache.clear()
    yield
    cache.clear()


def _records(n, first_id=1000):
    return [
        {"id": first_id + i, "username": f"flw{i % 3}", "visit_date": "2026-09-17", "status": "approved"}
        for i in range(n)
    ]


def _counting_client(pages_per_walk):
    """A get_export_client stand-in whose paginate() replays `pages_per_walk` on every call."""
    client = MagicMock()
    client.paginate.side_effect = lambda *a, **k: iter(pages_per_walk)
    client.__enter__ = lambda s: client
    client.__exit__ = lambda s, *a: False
    return client


def _export(client):
    return patch("connect_labs.labs.integrations.connect.factory.get_export_client", return_value=client)


def _age_slot(minutes):
    """Pretend the slot's rows were fetched `minutes` ago (and have not expired)."""
    RawVisitCache.objects.filter(opportunity_id=OPP).update(
        created_at=timezone.now() - timedelta(minutes=minutes),
        expires_at=timezone.now() + timedelta(minutes=30),
    )


# --- Cost: the point of the change ------------------------------------------------


def test_a_forced_run_over_two_pipelines_on_one_opportunity_walks_the_export_once():
    """The falsifier for #1926. The second pipeline's forced read must reuse the rows the
    first pipeline's forced read fetched moments ago, not repaginate the whole export."""
    client = _counting_client([_records(50)])
    backend = SQLBackend()
    with _export(client):
        first = list(
            backend.stream_raw_visits(OPP, "t", expected_visit_count=50, force_refresh=True, pipeline_id=PIPELINE_A)
        )
        second = list(
            backend.stream_raw_visits(OPP, "t", expected_visit_count=50, force_refresh=True, pipeline_id=PIPELINE_B)
        )

    assert client.paginate.call_count == 1
    assert first[-1] == ("complete", 50)
    assert second == [("cached", 50)]


def test_a_forced_page_load_through_the_pipeline_walks_each_opportunity_once():
    """End to end through AnalysisPipeline, the way the runner's stream reaches it: the
    request carries ?refresh=1 and every pipeline on it reads the same opportunity."""
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    request = RequestFactory().get("/labs/workflow/20934/run/", {"refresh": "1"})
    client = _counting_client([_records(40)])
    with _export(client):
        for pipeline_id in (PIPELINE_A, PIPELINE_B, 20911):
            pipeline = AnalysisPipeline(request, access_token="t", cchq_access_token="h")
            # _stream_analysis_inner resolves ?refresh=1 to force_refresh=True and
            # hands it to this, once per (pipeline, opportunity).
            list(pipeline._consume_raw_visits_stream(OPP, force_refresh=True, pipeline_id=pipeline_id))
            assert pipeline._visit_count == 40

    assert client.paginate.call_count == 1


def test_forced_list_fetches_walk_once_too():
    """``fetch_raw_visits`` (audit, visit-cache warm-up) takes the same forced path."""
    client = _counting_client([_records(20)])
    backend = SQLBackend()
    with _export(client):
        first = backend.fetch_raw_visits(OPP, "t", expected_visit_count=20, force_refresh=True, pipeline_id=PIPELINE_A)
        second = backend.fetch_raw_visits(
            OPP, "t", expected_visit_count=20, force_refresh=True, pipeline_id=PIPELINE_B
        )

    assert client.paginate.call_count == 1
    assert len(first) == len(second) == 20


# --- Force still means force ---------------------------------------------------------


def test_a_force_still_walks_when_the_slot_is_older_than_the_reuse_window():
    """A valid-but-old slot is exactly what ?refresh=1 exists to replace."""
    client = _counting_client([_records(30)])
    backend = SQLBackend()
    with _export(client):
        list(backend.stream_raw_visits(OPP, "t", expected_visit_count=30, pipeline_id=PIPELINE_A))
        _age_slot(minutes=30)
        events = list(
            backend.stream_raw_visits(OPP, "t", expected_visit_count=30, force_refresh=True, pipeline_id=PIPELINE_B)
        )

    assert client.paginate.call_count == 2
    assert events[-1] == ("complete", 30)


def test_the_request_floor_reaches_past_the_window():
    """The stream behind #1926 came back to opp 2154 nine minutes after its first
    pipeline walked it -- outside any short window. What makes that a reuse is that
    the rows were fetched after THIS REQUEST first forced, and only that."""
    client = _counting_client([_records(30)])
    backend = SQLBackend()
    with _export(client):
        list(backend.stream_raw_visits(OPP, "t", expected_visit_count=30, force_refresh=True, pipeline_id=PIPELINE_A))
        _age_slot(minutes=9)
        request_began = timezone.now() - timedelta(minutes=10)
        reused = list(
            backend.stream_raw_visits(
                OPP,
                "t",
                expected_visit_count=30,
                force_refresh=True,
                pipeline_id=PIPELINE_B,
                force_refresh_since=request_began,
            )
        )
        # The same nine-minute-old slot, for a request that began AFTER it was fetched.
        walked = list(
            backend.stream_raw_visits(
                OPP,
                "t",
                expected_visit_count=30,
                force_refresh=True,
                pipeline_id=PIPELINE_B,
                force_refresh_since=timezone.now(),
            )
        )

    assert reused == [("cached", 30)]
    assert walked[-1] == ("complete", 30)
    assert client.paginate.call_count == 2


def test_the_pipeline_measures_every_pipeline_from_the_requests_first_force():
    """The runner builds a new AnalysisPipeline per pipeline; the floor must live on the
    request they share, or each pipeline would measure from its own start."""
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    request = RequestFactory().get("/labs/workflow/20934/run/", {"refresh": "1"})
    first = AnalysisPipeline(request, access_token="t", cchq_access_token="h").force_refresh_since()
    second = AnalysisPipeline(request, access_token="t", cchq_access_token="h").force_refresh_since()
    other_request = RequestFactory().get("/labs/workflow/20934/run/", {"refresh": "1"})
    third = AnalysisPipeline(other_request, access_token="t", cchq_access_token="h").force_refresh_since()

    assert first == second
    assert third > first


def test_a_forced_image_read_does_not_accept_a_fresh_slot_fetched_without_images():
    """A copy without photo payloads cannot answer an image read however new it is."""
    client = _counting_client([_records(10)])
    backend = SQLBackend()
    with _export(client):
        list(backend.stream_raw_visits(OPP, "t", expected_visit_count=10, force_refresh=True, pipeline_id=PIPELINE_A))
        backend.fetch_raw_visits(OPP, "t", expected_visit_count=10, force_refresh=True, include_images=True)
        backend.fetch_raw_visits(OPP, "t", expected_visit_count=10, force_refresh=True, include_images=True)

    assert client.paginate.call_count == 2
    assert client.paginate.call_args.kwargs["params"] == {"images": "true"}


# --- Coalescing concurrent forced walks ---------------------------------------------


class _FakeClaim:
    """claim_raw_rebuild stand-in: refuses the first `refusals` claims, then leads."""

    def __init__(self, refusals):
        self.refusals = refusals
        self.slots = []

    def __call__(self, opp, slot):
        self.slots.append((opp, slot))
        leader = len(self.slots) > self.refusals
        claim = MagicMock()
        claim.__enter__ = lambda s: leader
        claim.__exit__ = lambda s, *a: False
        return claim


def test_a_forced_loser_waits_for_the_leaders_rows_instead_of_walking_beside_it():
    """Two forced walks of one slot at once must coalesce. The loser cannot be lent the
    old rows -- replacing them is what it was asked for -- so it waits for the leader's."""
    client = _counting_client([_records(25)])
    backend = SQLBackend()
    with _export(client):
        list(backend.stream_raw_visits(OPP, "t", expected_visit_count=25, pipeline_id=PIPELINE_A))
    _age_slot(minutes=30)
    walks_before = client.paginate.call_count

    claim = _FakeClaim(refusals=1)

    def _peer_lands(_seconds):
        # While we sleep, the leader finishes its walk and finalizes fresh rows.
        RawVisitCache.objects.filter(opportunity_id=OPP).update(created_at=timezone.now())

    with (
        _export(client),
        patch("connect_labs.labs.analysis.backends.sql.backend.claim_raw_rebuild", claim),
        patch("connect_labs.labs.analysis.backends.sql.backend.time.sleep", side_effect=_peer_lands) as sleep,
    ):
        events = list(
            backend.stream_raw_visits(OPP, "t", expected_visit_count=25, force_refresh=True, pipeline_id=PIPELINE_B)
        )

    assert client.paginate.call_count == walks_before
    assert events == [("waiting", 0), ("cached", 25)]
    assert sleep.call_count == 1
    # The lock is the shared user_visits slot's, so every visits pipeline contends on it.
    assert claim.slots[0] == claim.slots[1] == (OPP, -1)


def test_a_forced_loser_walks_itself_once_the_leader_has_made_it_wait_too_long():
    """Fails open: a peer that never lands must not wedge the page."""
    client = _counting_client([_records(12)])
    backend = SQLBackend()
    with (
        _export(client),
        patch("connect_labs.labs.analysis.backends.sql.backend.claim_raw_rebuild", _FakeClaim(refusals=10**6)),
        patch("connect_labs.labs.analysis.backends.sql.backend.FORCED_REFRESH_PEER_WAIT_SECONDS", 0),
        patch("connect_labs.labs.analysis.backends.sql.backend.time.sleep") as sleep,
    ):
        events = list(
            backend.stream_raw_visits(OPP, "t", expected_visit_count=12, force_refresh=True, pipeline_id=PIPELINE_A)
        )

    assert client.paginate.call_count == 1
    assert events[-1] == ("complete", 12)
    sleep.assert_not_called()


def test_the_pipeline_tells_the_page_why_it_is_waiting():
    """A forced loser can sit for minutes; the page must say so rather than look hung."""
    from connect_labs.labs.analysis.pipeline import EVENT_STATUS, AnalysisPipeline

    request = RequestFactory().get("/labs/workflow/20934/run/", {"refresh": "1"})
    pipeline = AnalysisPipeline(request, access_token="t", cchq_access_token="h")
    pipeline.backend = MagicMock()
    pipeline.backend.stream_raw_visits.return_value = iter([("waiting", 4), ("cached", 7)])
    pipeline.backend.last_raw_fetch_anomaly = None

    events = list(pipeline._consume_raw_visits_stream(OPP, force_refresh=True, pipeline_id=PIPELINE_A))

    assert events[0][0] == EVENT_STATUS and "waiting" in events[0][1]["message"]
    assert pipeline._visit_count == 7
    assert pipeline.backend.stream_raw_visits.call_args.kwargs["force_refresh_since"] == pipeline.force_refresh_since()


def test_retry_now_on_a_shrink_anomaly_still_retries_however_fresh_the_rows():
    """The anomaly banner's "Retry now" is a forced read. The rows the guard kept may be
    minutes old, inside the reuse window -- reusing them would make the button a no-op."""
    client = _counting_client([_records(30)])
    backend = SQLBackend()
    with _export(client):
        list(backend.stream_raw_visits(OPP, "t", expected_visit_count=30, pipeline_id=PIPELINE_A))
        SQLCacheManager(OPP, pipeline_id=PIPELINE_A).set_pending_raw_fetch_anomaly(
            {"previous_count": 30, "attempted_count": 3, "threshold_pct": 80}, minutes=10
        )
        events = list(
            backend.stream_raw_visits(OPP, "t", expected_visit_count=30, force_refresh=True, pipeline_id=PIPELINE_B)
        )

    assert client.paginate.call_count == 2
    assert events[-1] == ("complete", 30)
