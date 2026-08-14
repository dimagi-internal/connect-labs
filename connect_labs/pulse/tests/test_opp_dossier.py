"""The opportunity dossier — one engagement's full record on one page.

The behaviours worth protecting: the dossier names the partner and itemises
money, so nothing about it may be reachable without a login; and its figures
must reconcile with the event and works rows they summarise, because this page
is what a funder will quote.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.pulse.models import PulseEvent, PulseOpportunity, PulseWork


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="staff", password="pw")


@pytest.fixture
def opp(db):
    return PulseOpportunity.objects.create(
        opportunity_id=822,
        name="Child Health Campaign - NG - P2",
        org_slug="eha-clinics",
        country="NG",
        service_slug="mbw",
        is_active=True,
        lifetime_visit_count=40,
    )


@pytest.fixture
def populated(opp):
    now = timezone.now()
    for i in range(6):
        PulseEvent.objects.create(
            connect_visit_id=1000 + i,
            opportunity_id=822,
            field_ts=now - timedelta(weeks=i),
            sync_ts=now - timedelta(weeks=i),
            lat=11.03,
            lon=7.63,
            country="NG",
            status="approved" if i < 4 else "rejected",
            flagged=(i == 5),
            flag_type="duration" if i == 5 else "",
            service_slug="mbw",
            worker_hash=f"worker{i % 2}hash",
        )
    # An event on another opportunity, which must never leak into the dossier.
    PulseEvent.objects.create(
        connect_visit_id=2000,
        opportunity_id=999,
        field_ts=now,
        sync_ts=now,
        status="approved",
        service_slug="mbw",
        worker_hash="otherworker",
    )
    PulseWork.objects.create(
        work_key="w1",
        opportunity_id=822,
        worker_hash="worker0hash",
        status="approved",
        created_ts=now,
        approved_count=4,
        usd_to_worker="2.00",
        usd_to_org="1.00",
    )


@pytest.mark.django_db
class TestOppApi:
    def url(self):
        return reverse("pulse:api_opp")

    def test_requires_a_login(self, client, populated):
        assert client.get(self.url(), {"id": 822}).status_code == 403

    def test_unknown_opportunity_is_a_404(self, client, user, populated):
        client.force_login(user)
        assert client.get(self.url(), {"id": 31337}).status_code == 404

    def test_totals_reconcile_with_the_rows(self, client, user, populated):
        client.force_login(user)
        d = client.get(self.url(), {"id": 822}).json()

        assert d["opp"]["name"] == "Child Health Campaign - NG - P2"
        assert d["totals"]["events"] == 6, "the other opportunity's event leaked in"
        assert d["totals"]["flagged"] == 1
        assert d["totals"]["workers"] == 2
        assert d["statuses"] == {"approved": 4, "rejected": 2}
        assert d["money"]["usd_workers"] == 2.0
        assert d["money"]["usd_org"] == 1.0
        assert d["money"]["usd_total"] == 3.0

    def test_weekly_series_spans_the_history(self, client, user, populated):
        client.force_login(user)
        d = client.get(self.url(), {"id": 822}).json()
        weekly = d["weekly"]
        assert sum(w["n"] for w in weekly) == 6
        assert sum(w["approved"] for w in weekly) == 4
        assert [w["t"] for w in weekly] == sorted(w["t"] for w in weekly)

    def test_points_are_town_scale(self, client, user, populated):
        client.force_login(user)
        d = client.get(self.url(), {"id": 822}).json()
        assert d["points"], "mappable events should produce footprint points"
        for lat, lon, n in d["points"]:
            # Two decimal places ~ 1.1 km: footprint, never a household.
            assert round(lat, 2) == lat
            assert round(lon, 2) == lon
            assert n >= 1

    def test_workers_are_prefix_only(self, client, user, populated):
        client.force_login(user)
        d = client.get(self.url(), {"id": 822}).json()
        for w in d["workers"]:
            assert len(w["w"]) <= 6, "the dossier must not expose full worker hashes"


@pytest.mark.django_db
class TestOppPage:
    def test_requires_a_login(self, client, opp):
        res = client.get(reverse("pulse:opp", args=[822]))
        assert res.status_code == 302 and "login" in res["Location"].lower()

    def test_renders_the_identity_server_side(self, client, user, opp):
        client.force_login(user)
        res = client.get(reverse("pulse:opp", args=[822]))
        assert res.status_code == 200
        assert b"Child Health Campaign" in res.content

    def test_unknown_opportunity_is_a_404(self, client, user, db):
        client.force_login(user)
        assert client.get(reverse("pulse:opp", args=[31337])).status_code == 404
