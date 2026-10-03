"""Worker connectivity: was each visit sent before the next one was started?

The behaviours worth pinning are the ones a latency threshold got wrong or
that are easy to break silently:

* a worker who sends every visit as they finish it is online, however long
  the forms take;
* a worker who sends a day's work in one go is offline, even though every
  visit "arrived within a day";
* pairs that cannot be evidence (back-to-back, overnight, wrong clock, before
  Connect recorded arrival times) are not counted either way;
* the "next visit" is the next one IN SCOPE.
"""

from __future__ import annotations

import datetime as dt
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.pulse import connectivity
from connect_labs.pulse.models import PulseEvent, PulseOpportunity, PulseOrganization, PulseScalar
from connect_labs.pulse.reports import _median_sync_lag

_vid = iter(range(500_000, 10_000_000))


def _day_start(weeks_ago=2):
    """A Wednesday morning, so a day's visits never straddle a week boundary."""
    now = timezone.now() - timedelta(weeks=weeks_ago)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday + timedelta(days=2, hours=8)


def visit(worker, start, lag, *, opp=765, org="lakeside"):
    return PulseEvent.objects.create(
        connect_visit_id=next(_vid),
        opportunity_id=opp,
        org_slug=org,
        field_ts=start,
        sync_ts=start + lag,
        status="approved",
        service_slug="mbw",
        worker_hash=worker,
    )


def online_day(worker, n=6, *, every=timedelta(minutes=30), start=None, **kw):
    """Each visit takes 20 minutes and is sent the moment it is finished."""
    start = start or _day_start()
    for i in range(n):
        visit(worker, start + i * every, timedelta(minutes=21), **kw)


def offline_day(worker, n=6, *, every=timedelta(minutes=30), start=None, **kw):
    """The whole day's work arrives together that evening."""
    start = start or _day_start()
    evening = start + timedelta(hours=10)
    for i in range(n):
        t = start + i * every
        visit(worker, t, evening - t, **kw)


def judge(worker):
    return connectivity.per_worker(connectivity.worker_weeks(PulseEvent.objects.all())).get(worker)


@pytest.mark.django_db
class TestThePairTest:
    def test_sending_each_visit_before_the_next_is_online(self):
        online_day("w-on", n=12)
        w = judge("w-on")
        assert (w["pairs"], w["prompt"]) == (11, 11)
        assert w["class"] == connectivity.ONLINE

    def test_a_days_work_sent_in_one_go_is_offline_though_it_arrived_the_same_day(self):
        offline_day("w-off", n=12)
        w = judge("w-off")
        assert (w["pairs"], w["prompt"]) == (11, 0)
        assert w["class"] == connectivity.OFFLINE

    def test_signal_for_part_of_the_time_is_sometimes(self):
        day = _day_start()
        online_day("w-mix", n=6, start=day)
        offline_day("w-mix", n=6, start=day + timedelta(hours=3, minutes=30))
        w = judge("w-mix")
        assert w["class"] == connectivity.SOMETIMES
        assert 0.2 <= w["share"] < 0.8

    def test_back_to_back_and_overnight_pairs_are_not_evidence(self):
        day = _day_start()
        # Two minutes apart: too close for even an online phone to have sent.
        visit("w-gap", day, timedelta(minutes=30))
        visit("w-gap", day + timedelta(minutes=2), timedelta(minutes=30))
        # Next morning: arriving before it says nothing about "promptly".
        visit("w-gap", day + timedelta(hours=20), timedelta(minutes=5))
        assert judge("w-gap") is None, "no usable pair, so the worker is not judged at all"

    def test_a_visit_that_arrived_before_it_started_is_a_wrong_clock(self):
        day = _day_start()
        for i in range(4):
            visit("w-clock", day + i * timedelta(minutes=30), timedelta(minutes=-40))
        assert judge("w-clock") is None

    def test_visits_before_connect_recorded_arrival_are_ignored(self):
        # Connect back-filled date_created with the visit's own time, so these
        # would all look instantly prompt.
        old = dt.datetime(2024, 11, 6, 8, tzinfo=dt.timezone.utc)
        for i in range(12):
            visit("w-2024", old + i * timedelta(minutes=30), timedelta(0))
        assert judge("w-2024") is None

    def test_too_few_pairs_are_counted_but_not_classified(self):
        online_day("w-thin", n=4)
        w = judge("w-thin")
        assert w["pairs"] == 3 and w["class"] is None

    def test_the_next_visit_is_the_next_one_in_scope(self):
        """Interleave another opportunity's visits: scoped to one, each of its
        visits must be judged against its own successor, not the other's."""
        day = _day_start()
        for i in range(12):
            t = day + i * timedelta(minutes=30)
            visit("w-two", t, timedelta(minutes=21), opp=765)
            # A visit on another opp starts 10 minutes in, before 765's arrives.
            visit("w-two", t + timedelta(minutes=10), timedelta(minutes=21), opp=999)
        mixed = connectivity.per_worker(connectivity.worker_weeks(PulseEvent.objects.all()))["w-two"]
        scoped = connectivity.per_worker(connectivity.worker_weeks(PulseEvent.objects.filter(opportunity_id=765)))[
            "w-two"
        ]
        assert scoped["prompt"] == scoped["pairs"] == 11
        assert mixed["prompt"] < mixed["pairs"]


