"""Read API.

The behaviour most worth protecting: the server, not the page, decides whether
anything may be called LIVE. A display that decides for itself will show a
green badge over data that stopped arriving days ago — the single worst thing
this system could do in front of a funder.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.pulse import ingest
from connect_labs.pulse.models import PulseEvent, PulseIngestHealth, PulseOpportunity, PulseScalar


def make_event(vid, *, field_ts=None, status="approved", usd="0.70", flagged=False, country="NG"):
    now = timezone.now()
    ts = field_ts or now
    return PulseEvent.objects.create(
        connect_visit_id=vid,
        opportunity_id=765,
        field_ts=ts,
        sync_ts=ts + timedelta(minutes=9),
        lat=11.03,
        lon=7.63,
        country=country,
        status=status,
        flagged=flagged,
        flag_type="duration" if flagged else "",
        service_slug="mbw",
        worker_hash="985770f1bf2079f58119",
        usd_to_worker=usd if status == "approved" else None,
    )


@pytest.fixture
def populated(db):
    PulseOpportunity.objects.create(
        opportunity_id=765, name="Mother Baby Wellness (Nigeria)", lifetime_visit_count=120351, is_active=True
    )
    PulseScalar.objects.create(key="scope", value={"opportunities": 494, "lifetime_visits": 1647855, "programs": 108})
    for i in range(1, 11):
        make_event(i, field_ts=timezone.now() - timedelta(hours=i))
    make_event(50, status="rejected", usd=None)
    make_event(51, flagged=True)
    ingest.rebuild_rollups()


@pytest.mark.django_db
class TestSummary:
    def test_returns_scope_and_totals(self, client, populated):
        data = client.get(reverse("pulse:api_summary")).json()
        assert data["scope"]["lifetime_visits"] == 1647855
        assert data["stored"]["events"] == 12
        assert data["by_status"]["approved"] == 11
        assert data["by_status"]["rejected"] == 1

    def test_exposes_labels_so_cards_need_no_hardcoded_copy(self, client, populated):
        labels = client.get(reverse("pulse:api_summary")).json()["labels"]
        assert labels["countries"]["NG"] == "Nigeria"
        assert labels["flags"]["duration"] == "form filled too fast"

    def test_summary_carries_no_pii(self, client, populated):
        body = client.get(reverse("pulse:api_summary")).content.decode()
        assert "entity_name" not in body
        assert "phone" not in body


@pytest.mark.django_db
class TestIngestHonesty:
    def test_live_not_ok_when_ingest_never_ran(self, client, populated):
        data = client.get(reverse("pulse:api_summary")).json()
        assert data["ingest"]["live_ok"] is False
        assert "never" in data["ingest"]["message"].lower()

    def test_live_ok_after_recent_success(self, client, populated):
        ingest.record_success("tail")
        ingest.record_success("cheap")
        assert client.get(reverse("pulse:api_summary")).json()["ingest"]["live_ok"] is True

    def test_stale_ingest_refuses_to_claim_live(self, client, populated):
        """The failure that matters: the poller's refresh token died hours ago
        and nothing arrived since, but the page would still badge itself LIVE."""
        ingest.record_success("tail")
        health = PulseIngestHealth.objects.get(tier="tail")
        health.last_success_at = timezone.now() - timedelta(hours=5)
        health.save()

        state = client.get(reverse("pulse:api_summary")).json()["ingest"]
        assert state["live_ok"] is False
        assert "not live" in state["message"].lower()
        assert state["staleness_seconds"] > 3600

    def test_one_dead_tier_makes_the_whole_display_not_live(self, client, populated):
        ingest.record_success("tail")
        PulseIngestHealth.objects.create(tier="cheap")  # never succeeded
        assert client.get(reverse("pulse:api_summary")).json()["ingest"]["live_ok"] is False

    def test_events_endpoint_also_reports_ingest_state(self, client, populated):
        """Cards that only poll events must be able to tell they're stale too."""
        assert "ingest" in client.get(reverse("pulse:api_events")).json()


