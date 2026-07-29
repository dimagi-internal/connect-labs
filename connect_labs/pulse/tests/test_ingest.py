"""Ingest behaviour, exercised against a fake export client (no network).

The properties worth protecting here are the ones that would silently corrupt
what a funder sees: a cursor that goes backwards re-reads history forever, an
overlapping poll that double-counts inflates the headline number, and a dead
token that doesn't surface makes the screen lie.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from connect_labs.pulse import ingest
from connect_labs.pulse.models import (
    TIER_COLD,
    TIER_DORMANT,
    TIER_HOT,
    TIER_WARM,
    PulseCursor,
    PulseEvent,
    PulseIngestHealth,
    PulseOpportunity,
    PulseScalar,
)


def visit(vid: int, *, status="approved", location="11.03 7.63 0 0", synced=None, flagged="False"):
    ts = (synced or timezone.now()).isoformat().replace("+00:00", "Z")
    return {
        "id": vid,
        "opportunity_id": 765,
        "username": "985770f1bf2079f58119",
        "entity_name": "Real Person - 8037760312",
        "visit_date": ts,
        "date_created": ts,
        "status": status,
        "location": location,
        "flagged": flagged,
        "flag_reason": "None",
        "review_status": "agree",
        "form_json": {"big": "payload"},
    }


class FakeClient:
    """Stands in for ExportAPIClient, honouring last_id keyset semantics."""

    def __init__(self, rows=None, json_payload=None):
        self.rows = rows or []
        self.json_payload = json_payload or {}
        self.calls = []

    def paginate(self, endpoint, params=None, *, partial_ok=False):
        params = params or {}
        self.calls.append((endpoint, dict(params), partial_ok))
        rows = sorted(self.rows, key=lambda r: r["id"])
        last_id = params.get("last_id")
        if params.get("cursor_order") == "reverse":
            rows = list(reversed(rows))
            if last_id:
                rows = [r for r in rows if r["id"] < last_id]
        elif last_id:
            rows = [r for r in rows if r["id"] > last_id]
        size = params.get("page_size", 1000)
        for i in range(0, len(rows), size):
            yield rows[i : i + size]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def opp(db):
    return PulseOpportunity.objects.create(
        opportunity_id=765, name="Mother Baby Wellness (Nigeria)", usd_per_service="0.70", is_active=True
    )


@pytest.fixture
def cursor(db):
    """An already-positioned cursor — the steady state.

    ``last_polled_at`` set means "this cursor has been seeded", so tail_visits
    tails rather than bootstrapping. Use ``fresh_cursor`` to exercise the
    first-run seeding path.
    """
    return PulseCursor.objects.create(
        opportunity_id=765,
        endpoint=ingest.VISITS_ENDPOINT,
        last_id=0,
        last_polled_at=timezone.now() - timedelta(days=1),
    )


@pytest.fixture
def fresh_cursor(db):
    """Never polled — the first-run case."""
    return PulseCursor.objects.create(opportunity_id=765, endpoint=ingest.VISITS_ENDPOINT)


@pytest.mark.django_db
class TestTailVisits:
    def test_stores_new_events_and_advances_cursor(self, opp, cursor):
        client = FakeClient([visit(1), visit(2), visit(3)])
        result = ingest.tail_visits(client, cursor)

        assert result["stored"] == 3
        assert PulseEvent.objects.count() == 3
        cursor.refresh_from_db()
        assert cursor.last_id == 3

    def test_second_poll_requests_only_new_rows(self, opp, cursor):
        """The whole reason this is affordable: last_id makes the endpoint a
        change feed rather than a full re-read."""
        client = FakeClient([visit(1), visit(2)])
        ingest.tail_visits(client, cursor)
        cursor.refresh_from_db()

        client.rows.append(visit(3))
        client.calls.clear()
        result = ingest.tail_visits(client, cursor)

        assert client.calls[0][1]["last_id"] == 2
        assert result["stored"] == 1
        assert PulseEvent.objects.count() == 3

    def test_overlapping_poll_does_not_double_count(self, opp, cursor):
        """A cursor is re-read after a failure, so re-seeing a visit is normal
        and must be a no-op. Double-counting would inflate the headline."""
        client = FakeClient([visit(1), visit(2)])
        ingest.tail_visits(client, cursor)

        cursor.last_id = 0  # simulate a cursor rewind after a crash
        cursor.save()
        ingest.tail_visits(client, cursor)

        assert PulseEvent.objects.count() == 2

    def test_declares_partial_ok_when_it_may_stop_early(self, opp, cursor):
        """Ingest caps rows per poll. Without partial_ok=True the audit trail
        records every deliberate early stop as a failed export (#1025)."""
        client = FakeClient([visit(i) for i in range(1, 20)])
        ingest.tail_visits(client, cursor, max_rows=5)
        assert client.calls[0][2] is True

    def test_cursor_never_goes_backwards(self, opp, cursor):
        cursor.last_id = 100
        cursor.save()
        ingest.tail_visits(FakeClient([]), cursor)
        cursor.refresh_from_db()
        assert cursor.last_id == 100

    def test_off_map_points_are_counted_not_silently_dropped(self, opp, cursor):
        """A rising count here means either bad GPS or a new country the box
        list doesn't know about. Either way it must be visible."""
        ingest.tail_visits(FakeClient([visit(1, location="-57.0 -110.02 0 0")]), cursor)

        assert PulseEvent.objects.count() == 1  # service still counted
        assert PulseEvent.objects.first().lat is None  # but not plotted
        assert PulseScalar.objects.get(key=ingest.SCALAR_OFF_MAP).value["count"] == 1

    def test_strips_pii_end_to_end(self, opp, cursor):
        ingest.tail_visits(FakeClient([visit(1)]), cursor)
        event = PulseEvent.objects.first()
        blob = " ".join(str(getattr(event, f.name)) for f in PulseEvent._meta.concrete_fields)
        assert "Real Person" not in blob
        assert "8037760312" not in blob

    def test_only_approved_work_carries_money(self, opp, cursor):
        ingest.tail_visits(FakeClient([visit(1, status="approved"), visit(2, status="rejected")]), cursor)
        assert PulseEvent.objects.get(connect_visit_id=1).usd_to_worker is not None
        assert PulseEvent.objects.get(connect_visit_id=2).usd_to_worker is None


@pytest.mark.django_db
class TestTiering:
    @pytest.mark.parametrize(
        "age,expected",
        [
            (timedelta(minutes=30), TIER_HOT),
            (timedelta(days=2), TIER_WARM),
            (timedelta(days=30), TIER_COLD),
            (timedelta(days=200), TIER_DORMANT),
        ],
    )
    def test_tier_follows_recency_of_work(self, age, expected):
        assert ingest.tier_for(timezone.now() - age) == expected

    def test_never_polled_is_dormant(self):
        assert ingest.tier_for(None) == TIER_DORMANT

    def test_tail_retiers_a_waking_opportunity(self, opp, cursor):
        """An opp that starts producing work must escalate to hot on its own,
        or a newly-launched programme would poll once a week forever."""
        cursor.tier = TIER_DORMANT
        cursor.save()
        ingest.tail_visits(FakeClient([visit(1)]), cursor)
        cursor.refresh_from_db()
        assert cursor.tier == TIER_HOT

    def test_due_cursors_respects_the_interval(self, opp, cursor):
        cursor.tier = TIER_COLD
        cursor.last_polled_at = timezone.now()
        cursor.save()
        assert ingest.due_cursors() == []

        cursor.last_polled_at = timezone.now() - timedelta(days=2)
        cursor.save()
        assert len(ingest.due_cursors()) == 1


@pytest.mark.django_db
class TestCheapTier:
    def test_refresh_opportunities_populates_scope_and_opps(self):
        client = FakeClient(
            json_payload={
                "organizations": [{"id": 1, "slug": "a", "name": "A"}],
                "programs": [{"id": 9, "organization": "a"}],
                "opportunities": [
                    {
                        "id": 765,
                        "name": "Mother Baby Wellness (Nigeria)",
                        "program": 9,
                        "is_active": True,
                        "visit_count": 120351,
                        "organization": "a",
                    },
                    {
                        "id": 1996,
                        "name": "Readers - NG - EHA",
                        "program": 9,
                        "is_active": False,
                        "visit_count": 11644,
                        "organization": "a",
                    },
                ],
            }
        )
        import connect_labs.pulse.client as pulse_client

        original = pulse_client.fetch_json
        pulse_client.fetch_json = lambda c, path: c.json_payload
        try:
            scope = ingest.refresh_opportunities(client)
        finally:
            pulse_client.fetch_json = original

        assert scope["opportunities"] == 2
        assert scope["lifetime_visits"] == 131995
        assert PulseOpportunity.objects.get(opportunity_id=765).service_slug == "mbw"
        assert PulseScalar.objects.get(key=ingest.SCALAR_SCOPE).value["lifetime_visits"] == 131995

    def test_refresh_rate_measures_usd_per_approved_work(self, opp):
        client = FakeClient(
            [
                {"id": 1, "status": "approved", "saved_payment_accrued_usd": "0.70"},
                {"id": 2, "status": "approved", "saved_payment_accrued_usd": "0.80"},
                {"id": 3, "status": "rejected", "saved_payment_accrued_usd": "9.99"},
            ]
        )
        rate = ingest.refresh_rate(client, opp)
        assert float(rate) == pytest.approx(0.75)  # rejected work excluded

    def test_ensure_cursors_covers_every_opportunity_on_both_streams(self, opp):
        """Each opportunity needs a cursor per stream — visits and works are
        tailed independently, at different cadences and costs."""
        PulseOpportunity.objects.create(opportunity_id=999, name="Other")
        assert ingest.ensure_cursors() == 4  # 2 opps x 2 streams
        assert PulseCursor.objects.filter(endpoint=ingest.VISITS_ENDPOINT).count() == 2
        assert PulseCursor.objects.filter(endpoint=ingest.WORKS_ENDPOINT).count() == 2
        assert ingest.ensure_cursors() == 0  # idempotent


@pytest.mark.django_db
class TestHealth:
    def test_unhealthy_until_first_success(self):
        health = PulseIngestHealth.objects.create(tier="tail")
        assert health.is_healthy is False

    def test_healthy_after_recent_success(self):
        ingest.record_success("tail")
        assert PulseIngestHealth.objects.get(tier="tail").is_healthy is True

    def test_stale_success_is_unhealthy(self):
        """The failure that matters: ingest stopped hours ago but the screen
        still claims LIVE."""
        ingest.record_success("tail")
        health = PulseIngestHealth.objects.get(tier="tail")
        health.last_success_at = timezone.now() - timedelta(hours=3)
        health.save()
        assert health.is_healthy is False

    def test_repeated_failures_are_unhealthy_even_if_recent(self):
        ingest.record_success("tail")
        for _ in range(5):
            ingest.record_failure("tail", "boom")
        assert PulseIngestHealth.objects.get(tier="tail").is_healthy is False

    def test_failure_records_the_reason(self):
        ingest.record_failure("cheap", "auth: token expired")
        health = PulseIngestHealth.objects.get(tier="cheap")
        assert "token expired" in health.last_error
        assert health.consecutive_failures == 1


@pytest.mark.django_db
class TestRollups:
    def test_rollups_reconcile_with_raw_events(self, opp, cursor):
        ingest.tail_visits(
            FakeClient([visit(1), visit(2), visit(3, status="rejected"), visit(4, flagged="True")]), cursor
        )
        ingest.rebuild_rollups()

        from django.db.models import Sum

        from connect_labs.pulse.models import PulseRollup

        assert PulseRollup.objects.aggregate(n=Sum("n"))["n"] == PulseEvent.objects.count()
        assert PulseRollup.objects.aggregate(f=Sum("flagged_n"))["f"] == 1

    def test_rebuild_is_idempotent(self, opp, cursor):
        ingest.tail_visits(FakeClient([visit(1), visit(2)]), cursor)
        ingest.rebuild_rollups()
        ingest.rebuild_rollups()

        from django.db.models import Sum

        from connect_labs.pulse.models import PulseRollup

        assert PulseRollup.objects.aggregate(n=Sum("n"))["n"] == 2


@pytest.mark.django_db
class TestCursorSeeding:
    """A fresh cursor must start at the present, not at the beginning of time.

    Otherwise the live tail — which is capped and cadenced for small deltas —
    becomes the path that drags full history, at 16KB/row on the wire.
    """

    def test_fresh_cursor_seeds_to_newest_and_stores_nothing(self, opp, fresh_cursor):
        client = FakeClient([visit(i) for i in range(1, 500)])
        result = ingest.tail_visits(client, fresh_cursor)

        assert result.get("seeded") is True
        assert result["stored"] == 0  # history is backfill's job, not the tail's
        fresh_cursor.refresh_from_db()
        assert fresh_cursor.last_id == 499

    def test_seeding_requests_only_one_row(self, opp, fresh_cursor):
        client = FakeClient([visit(i) for i in range(1, 500)])
        ingest.tail_visits(client, fresh_cursor)
        endpoint, params, partial_ok = client.calls[0]
        assert params["cursor_order"] == "reverse"
        assert params["page_size"] == 1
        assert partial_ok is True

    def test_next_poll_after_seeding_tails_normally(self, opp, fresh_cursor):
        client = FakeClient([visit(i) for i in range(1, 10)])
        ingest.tail_visits(client, fresh_cursor)
        fresh_cursor.refresh_from_db()

        client.rows.append(visit(10))
        result = ingest.tail_visits(client, fresh_cursor)
        assert result["stored"] == 1
        assert PulseEvent.objects.count() == 1

    def test_opportunity_with_no_visits_is_marked_dormant(self, opp, fresh_cursor):
        ingest.tail_visits(FakeClient([]), fresh_cursor)
        fresh_cursor.refresh_from_db()
        assert fresh_cursor.tier == TIER_DORMANT
        assert fresh_cursor.last_id is None

    def test_seeded_cursor_is_not_reseeded(self, opp, fresh_cursor):
        """Seeding is a one-time bootstrap; re-seeding would skip real events."""
        client = FakeClient([visit(1), visit(2)])
        ingest.tail_visits(client, fresh_cursor)
        fresh_cursor.refresh_from_db()
        client.calls.clear()

        client.rows.append(visit(3))
        ingest.tail_visits(client, fresh_cursor)
        assert client.calls[0][1]["cursor_order"] == "forward"


@pytest.mark.django_db
class TestDueness:
    """Regression: a never-polled cursor must be due immediately.

    due_at once returned timezone.now() for unpolled cursors, which is always
    microseconds later than the `now` the caller already captured — so nothing
    was ever due and ingest silently polled nothing while looking healthy.
    """

    def test_never_polled_cursor_is_due(self, fresh_cursor):
        assert fresh_cursor.is_due() is True

    def test_never_polled_cursor_is_due_against_a_pre_captured_now(self, fresh_cursor):
        now = timezone.now()
        assert fresh_cursor.is_due(now) is True

    def test_due_cursors_includes_never_polled(self, fresh_cursor):
        assert len(ingest.due_cursors()) == 1

    def test_recently_polled_hot_cursor_is_not_due(self, db):
        c = PulseCursor.objects.create(
            opportunity_id=99,
            endpoint=ingest.VISITS_ENDPOINT,
            tier=TIER_HOT,
            last_polled_at=timezone.now(),
        )
        assert c.is_due() is False


@pytest.mark.django_db
class TestPollerUserResolution:
    """Who Pulse polls as decides every number on the screen, so this resolves
    explicitly or complains — it never silently ingests nothing."""

    def test_uses_the_configured_user(self, settings, django_user_model):
        django_user_model.objects.create(username="jonathan")
        settings.PULSE_POLLER_USERNAME = "jonathan"
        from connect_labs.pulse.client import get_poller_user

        assert get_poller_user().username == "jonathan"

    def test_configured_but_missing_user_raises(self, settings):
        settings.PULSE_POLLER_USERNAME = "nobody"
        from connect_labs.pulse.client import PulseAuthError, get_poller_user

        with pytest.raises(PulseAuthError, match="does not exist"):
            get_poller_user()

    def test_falls_back_to_a_stored_token_when_unset(self, settings, django_user_model):
        """An unset env var must not mean 'ingest nothing forever' — that shows
        up as an empty screen with no visible cause."""
        from django.utils import timezone as tz

        from connect_labs.labs.models import UserConnectToken
        from connect_labs.pulse.client import get_poller_user

        user = django_user_model.objects.create(username="fallback-user")
        UserConnectToken.objects.create(
            user=user, access_token="x", refresh_token="y", expires_at=tz.now() + timedelta(hours=1)
        )
        settings.PULSE_POLLER_USERNAME = ""
        assert get_poller_user().username == "fallback-user"

    def test_raises_when_unset_and_no_token_exists(self, settings):
        settings.PULSE_POLLER_USERNAME = ""
        from connect_labs.pulse.client import PulseAuthError, get_poller_user

        with pytest.raises(PulseAuthError, match="no user has a stored Connect token"):
            get_poller_user()


@pytest.mark.django_db
class TestPollerOverride:
    """The poller identity is settable from the DB.

    Scope — every headline figure — follows this user's org membership, and on
    a deployed environment the env var lives in an ECS task definition. Being
    able to correct it without AWS access is the difference between a display
    that understates the estate for a week and one that is fixed in a minute.
    """

    def test_db_override_wins_over_settings(self, settings, django_user_model):
        from connect_labs.pulse.client import SCALAR_POLLER, get_poller_user

        django_user_model.objects.create(username="from-settings")
        django_user_model.objects.create(username="from-db")
        settings.PULSE_POLLER_USERNAME = "from-settings"
        PulseScalar.objects.create(key=SCALAR_POLLER, value={"username": "from-db"})

        assert get_poller_user().username == "from-db"

    def test_clearing_the_override_falls_back_to_settings(self, settings, django_user_model):
        from connect_labs.pulse.client import SCALAR_POLLER, get_poller_user

        django_user_model.objects.create(username="from-settings")
        settings.PULSE_POLLER_USERNAME = "from-settings"
        PulseScalar.objects.filter(key=SCALAR_POLLER).delete()

        assert get_poller_user().username == "from-settings"

    def test_empty_override_is_ignored(self, settings, django_user_model):
        """A blank value must not resolve to a user named '' or crash."""
        from connect_labs.pulse.client import SCALAR_POLLER, get_poller_user

        django_user_model.objects.create(username="from-settings")
        settings.PULSE_POLLER_USERNAME = "from-settings"
        PulseScalar.objects.create(key=SCALAR_POLLER, value={"username": ""})

        assert get_poller_user().username == "from-settings"