@pytest.fixture
def estate(db, settings, django_user_model):
    django_user_model.objects.create(username="poller-account")
    settings.PULSE_POLLER_USERNAME = "poller-account"
    PulseScalar.objects.create(key="scope", value={"opportunities": 1, "lifetime_visits": 100})
    PulseOrganization.objects.create(slug="lakeside", name="Lakeside Health")
    PulseOpportunity.objects.create(opportunity_id=765, name="MBW", org_slug="lakeside", is_active=True)
    online_day("aaaa1111on", n=12)
    offline_day("bbbb2222off", n=12)


@pytest.mark.django_db
class TestSurfaces:
    def test_weekly_trend_counts_each_class(self, client, estate):
        trends = client.get(reverse("pulse:api_summary")).json()["trends"]
        week = next(w for w in trends if w["workers"])
        assert (week["workers"], week["online"], week["sometimes"], week["offline"]) == (2, 1, 0, 1)
        assert week["online_rate"] == 0.5
        assert week["connected_rate"] == 0.5

    def test_summary_carries_the_distribution_across_workers(self, client, estate):
        c = client.get(reverse("pulse:api_summary")).json()["connectivity"]
        assert c["workers"] == 2
        assert c["classes"] == {"online": 1, "sometimes": 0, "offline": 1}
        assert c["histogram"][0] == 1 and c["histogram"][-1] == 1
        assert c["method"]["reliable_from"] == "2025-01-15"

    def test_partner_roster_carries_each_workers_share(self, client, estate, django_user_model):
        client.force_login(django_user_model.objects.create(username="viewer"))
        rows = client.get(reverse("pulse:api_partner"), {"org": "lakeside"}).json()["workers"]
        by = {r["worker"]: r for r in rows}
        assert by["aaaa11"]["online_class"] == "online" and by["aaaa11"]["online_share"] == 1.0
        assert by["bbbb22"]["online_class"] == "offline" and by["bbbb22"]["online_share"] == 0.0

    def test_worker_record_carries_its_connectivity(self, client, estate, django_user_model):
        client.force_login(django_user_model.objects.create(username="viewer"))
        c = client.get(reverse("pulse:api_worker"), {"w": "bbbb22", "org": "lakeside"}).json()["connectivity"]
        assert c["class"] == "offline" and c["pairs"] == 11
        assert c["weekly"] and c["weekly"][0]["class"] == "offline"


@pytest.mark.django_db
def test_report_median_lag_ignores_back_filled_arrival_times():
    old = dt.datetime(2024, 11, 6, 8, tzinfo=dt.timezone.utc)
    for i in range(20):
        visit("w-old", old + i * timedelta(minutes=30), timedelta(0))
    online_day("w-new", n=3)
    assert _median_sync_lag(PulseEvent.objects.all()) == pytest.approx(21.0)


def visit_at(worker, start, lag, *, country="NG", lat=None, lon=None):
    return PulseEvent.objects.create(
        connect_visit_id=next(_vid),
        opportunity_id=765,
        org_slug="lakeside",
        field_ts=start,
        sync_ts=start + lag,
        status="approved",
        service_slug="mbw",
        worker_hash=worker,
        country=country,
        lat=lat,
        lon=lon,
    )


