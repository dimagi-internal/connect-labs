"""The header widget's data: Pulse in one line, on any labs page that asks.

What matters most here is what the payload does NOT carry. It is shown on pages
that have nothing to do with Pulse, to anyone signed in, so it must never hold
a partner's identity or a coordinate -- and it must never call stored data live.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from connect_labs.pulse import ingest
from connect_labs.pulse.api import widget_payload
from connect_labs.pulse.models import PulseEvent, PulseIngestHealth, PulseOpportunity


def event(vid, *, synced_ago, opp=765, service="kmc", country="NG", org="partner-workspace"):
    ts = timezone.now() - synced_ago
    return PulseEvent.objects.create(
        connect_visit_id=vid,
        opportunity_id=opp,
        org_slug=org,
        field_ts=ts - timedelta(minutes=5),
        sync_ts=ts,
        lat=11.0312,
        lon=7.6341,
        country=country,
        status="approved",
        service_slug=service,
        worker_hash="985770f1bf2079f58119",
    )


@pytest.fixture
def pulse(db, settings, django_user_model):
    cache.clear()
    django_user_model.objects.create(username="poller-account")
    settings.PULSE_POLLER_USERNAME = "poller-account"
    PulseOpportunity.objects.create(opportunity_id=765, name="KMC Nigeria", is_active=True)
    PulseOpportunity.objects.create(opportunity_id=999, name="Sandbox", is_test=True)

    event(1, synced_ago=timedelta(minutes=4))
    event(2, synced_ago=timedelta(minutes=40), service="chc", country="UG")
    event(3, synced_ago=timedelta(hours=5))
    event(4, synced_ago=timedelta(hours=23, minutes=30))
    event(5, synced_ago=timedelta(hours=30))  # outside the day
    event(6, synced_ago=timedelta(minutes=1), opp=999, service="ace")  # test work
    ingest.record_success("tail")
    yield
    cache.clear()


@pytest.mark.django_db
class TestTheFigures:
    def test_counts_the_last_24_hours_of_real_work_only(self, pulse):
        data = widget_payload()
        assert data["services_24h"] == 4
        assert data["countries_24h"] == 2

    def test_hourly_bins_are_24_long_and_sum_to_the_count(self, pulse):
        data = widget_payload()
        assert len(data["hourly"]) == 24
        assert sum(data["hourly"]) == data["services_24h"]

    def test_the_newest_bin_is_last(self, pulse):
        """Oldest first, so the sparkline reads left to right as time does."""
        hourly = widget_payload()["hourly"]
        assert hourly[-1] + hourly[-2] >= 1  # the 4-minute-old service is at the end
        assert hourly[0] + hourly[1] >= 1  # the 23.5-hour-old one at the start

    def test_the_latest_service_is_named_the_way_pulse_names_it(self, pulse):
        latest = widget_payload()["latest"]
        assert latest["service"] == "Kangaroo Mother Care"
        assert latest["country"] == "Nigeria"
        assert 200 <= latest["seconds_ago"] <= 300

    def test_test_work_is_never_the_latest(self, pulse):
        """The sandbox synced a minute ago; the widget must not lead with it."""
        assert widget_payload()["latest"]["service"] != "ACE"

    def test_a_quiet_day_says_so_rather_than_failing(self, db, settings, django_user_model):
        cache.clear()
        data = widget_payload()
        assert data["services_24h"] == 0
        assert data["hourly"] == [0] * 24
        assert data["latest"] is None


@pytest.mark.django_db
class TestWhatItMustNotCarry:
    def test_carries_exactly_these_fields(self, client, pulse):
        """An allowlist, not a denylist: a field added later has to be added
        here too, by someone who has read why."""
        data = client.get(reverse("pulse:api_widget")).json()
        assert set(data) == {
            "live",
            "staleness_seconds",
            "services_24h",
            "hourly",
            "countries_24h",
            "latest",
            "poll_seconds",
        }
        assert set(data["latest"]) == {"service", "country", "seconds_ago"}

    def test_no_partner_identity_and_no_coordinates(self, client, pulse):
        body = client.get(reverse("pulse:api_widget")).content.decode()
        assert "partner-workspace" not in body
        for coordinate in ("11.03", "7.63"):
            assert coordinate not in body, coordinate

    def test_no_poller_account_or_ingest_errors(self, client, pulse):
        """Those belong on the Pulse index, which is built to explain them."""
        body = client.get(reverse("pulse:api_widget")).content.decode()
        assert "poller-account" not in body
        assert "poller" not in body


@pytest.mark.django_db
class TestLiveMeansLive:
    def test_healthy_ingest_is_live(self, pulse):
        assert widget_payload()["live"] is True

    def test_a_stale_stream_is_not_live(self, pulse):
        PulseIngestHealth.objects.filter(tier="tail").update(last_success_at=timezone.now() - timedelta(days=2))
        assert widget_payload()["live"] is False

    def test_no_ingest_at_all_is_not_live(self, db, settings, django_user_model):
        cache.clear()
        assert widget_payload()["live"] is False


@pytest.mark.django_db
class TestTheEndpoint:
    def test_serves_json_and_is_cached(self, client, pulse, django_assert_max_num_queries):
        first = client.get(reverse("pulse:api_widget"))
        assert first.status_code == 200
        assert first.json()["services_24h"] == 4
        with django_assert_max_num_queries(2):  # session/auth only; the payload is a cache read
            again = client.get(reverse("pulse:api_widget"))
        assert again.json() == first.json()
