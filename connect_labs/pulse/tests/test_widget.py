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


def event(vid, *, now, synced_ago, opp=765, service="kmc", country="NG", org="partner-workspace"):
    ts = now - synced_ago
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
    """The fixture's own "now", yielded so each test passes the same instant to
    widget_payload: a bin boundary is then a fact of the test, not of the wall
    clock it happened to run at."""
    now = timezone.now()
    cache.clear()
    django_user_model.objects.create(username="poller-account")
    settings.PULSE_POLLER_USERNAME = "poller-account"
    PulseOpportunity.objects.create(opportunity_id=765, name="KMC Nigeria", is_active=True)
    PulseOpportunity.objects.create(opportunity_id=999, name="Sandbox", is_test=True)

    event(1, now=now, synced_ago=timedelta(minutes=4))
    event(2, now=now, synced_ago=timedelta(minutes=40), service="chc", country="UG")
    event(3, now=now, synced_ago=timedelta(hours=5, minutes=10))
    # Inside the day by one minute. Clock-hour bins dropped a service like this
    # one whenever the test ran in the first part of an hour.
    event(4, now=now, synced_ago=timedelta(hours=23, minutes=59))
    event(5, now=now, synced_ago=timedelta(hours=30))  # outside the day
    event(6, now=now, synced_ago=timedelta(minutes=1), opp=999, service="ace")  # test work
    ingest.record_success("tail")
    yield now
    cache.clear()


@pytest.mark.django_db
class TestTheFigures:
    def test_counts_the_last_24_hours_of_real_work_only(self, pulse):
        data = widget_payload(now=pulse)
        assert data["services_24h"] == 4
        assert data["countries_24h"] == 2

    def test_hourly_bins_are_24_long_and_sum_to_the_count(self, pulse):
        data = widget_payload(now=pulse)
        assert len(data["hourly"]) == 24
        assert sum(data["hourly"]) == data["services_24h"]

    def test_bins_are_rolling_hours_ending_now(self, pulse):
        """Oldest first, so the sparkline reads left to right as time does, and
        every bar is a full hour: the last is the last sixty minutes, not the
        minutes since the top of the clock hour."""
        hourly = widget_payload(now=pulse)["hourly"]
        assert hourly[23] == 2  # 4 and 40 minutes ago
        assert hourly[23 - 5] == 1  # five hours and ten minutes ago
        assert hourly[0] == 1  # 23h59m ago: the oldest bar, not dropped
        assert sum(hourly) == 4

    @pytest.mark.parametrize("minute", [0, 1, 29, 30, 59])
    def test_the_total_never_depends_on_the_minute_it_is_asked(self, pulse, minute):
        """The bug this replaced passed or failed with the clock."""
        at = pulse.replace(minute=minute, second=0, microsecond=0) + timedelta(hours=1)
        PulseEvent.objects.all().delete()
        for i, age in enumerate(
            [timedelta(seconds=30), timedelta(hours=12), timedelta(hours=23, minutes=59, seconds=30)]
        ):
            PulseEvent.objects.create(
                connect_visit_id=100 + i,
                opportunity_id=765,
                field_ts=at - age,
                sync_ts=at - age,
                country="NG",
                status="approved",
                service_slug="kmc",
                worker_hash="w",
            )
        data = widget_payload(now=at)
        assert data["services_24h"] == 3
        assert data["hourly"][0] == 1 and data["hourly"][23] == 1

    def test_the_latest_service_is_named_the_way_pulse_names_it(self, pulse):
        latest = widget_payload(now=pulse)["latest"]
        assert latest["service"] == "Kangaroo Mother Care"
        assert latest["country"] == "Nigeria"
        assert 200 <= latest["seconds_ago"] <= 300

    def test_test_work_is_never_the_latest(self, pulse):
        """The sandbox synced a minute ago; the widget must not lead with it."""
        assert widget_payload(now=pulse)["latest"]["service"] != "ACE"

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
        assert widget_payload(now=pulse)["live"] is True

    def test_a_stale_stream_is_not_live(self, pulse):
        PulseIngestHealth.objects.filter(tier="tail").update(last_success_at=timezone.now() - timedelta(days=2))
        assert widget_payload(now=pulse)["live"] is False

    def test_no_ingest_at_all_is_not_live(self, db, settings, django_user_model):
        cache.clear()
        assert widget_payload()["live"] is False


@pytest.mark.django_db
class TestTheEndpoint:
    def test_serves_json_and_is_cached(self, client, pulse, django_assert_max_num_queries):
        """The view reads the real clock, not the fixture's, so this counts
        only what stays inside the day however long the run takes to reach it:
        the 23h59m service is excluded rather than raced."""
        PulseEvent.objects.filter(connect_visit_id=4).delete()
        first = client.get(reverse("pulse:api_widget"))
        assert first.status_code == 200
        assert first.json()["services_24h"] == 3
        with django_assert_max_num_queries(2):  # session/auth only; the payload is a cache read
            again = client.get(reverse("pulse:api_widget"))
        assert again.json() == first.json()
