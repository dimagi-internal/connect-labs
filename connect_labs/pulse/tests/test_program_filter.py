"""Programme filtering.

The rule the whole feature rests on: a filter must move EVERY figure on the
screen. Leaving a server-side total unfiltered above a filtered map is the same
defect class as the inferred poller and the head-sliced replay — a number that
is true about something other than what is being asked.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.pulse.models import PulseEvent, PulseGridCell, PulseOpportunity, PulseProgram, PulseScalar, PulseWork


@pytest.fixture
def two_programmes(db, settings, django_user_model):
    django_user_model.objects.create(username="poller-account")
    settings.PULSE_POLLER_USERNAME = "poller-account"

    PulseProgram.objects.create(program_id=1, name="ECD Nigeria 2025", delivery_type="ecd")
    PulseProgram.objects.create(program_id=2, name="Readers - NG - Program 1", delivery_type="readers")
    PulseProgram.objects.create(program_id=3, name="[TEST 02] Dimagi-GW CHC", delivery_type="chc", is_test=True)

    for pid, opp, n, svc in ((1, 100, 40, "ecd"), (2, 200, 10, "readers"), (3, 300, 7, "chc")):
        PulseOpportunity.objects.create(
            opportunity_id=opp,
            name=f"opp {opp}",
            program_id=pid,
            service_slug=svc,
            is_active=True,
            lifetime_visit_count=n * 10,
        )
        for i in range(n):
            PulseEvent.objects.create(
                connect_visit_id=pid * 1000 + i,
                opportunity_id=opp,
                program_id=pid,
                field_ts=timezone.now() - timedelta(hours=i % 20),
                sync_ts=timezone.now(),
                lat=11.0,
                lon=7.6,
                country="NG",
                status="approved",
                service_slug=svc,
                worker_hash="w" * 20,
                usd_to_worker="0.50",
            )
            PulseWork.objects.create(
                work_key=f"{pid}-{i}",
                opportunity_id=opp,
                program_id=pid,
                status="approved",
                country="NG",
                service_slug=svc,
                created_ts=timezone.now(),
                usd_to_worker="0.50",
                usd_to_org="0.25",
            )
    PulseScalar.objects.create(key="scope", value={"opportunities": 3, "lifetime_visits": 570, "programs": 3})
    PulseGridCell.objects.create(
        lat_q=1100, lon_q=760, n=500, approved_n=500, flagged_n=0, country="NG", service_slug="ecd"
    )
    PulseGridCell.objects.create(
        lat_q=1101, lon_q=761, n=90, approved_n=90, flagged_n=0, country="NG", service_slug="readers"
    )


def summary(client, **params):
    return client.get(reverse("pulse:api_summary"), params).json()


@pytest.mark.django_db
class TestEveryFigureMoves:
    def test_unfiltered_sees_everything(self, client, two_programmes):
        d = summary(client)
        assert d["stored"]["events"] == 57
        assert d["money"]["works"] == 57
        assert d["program"] is None

    def test_filtering_moves_events_money_and_scope_together(self, client, two_programmes):
        d = summary(client, program=1)
        assert d["program"]["name"] == "ECD Nigeria 2025"
        assert d["stored"]["events"] == 40, "map/ticker not filtered"
        assert d["money"]["works"] == 40, "money spine not filtered"
        # The headline scale must not keep describing the whole estate.
        assert d["scope"]["opportunities"] == 1
        assert d["scope"]["lifetime_visits"] == 400
        assert len(d["opportunities"]) == 1

    def test_a_second_programme_gives_its_own_numbers(self, client, two_programmes):
        d = summary(client, program=2)
        assert d["stored"]["events"] == 10
        assert d["money"]["works"] == 10
        assert d["scope"]["lifetime_visits"] == 100

    def test_an_unknown_programme_falls_back_to_everything(self, client, two_programmes):
        """Better to show the whole estate than an empty screen with no cause."""
        assert summary(client, program=9999)["stored"]["events"] == 57
        assert summary(client, program="not-a-number")["stored"]["events"] == 57


@pytest.mark.django_db
class TestMenu:
    def test_offers_real_programmes_by_volume(self, client, two_programmes):
        menu = summary(client)["programs"]
        assert [m["name"] for m in menu] == ["ECD Nigeria 2025", "Readers - NG - Program 1"]

    def test_excludes_test_programmes(self, client, two_programmes):
        """They carry real volume, so they cannot be spotted by size."""
        assert all("TEST" not in m["name"] for m in summary(client)["programs"])

    def test_excludes_programmes_with_no_delivery(self, client, two_programmes):
        PulseProgram.objects.create(program_id=4, name="Never Ran", delivery_type="chc")
        assert all(m["name"] != "Never Ran" for m in summary(client)["programs"])

    def test_carries_a_label_without_inventing_one(self, client, two_programmes):
        by_name = {m["name"]: m for m in summary(client)["programs"]}
        assert by_name["ECD Nigeria 2025"]["service_label"] == "Early childhood development"


@pytest.mark.django_db
class TestOtherEndpoints:
    def test_events_endpoint_is_filtered(self, client, two_programmes):
        d = client.get(reverse("pulse:api_events"), {"program": 2}).json()
        assert {r[5] for r in d["events"]} == {200}

    def test_replay_is_filtered_and_still_samples(self, client, two_programmes):
        d = client.get(reverse("pulse:api_replay"), {"program": 1, "hours": 48, "limit": 10}).json()
        assert d["sampled"] is True
        assert {r[5] for r in d["events"]} == {100}, "another programme leaked into a filtered replay"
        assert d["matched"] == 40

    def test_grid_narrows_by_delivery_type(self, client, two_programmes):
        """Cells predate programme attribution — their source rows are deleted —
        so they filter by delivery type, and the response says so."""
        d = client.get(reverse("pulse:api_grid"), {"program": 1}).json()
        assert d["filtered_by"] == "ecd"
        assert [c[6] for c in d["cells"]] == ["ecd"]

    def test_grid_unfiltered_reports_no_narrowing(self, client, two_programmes):
        assert client.get(reverse("pulse:api_grid")).json()["filtered_by"] is None


@pytest.mark.django_db
class TestEcdLabelling:
    def test_ecd_is_named_not_dumped_in_service_delivery(self, client, two_programmes):
        """163,473 ECD visits rendered as the generic bucket on prod because the
        name regex had no `ecd` pattern."""
        names = {s["name"] for s in summary(client)["money"]["by_service"]}
        assert "Early childhood development" in names
        assert "Service delivery" not in names

    def test_an_unmapped_delivery_type_shows_its_code_not_a_guess(self):
        from connect_labs.pulse.normalize import service_label

        assert service_label("ivp") == "IVP"
        assert service_label("") == "Service delivery"