@pytest.mark.django_db
class TestBacklogDays:
    """A day when forwarding to Connect backed up makes every phone look offline."""

    def _backlog(self, day):
        # 200 workers whose visits all took five hours to arrive: even the
        # fastest tenth were slow, which no offline worker can cause alone.
        for i in range(connectivity.BACKLOG_MIN_VISITS):
            visit(f"crowd-{i}", day + timedelta(minutes=i), timedelta(hours=5))

    def test_a_slow_day_for_everyone_is_found(self):
        day = _day_start()
        self._backlog(day)
        found = connectivity.backlog_days()
        assert [d["date"] for d in found] == [day.date()]
        assert found[0]["p10_minutes"] > connectivity.BACKLOG_P10_MINUTES

    def test_pairs_on_a_backlog_day_are_not_evidence(self):
        day = _day_start()
        self._backlog(day)
        online_day("w-caught", n=12, start=day)
        assert judge("w-caught") is None

    def test_a_day_that_is_merely_slow_is_not_a_backlog(self):
        """The shape production flagged at the old 30-minute threshold: every
        visit about 35 minutes late. Slow partners, not stalled forwarding."""
        day = _day_start()
        for i in range(connectivity.BACKLOG_MIN_VISITS):
            visit(f"slow-{i}", day + timedelta(minutes=i), timedelta(minutes=35))
        assert connectivity.backlog_days() == []

    def test_a_thin_day_is_never_called_a_backlog(self):
        offline_day("w-alone", n=12)
        assert connectivity.backlog_days() == []


@pytest.mark.django_db
def test_hour_of_day_is_local_to_the_country():
    # 07:00 UTC is 08:00 in Nigeria (UTC+1) and 10:00 in Kenya (UTC+3).
    day = _day_start().replace(hour=7)
    for i in range(3):
        visit_at("w-ng", day + timedelta(minutes=10 * i), timedelta(minutes=5), country="NG")
        visit_at("w-ke", day + timedelta(minutes=10 * i), timedelta(minutes=30), country="KE")
    hours = {r["hour"]: r for r in connectivity.by_hour(PulseEvent.objects.all())}
    assert (hours[8]["pairs"], hours[8]["prompt"]) == (2, 2)
    assert (hours[10]["pairs"], hours[10]["prompt"]) == (2, 0)
    assert hours[3]["share"] is None


@pytest.mark.django_db
def test_map_tiles_nest_and_map_every_worker():
    # Three workers in two ~11 km cells that share the ~90 km tile, and one
    # worker alone far away -- who is mapped too, not withheld.
    for i in range(3):
        online_day(f"kano-{i}", n=12)
    online_day("lone-0", n=12)
    PulseEvent.objects.filter(worker_hash__in=["kano-0", "kano-1"]).update(lat=12.01, lon=8.52)
    PulseEvent.objects.filter(worker_hash="kano-2").update(lat=12.31, lon=8.71)
    PulseEvent.objects.filter(worker_hash="lone-0").update(lat=9.05, lon=7.49)
    events = PulseEvent.objects.all()
    workers = connectivity.merge(
        connectivity.per_worker(connectivity.worker_weeks(events)), connectivity.worker_profiles(events)
    )
    levels = {
        lv["degrees"]: lv["tiles"]
        for lv in connectivity.tile_levels(workers, connectivity.home_cells(events))["levels"]
    }
    assert list(levels) == list(connectivity.TILE_LEVELS)
    for d, ts in levels.items():
        assert sum(t["workers"] for t in ts) == 4, f"every worker is on the map at {d} degrees"
    # 0.8-degree tiles: 12.0-12.8N, 8.0-8.8E holds the three Kano workers.
    kano = next(t for t in levels[0.8] if t["workers"] == 3)
    assert (kano["lat"], kano["lon"], kano["online"]) == (12.0, 8.0, 3)
    assert kano["delayed_3d_rate"] == 0.0
    # At 0.1 degrees they split into the two cells they were placed in.
    assert sorted(t["workers"] for t in levels[0.1]) == [1, 1, 2]


@pytest.mark.django_db
class TestConnectivityPage:
    def test_the_api_refuses_an_anonymous_caller(self, client, estate):
        assert client.get(reverse("pulse:api_connectivity")).status_code == 403

    def test_the_api_carries_every_panel(self, client, estate, django_user_model):
        client.force_login(django_user_model.objects.create(username="viewer"))
        data = client.get(reverse("pulse:api_connectivity")).json()
        assert data["distribution"]["classes"] == {"online": 1, "sometimes": 0, "offline": 1}
        assert data["weekly"] and data["weekly"][0]["workers"] == 2
        assert len(data["hours"]) == 24
        assert [lv["degrees"] for lv in data["map"]["levels"]] == list(connectivity.TILE_LEVELS)
        assert data["backlog_days"] == []
        assert data["method"]["online_share"] == connectivity.ONLINE_SHARE

    def test_the_page_renders_for_a_signed_in_user(self, client, django_user_model):
        client.force_login(django_user_model.objects.create(username="viewer"))
        page = client.get(reverse("pulse:connectivity"))
        assert page.status_code == 200
        assert b"pulse/connectivity.js" in page.content


