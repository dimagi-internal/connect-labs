"""What a no-change poll costs.

Measured on prod before this: ``poll_cheap_tier`` took 17.8s on average and
44.6s at worst, every five minutes, to change ten rows. Pulse as a whole was
using ~23% of a Celery worker continuously.

The waste was not the HTTP fetch. It was that a steady state still did the full
amount of database work:

* ``update_or_create`` writes unconditionally, and every mirrored model carries
  ``updated_at = auto_now``, so all ~690 orgs, programmes and opportunities were
  rewritten every five minutes — ~199,000 row writes a day to change nothing;
* the country and delivery-type backfills issue one UPDATE per opportunity
  against ``PulseWork`` (1M rows) whether or not any row disagrees — ~438,000
  UPDATEs a day, almost all matching zero rows.

These tests pin the properties rather than the timings, because a duration is
not reproducible in CI and a query count is.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from connect_labs.pulse import ingest
from connect_labs.pulse.models import PulseOpportunity, PulseOrganization, PulseProgram

PAYLOAD = {
    "organizations": [
        {"id": 1, "slug": "connect-nigeria", "name": "Connect Nigeria", "funder": "givewell"},
        {"id": 2, "slug": "living-goods", "name": "Living Goods", "funder": ""},
    ],
    "programs": [
        {
            "id": 10,
            "name": "CHC Nigeria",
            "delivery_type": "chc",
            "organization": "connect-nigeria",
            "currency": "NGN",
        },
        {"id": 20, "name": "KMC Uganda", "delivery_type": "kmc", "organization": "living-goods", "currency": "UGX"},
    ],
    "opportunities": [
        {
            "id": 1,
            "name": "CHC NG P1",
            "organization": "connect-nigeria",
            "program": 10,
            "is_active": True,
            "visit_count": 100,
        },
        {
            "id": 2,
            "name": "KMC UG",
            "organization": "living-goods",
            "program": 20,
            "is_active": True,
            "visit_count": 50,
        },
    ],
}


class _Client:
    """Stands in for the export client; records how often it was asked."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0


@pytest.fixture
def patched(monkeypatch):
    client = _Client(PAYLOAD)

    def fake_fetch_json(_client, path):
        client.calls += 1
        return client.payload

    monkeypatch.setattr("connect_labs.pulse.client.fetch_json", fake_fetch_json)
    return client


@pytest.mark.django_db
class TestSteadyStateWritesNothing:
    def test_the_first_sync_creates_everything(self, patched):
        ingest.refresh_opportunities(patched)
        assert PulseOrganization.objects.count() == 2
        assert PulseProgram.objects.count() == 2
        assert PulseOpportunity.objects.count() == 2

    def test_a_second_identical_sync_rewrites_no_rows(self, patched):
        """The property that matters: unchanged upstream data must not produce
        writes. `updated_at` is auto_now, so a rewrite is observable."""
        ingest.refresh_opportunities(patched)
        stamps = {o.opportunity_id: o.updated_at for o in PulseOpportunity.objects.all()}
        org_stamps = {o.slug: o.updated_at for o in PulseOrganization.objects.all()}

        ingest.refresh_opportunities(patched)

        assert {o.opportunity_id: o.updated_at for o in PulseOpportunity.objects.all()} == stamps
        assert {o.slug: o.updated_at for o in PulseOrganization.objects.all()} == org_stamps

    def test_a_second_identical_sync_issues_no_write_queries(self, patched):
        ingest.refresh_opportunities(patched)
        with CaptureQueriesContext(connection) as ctx:
            ingest.refresh_opportunities(patched)
        writes = [q for q in ctx.captured_queries if q["sql"].lstrip().upper().startswith(("UPDATE", "INSERT"))]
        assert writes == [], f"a no-change sync wrote {len(writes)} times"

    def test_the_query_count_does_not_scale_with_the_estate(self, patched):
        """Previously this was one SELECT + one write per row. On prod that was
        ~1,380 queries per run for 690 rows; a bulk compare is a fixed handful
        regardless of size."""
        ingest.refresh_opportunities(patched)
        with CaptureQueriesContext(connection) as ctx:
            ingest.refresh_opportunities(patched)
        assert len(ctx.captured_queries) <= 6, [q["sql"][:80] for q in ctx.captured_queries]

    def test_a_real_change_is_still_written(self, patched):
        """Cheapness must not cost correctness."""
        ingest.refresh_opportunities(patched)
        patched.payload["opportunities"][0]["visit_count"] = 999
        ingest.refresh_opportunities(patched)
        assert PulseOpportunity.objects.get(opportunity_id=1).lifetime_visit_count == 999

    def test_a_new_row_is_still_created(self, patched):
        ingest.refresh_opportunities(patched)
        patched.payload["opportunities"].append(
            {
                "id": 3,
                "name": "New opp",
                "organization": "living-goods",
                "program": 20,
                "is_active": True,
                "visit_count": 7,
            }
        )
        ingest.refresh_opportunities(patched)
        assert PulseOpportunity.objects.filter(opportunity_id=3).exists()