@pytest.mark.django_db
class TestEvents:
    def test_returns_positional_rows_with_a_field_map(self, client, populated):
        data = client.get(reverse("pulse:api_events")).json()
        assert data["fields"][0] == "visit_id"
        assert len(data["events"]) == 12
        assert data["cursor"] == 51

    def test_since_cursor_returns_only_newer(self, client, populated):
        data = client.get(reverse("pulse:api_events"), {"since": 10}).json()
        ids = [row[0] for row in data["events"]]
        assert ids == [50, 51]

    def test_events_carry_no_worker_identity(self, client, populated):
        data = client.get(reverse("pulse:api_events")).json()
        worker_idx = data["fields"].index("worker")
        # Truncated hash only — never a name, never the full identifier.
        assert all(len(row[worker_idx] or "") <= 6 for row in data["events"])


@pytest.mark.django_db
class TestReplay:
    def test_window_is_selected_on_field_time(self, client, populated):
        """Selecting on arrival time while pacing on field time makes a window
        span far wider than its label claims — the prototype's 'last 48h'
        actually covered nine days because of the offline-sync tail."""
        data = client.get(reverse("pulse:api_replay"), {"hours": 6}).json()
        assert data["window"]["basis"] == "field_ts"
        assert data["window"]["hours"] == 6

    def test_window_excludes_events_outside_it(self, client, populated):
        make_event(900, field_ts=timezone.now() - timedelta(days=9))
        data = client.get(reverse("pulse:api_replay"), {"hours": 48}).json()
        assert 900 not in [row[0] for row in data["events"]]

    def test_events_are_ordered_by_field_time(self, client, populated):
        data = client.get(reverse("pulse:api_replay"), {"hours": 72}).json()
        ts_idx = data["fields"].index("field_ts")
        stamps = [row[ts_idx] for row in data["events"]]
        assert stamps == sorted(stamps)

    def test_truncation_is_declared(self, client, populated):
        """Silent truncation reads as 'that's all there was'."""
        data = client.get(reverse("pulse:api_replay"), {"hours": 72, "limit": 3}).json()
        assert data["truncated"] is True
        assert len(data["events"]) == 3


@pytest.mark.django_db
class TestPublicAccess:
    def test_unknown_token_is_404(self, client):
        assert client.get(reverse("pulse:public", args=["nope"])).status_code == 404

    def test_revoked_token_is_404_and_indistinguishable_from_unknown(self, client, django_user_model):
        from connect_labs.pulse.views import mint_public_token

        user = django_user_model.objects.create(username="jj")
        token = mint_public_token(user, label="A funder")
        assert client.get(reverse("pulse:public", args=[token.token])).status_code == 200

        token.revoked = True
        token.save()
        revoked = client.get(reverse("pulse:public", args=[token.token]))
        unknown = client.get(reverse("pulse:public", args=["definitely-not-a-token"]))
        assert revoked.status_code == unknown.status_code == 404

    def test_public_page_sets_noindex(self, client, django_user_model):
        from connect_labs.pulse.views import mint_public_token

        user = django_user_model.objects.create(username="jj2")
        token = mint_public_token(user)
        response = client.get(reverse("pulse:public", args=[token.token]))
        assert "noindex" in response["X-Robots-Tag"]

    def test_tokens_are_unguessable(self, django_user_model):
        from connect_labs.pulse.views import mint_public_token

        user = django_user_model.objects.create(username="jj3")
        tokens = {mint_public_token(user).token for _ in range(5)}
        assert len(tokens) == 5
        assert all(len(t) >= 24 for t in tokens)

    def test_authenticated_display_requires_login(self, client):
        response = client.get(reverse("pulse:display", args=["nightmap"]))
        assert response.status_code in (302, 403)