@pytest.mark.django_db
class TestDelays:
    """How long work waited on the phone. Offline-first makes a delay normal, so
    these report how much waited how long, not whether anything went wrong."""

    def test_a_worker_profile_counts_delays_in_bands(self):
        day = _day_start()
        for i, lag in enumerate(
            (timedelta(minutes=5), timedelta(hours=2), timedelta(days=2), timedelta(days=4), timedelta(days=8))
        ):
            visit("w-held", day + i * timedelta(minutes=30), lag)
        p = connectivity.worker_profiles(PulseEvent.objects.all())["w-held"]
        assert p["visits"] == 5 and p["timed"] == 5
        assert p["seen_online"] == 1, "only the five-minute visit proves signal on its own"
        assert (p["delayed_1d"], p["delayed_3d"], p["delayed_7d"]) == (3, 2, 1)
        assert p["median_delay_minutes"] == pytest.approx(2 * 24 * 60)

    def test_sparse_workers_are_seen_online_though_the_pair_test_cannot_judge_them(self):
        # One visit a day, sent at once: no usable pair, but plainly online.
        day = _day_start()
        for i in range(4):
            visit("w-sparse", day + timedelta(days=i), timedelta(minutes=3))
        workers = connectivity.merge(
            connectivity.per_worker(connectivity.worker_weeks(PulseEvent.objects.all())),
            connectivity.worker_profiles(PulseEvent.objects.all()),
        )
        s = connectivity.summarise(workers.values())
        assert s["judged"] == 0
        assert s["seen_online"] == 1 and s["seen_online_rate"] == 1.0

    def test_breakdown_counts_each_worker_once_under_their_main_partner(self):
        online_day("w-a", n=12, org="lakeside")
        offline_day("w-b", n=12, org="hillside")
        visit("w-a", _day_start() + timedelta(hours=9), timedelta(minutes=5), org="hillside")
        workers = connectivity.merge(
            connectivity.per_worker(connectivity.worker_weeks(PulseEvent.objects.all())),
            connectivity.worker_profiles(PulseEvent.objects.all()),
        )
        rows = {r["key"]: r for r in connectivity.breakdown(workers, "org")}
        assert rows["lakeside"]["workers"] == 1 and rows["lakeside"]["online"] == 1
        assert rows["hillside"]["workers"] == 1 and rows["hillside"]["offline"] == 1
        # The whole day's work waited ~10h for the evening sync: none of it a day.
        assert rows["hillside"]["delayed_1d_rate"] == 0.0


@pytest.mark.django_db
class TestConnectivityFilters:
    def _viewer(self, client, django_user_model):
        client.force_login(django_user_model.objects.create(username="viewer"))
        return client

    def test_the_api_carries_the_tables_and_every_menu(self, client, estate, django_user_model):
        data = self._viewer(client, django_user_model).get(reverse("pulse:api_connectivity")).json()
        assert {r["key"] for r in data["by_org"]} == {"lakeside"}
        assert data["by_org"][0]["name"] == "Lakeside Health"
        assert data["by_opportunity"][0]["name"] == "MBW"
        assert data["summary"]["workers"] == 2
        # The fixture's forms take 20 minutes, so even the online worker never
        # lands inside 15: "seen online" is evidence one way only, by design.
        assert data["summary"]["seen_online"] == 0
        assert {"programs", "orgs", "services", "countries"} <= set(data)

    def test_country_narrows_to_where_the_visits_happened(self, client, estate, django_user_model):
        PulseEvent.objects.filter(worker_hash="bbbb2222off").update(country="KE")
        c = self._viewer(client, django_user_model)
        ke = c.get(reverse("pulse:api_connectivity"), {"country": "KE"}).json()
        assert ke["summary"]["workers"] == 1 and ke["summary"]["offline"] == 1
        assert ke["country"] == "KE"
        everywhere = c.get(reverse("pulse:api_connectivity")).json()
        assert everywhere["summary"]["workers"] == 2

    def test_a_date_window_narrows_every_figure(self, client, estate, django_user_model):
        c = self._viewer(client, django_user_model)
        future = (timezone.now() + timedelta(days=1)).date().isoformat()
        data = c.get(reverse("pulse:api_connectivity"), {"from": future}).json()
        assert data["summary"]["workers"] == 0 and data["by_org"] == []