class TestTheSlowSweepsAreNotOnTheFastPath:
    """Their inputs move in hours; each costs a query per opportunity against a
    million-row table whether or not anything differs."""

    def _tasks(self):
        from pathlib import Path

        from django.conf import settings

        return (Path(settings.APPS_DIR) / "pulse" / "tasks.py").read_text()

    def test_the_cheap_tier_no_longer_runs_them(self):
        import re

        src = self._tasks()
        body = re.search(r"def poll_cheap_tier\(.*?\n(?=@celery_app|\ndef )", src, re.DOTALL).group(0)
        for sweep in ("refresh_opportunity_countries", "resync_service_slugs", "refresh_rate"):
            assert sweep not in body, f"{sweep} is back on the five-minute path"

    def test_they_have_their_own_task(self):
        src = self._tasks()
        assert "def poll_slow_maintenance" in src
        for sweep in ("refresh_opportunity_countries", "resync_service_slugs", "refresh_rate"):
            assert sweep in src

    def test_that_task_is_scheduled_hourly_not_every_few_minutes(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["pulse-slow-maintenance"]
        assert entry["task"].endswith("poll_slow_maintenance")
        # crontab(minute=7) -> once an hour. A */n minute spec would not have a
        # single-valued minute set.
        assert len(entry["schedule"].minute) == 1, "expected an hourly cadence"

    def test_the_live_tails_are_untouched(self):
        """The freshness of the display comes from these, so the cadence that
        matters must not have been slowed to buy back worker time."""
        from django.conf import settings

        assert settings.CELERY_BEAT_SCHEDULE["pulse-visit-tail"]["schedule"].minute == set(range(60))


class TestTheSlowUpstreamCallIsNotOnAFastCadence:
    """The cheap tier's cost is one request, not its local work.

    Measured on prod after the write-elimination above: the task still took
    ~16s, and with the sweeps gone that is essentially all a single call to
    ``opp_org_program_list`` — which makes Connect aggregate a visit_count for
    every one of 507 opportunities across 1.65M visits. The tails' calls are
    0.2-0.5s each by comparison.

    So the lever is cadence, not efficiency: at every five minutes it was 77
    minutes a day of worker time waiting on a list that changes on the order of
    days.
    """

    def test_the_cheap_tier_is_not_on_a_five_minute_cadence(self):
        from django.conf import settings

        minutes = settings.CELERY_BEAT_SCHEDULE["pulse-cheap-tier"]["schedule"].minute
        assert len(minutes) <= 4, (
            f"cheap tier runs {len(minutes)} times an hour. Each run is ~16s of waiting on one "
            "slow upstream aggregate; the data behind it changes on the order of days."
        )

    def test_the_tails_that_carry_freshness_are_still_fast(self):
        """The trade only works because nothing a viewer watches depends on the
        cheap tier's cadence."""
        from django.conf import settings

        sched = settings.CELERY_BEAT_SCHEDULE
        assert sched["pulse-visit-tail"]["schedule"].minute == set(range(60))
        assert len(sched["pulse-works"]["schedule"].minute) >= 30
