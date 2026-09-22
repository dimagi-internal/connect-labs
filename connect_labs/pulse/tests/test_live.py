"""The live views' shared answers: the newest visit, and the ingest verdict.

Two things are worth pinning. A night map that is already caught up must cost
the server nothing but a cache read -- that is the whole point. And the cache
must never be what delays a new service reaching a screen, or a stream's
failure reaching its LIVE badge: ingest drops the cached answer the moment it
changes it.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from connect_labs.pulse import ingest, live
from connect_labs.pulse.api import _ingest_state
from connect_labs.pulse.models import PulseIngestHealth
from connect_labs.pulse.tests.test_api import make_event, populated  # noqa: F401
from connect_labs.pulse.tests.test_ingest import opp, visit  # noqa: F401


@pytest.fixture
def cached_live(settings):
    """The live cache switched on, as it is in every deployed environment."""
    settings.PULSE_LIVE_CACHE_SECONDS = 60
    cache.clear()
    yield
    cache.clear()


def _pulse_tables(queries) -> list[str]:
    return [q["sql"] for q in queries if '"pulse_' in q["sql"] or '"users_user"' in q["sql"]]


@pytest.mark.django_db
class TestACaughtUpPollIsFree:
    def test_reads_no_pulse_table_and_no_poller_account(self, client, populated, cached_live):  # noqa: F811
        ingest.record_success("tail")
        cursor = client.get(reverse("pulse:api_events")).json()["cursor"]
        client.get(reverse("pulse:api_events"), {"since": cursor})  # warms the head and the verdict

        with CaptureQueriesContext(connection) as queries:
            payload = client.get(reverse("pulse:api_events"), {"since": cursor}).json()

        assert payload["events"] == []
        assert payload["ingest"]["live_ok"] is True
        assert _pulse_tables(queries.captured_queries) == []

    def test_answers_the_same_as_the_full_query_did(self, client, populated):  # noqa: F811
        """Same shape as before: no events, no cursor (the client keeps its own),
        and the ingest verdict."""
        cursor = client.get(reverse("pulse:api_events")).json()["cursor"]
        payload = client.get(reverse("pulse:api_events"), {"since": cursor}).json()
        assert payload["events"] == []
        assert payload["cursor"] is None
        assert "live_ok" in payload["ingest"]


@pytest.mark.django_db
class TestTheCacheNeverDelaysNews:
    def test_a_poll_behind_the_head_still_gets_what_is_new(self, client, populated, cached_live):  # noqa: F811
        cursor = client.get(reverse("pulse:api_events")).json()["cursor"]
        client.get(reverse("pulse:api_events"), {"since": cursor})  # head now cached at `cursor`

        make_event(cursor + 1)
        live.events_arrived()

        payload = client.get(reverse("pulse:api_events"), {"since": cursor}).json()
        assert [row[0] for row in payload["events"]] == [cursor + 1]
        assert payload["cursor"] == cursor + 1

    def test_storing_events_moves_the_head_at_once(self, opp, cached_live):  # noqa: F811
        ingest._store_events([visit(1)], opp)
        assert live.head() == 1
        ingest._store_events([visit(2)], opp)
        assert live.head() == 2  # not the cached 1

    def test_a_tier_failing_reaches_the_live_badge_at_once(self, populated, cached_live):  # noqa: F811
        ingest.record_success("tail")
        assert _ingest_state()["live_ok"] is True  # now cached

        for _ in range(5):
            ingest.record_failure("tail", "Connect timed out")
        assert _ingest_state()["live_ok"] is False

    def test_a_tier_recovering_reaches_it_at_once_too(self, populated, cached_live):  # noqa: F811
        PulseIngestHealth.objects.create(tier="tail", last_success_at=timezone.now() - timedelta(days=1))
        assert _ingest_state()["live_ok"] is False  # now cached

        ingest.record_success("tail")
        assert _ingest_state()["live_ok"] is True


@pytest.mark.django_db
class TestACacheOutageCostsFreshnessNotIngest:
    def test_invalidation_errors_are_swallowed(self, opp, cached_live):  # noqa: F811
        with mock.patch.object(live.cache, "delete", side_effect=ConnectionError("redis down")):
            created, _ = ingest._store_events([visit(7)], opp)
            ingest.record_success("tail")
        assert created == 1
        assert PulseIngestHealth.objects.get(tier="tail").consecutive_failures == 0


@pytest.mark.django_db
def test_the_widget_refreshes_at_the_hot_tier_cadence(client, populated):  # noqa: F811
    assert client.get(reverse("pulse:api_widget")).json()["poll_seconds"] == 15
