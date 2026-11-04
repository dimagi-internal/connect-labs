"""Confirming a handout through an update link: the supervisor who gave it, and the person who got it.

THIS REPOSITORY IS PUBLIC. Every organisation, place and figure here is invented.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import DistributionLine, Movement
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10508
OPPORTUNITY = 4244


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def world(da):
    op(da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton"})
    partner = op(da, "org_upsert", data={"slug": "cbi", "name": "CBI"})

    def place(slug, name, kind):
        return op(
            da,
            "supply_point_upsert",
            data={
                "slug": slug,
                "name": name,
                "kind": kind,
                "source": "we_recorded",
                "managed_by_org_id": partner["id"],
            },
        )

    store = place("cbi-warehouse", "CBI warehouse", "central_store")
    wuse = place("wuse", "Wuse PHC", "facility")
    run = op(
        da,
        "distribution_plan",
        data={
            "supply_point_id": store["id"],
            "opportunity_id": OPPORTUNITY,
            "commodity_slug": "rutf",
            "distributed_on": "2026-11-09",
            "reference": "Monday handout",
            "source": "we_recorded",
            "lines": [{"to_supply_point_id": wuse["id"], "quantity": "12", "quantity_unit": "carton"}],
        },
    )
    return {"partner": partner, "store": store, "wuse": wuse, "run": run}


def link_for(da, world, *points):
    return op(
        da,
        "update_link_issue",
        data={
            "org_id": world["partner"]["id"],
            "supply_point_ids": [p["id"] for p in points],
            "label": "CBI supervisor",
        },
    )


def url(issued):
    return reverse("supply_chain:update_link_public", args=[issued["token"]])


class TestConfirmingWhatWasHandedOut:
    def test_the_supervisor_confirms_a_planned_place_and_stock_moves_as_their_word(self, client, da, world):
        issued = link_for(da, world, world["store"])
        assert "Confirm what you handed out" in client.get(url(issued)).content.decode()
        line = DistributionLine.objects.get()
        client.post(
            url(issued),
            {
                "action": "confirm_handout",
                "confirm_handout-line": line.pk,
                "confirm_handout-quantity_given": "10",
                "confirm_handout-handed_out_on": "2026-11-09",
            },
        )
        line.refresh_from_db()
        assert line.quantity_given == Decimal("10")
        assert line.movement.quantity == Decimal("10"), "the ledger posts what was given, not what was planned"
        assert line.movement.source == "partner_reported"
        assert line.movement.recorded_by_org_id is not None

    def test_a_link_that_does_not_cover_the_handout_cannot_confirm_it(self, client, da, world):
        other = op(
            da,
            "supply_point_upsert",
            data={"slug": "elsewhere", "name": "Elsewhere", "kind": "facility", "source": "we_recorded"},
        )
        issued = link_for(da, world, other)
        body = client.get(url(issued)).content.decode()
        assert "Confirm what you handed out" not in body
        line = DistributionLine.objects.get()
        client.post(
            url(issued),
            {
                "action": "confirm_handout",
                "confirm_handout-line": line.pk,
                "confirm_handout-quantity_given": "12",
                "confirm_handout-handed_out_on": "2026-11-09",
            },
        )
        line.refresh_from_db()
        assert line.quantity_given is None and not Movement.objects.filter(distribution_id=world["run"]["id"]).exists()

    def test_a_page_with_no_handouts_does_not_mention_them(self, client, da):
        op(da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton"})
        partner = op(da, "org_upsert", data={"slug": "cbi", "name": "CBI"})
        point = op(
            da, "supply_point_upsert", data={"slug": "p", "name": "P", "kind": "facility", "source": "we_recorded"}
        )
        issued = op(
            da, "update_link_issue", data={"org_id": partner["id"], "supply_point_ids": [point["id"]], "label": "x"}
        )
        body = client.get(url(issued)).content.decode()
        assert "handed out" not in body and "Confirm received" not in body


class TestTheReceiverConfirms:
    def given(self, da, world):
        op(
            da,
            "distribution_confirm",
            distribution_id=world["run"]["id"],
            data={
                "lines": [{"line_id": world["run"]["lines"][0]["id"], "quantity_given": "12"}],
                "source": "we_recorded",
            },
        )

    def test_the_place_that_got_it_says_how_much_arrived(self, client, da, world):
        self.given(da, world)
        issued = link_for(da, world, world["wuse"])
        assert "Confirm what you received" in client.get(url(issued)).content.decode()
        line = DistributionLine.objects.get()
        client.post(
            url(issued),
            {
                "action": "confirm_received",
                "confirm_received-line": line.pk,
                "confirm_received-quantity_received": "9",
                "confirm_received-received_on": "2026-11-10",
            },
        )
        line.refresh_from_db()
        assert line.quantity_received == Decimal("9") and line.quantity_given == Decimal("12")
        assert line.received_by == "CBI"
        # offered once only: it has been said
        assert "Confirm what you received" not in client.get(url(issued)).content.decode()

    def test_a_handout_not_yet_given_cannot_be_confirmed_received(self, client, da, world):
        issued = link_for(da, world, world["wuse"])
        assert "Confirm what you received" not in client.get(url(issued)).content.decode()
        line = DistributionLine.objects.get()
        client.post(
            url(issued),
            {
                "action": "confirm_received",
                "confirm_received-line": line.pk,
                "confirm_received-quantity_received": "9",
                "confirm_received-received_on": "2026-11-10",
            },
        )
        line.refresh_from_db()
        assert line.quantity_received is None


class TestConfirmingManyPlacesAtOnce:
    @pytest.fixture
    def two_places(self, da, world):
        garki = op(
            da,
            "supply_point_upsert",
            data={
                "slug": "garki",
                "name": "Garki PHC",
                "kind": "facility",
                "source": "we_recorded",
                "managed_by_org_id": world["partner"]["id"],
            },
        )
        run = op(
            da,
            "distribution_plan",
            data={
                "supply_point_id": world["store"]["id"],
                "opportunity_id": OPPORTUNITY,
                "commodity_slug": "rutf",
                "distributed_on": "2026-11-16",
                "reference": "Second handout",
                "source": "we_recorded",
                "lines": [
                    {"to_supply_point_id": world["wuse"]["id"], "quantity": "12", "quantity_unit": "carton"},
                    {"to_supply_point_id": garki["id"], "quantity": "10", "quantity_unit": "carton"},
                ],
            },
        )
        return run

    def post(self, client, issued, line, other_places):
        return client.post(
            url(issued),
            {
                "action": "confirm_handout",
                "confirm_handout-line": line.pk,
                "confirm_handout-quantity_given": "12",
                "confirm_handout-handed_out_on": "2026-11-16",
                "confirm_handout-other_places": other_places,
            },
        )

    def test_one_submission_confirms_the_chosen_place_and_the_others_named(self, client, da, world, two_places):
        issued = link_for(da, world, world["store"])
        line = DistributionLine.objects.get(distribution_id=two_places["id"], to_supply_point_id=world["wuse"]["id"])
        self.post(client, issued, line, "Garki PHC - 8 cartons")
        given = {
            l.to_supply_point.name: l.quantity_given
            for l in DistributionLine.objects.filter(distribution_id=two_places["id"])
        }
        assert given == {"Wuse PHC": Decimal("12"), "Garki PHC": Decimal("8")}
        assert Movement.objects.filter(distribution_id=two_places["id"]).count() == 2

    def test_an_unreadable_or_unknown_place_confirms_nothing(self, client, da, world, two_places):
        issued = link_for(da, world, world["store"])
        line = DistributionLine.objects.get(distribution_id=two_places["id"], to_supply_point_id=world["wuse"]["id"])
        response = self.post(client, issued, line, "Nowhere Clinic - 3 cartons")
        assert "Nowhere Clinic" in response.content.decode()
        assert not Movement.objects.filter(distribution_id=two_places["id"]).exists()
