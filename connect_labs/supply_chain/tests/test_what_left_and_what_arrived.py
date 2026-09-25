"""Twenty left the warehouse and eighteen arrived. Where are the other two?

THIS REPOSITORY IS PUBLIC. Every organisation and figure here is invented.

A consignment records what was DESPATCHED, on its lines. A goods received
note records what was ACCEPTED and what was REJECTED. Nothing compared them,
so a consignment could be marked delivered having lost two cartons on the
road and the only trace was a stock balance quietly lower than expected.

The gap matters because of what it is NOT. Rejected goods arrived and were
turned away -- somebody saw them, wrote a reason, and the record says so.
What this finds is the remainder: quantity that left, was never accepted, and
was never rejected either. Nobody has said what happened to it.

Three decisions:

**Only once the consignment has landed.** A part-received shipment is
mid-delivery, and calling the rest missing while it is still on the road
would cry wolf at every ordinary partial delivery.

**Rejections are subtracted, not counted as loss.** A rejection is an
accounted-for outcome. Treating it as missing would report the same two
cartons twice, once as rejected and once as lost.

**Units that do not match produce no figure.** A consignment despatched in
cartons and received in sachets cannot be differenced without a pack size,
and this domain does not guess one -- it says it cannot tell.
"""

from datetime import date, timedelta

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10957


def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture
def world():
    access = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
    call_operation(
        "commodity_upsert",
        access,
        {
            "data": {
                "slug": "a-placeholder-good",
                "name": "A placeholder good",
                "base_unit": "unit",
                "pack_unit": "box",
            }
        },
    )
    supplier = call_operation("supplier_create", access, {"data": {"name": "A Placeholder Seller"}})
    buyer = call_operation("org_upsert", access, {"data": {"slug": "a-buyer", "name": "A Placeholder Buyer"}})
    store = call_operation(
        "supply_point_upsert",
        access,
        {"data": {"slug": "a-store", "name": "A placeholder store", "kind": "central_store", "source": "we_recorded"}},
    )
    contract = call_operation(
        "contract_create",
        access,
        {
            "data": {
                "supplier_id": supplier["id"],
                "commodity_slug": "a-placeholder-good",
                "buyer_of_record": "programme_org",
                "buyer_org_id": buyer["id"],
                "delivery_supply_point_id": store["id"],
                "quantity": "20",
                "quantity_unit": "box",
                "currency": "USD",
                "status": "placed",
                "signed_on": days_ago(30),
                "source": "we_recorded",
            }
        },
    )
    return {"access": access, "contract": contract, "store": store}


def _despatch(world, *, quantity, unit="box", status="delivered"):
    return call_operation(
        "shipment_record",
        world["access"],
        {
            "data": {
                "contract_id": world["contract"]["id"],
                "reference": "A-PLACEHOLDER-WAYBILL",
                "status": status,
                "dispatched_on": days_ago(10),
                "source": "we_recorded",
                "lines": [{"quantity": quantity, "quantity_unit": unit}],
            }
        },
    )


def _receive(world, shipment, *, accepted, rejected=None, unit="box"):
    line = {"quantity_accepted": accepted, "quantity_unit": unit}
    if rejected:
        line["quantity_rejected"] = rejected
        line["rejection_reason"] = "damaged in transit"
    return call_operation(
        "receipt_record",
        world["access"],
        {
            "data": {
                "contract_id": world["contract"]["id"],
                "shipment_id": shipment["id"],
                "supply_point_id": world["store"]["id"],
                "received_on": days_ago(5),
                "source": "we_recorded",
                "lines": [line],
            }
        },
    )


def _check(world, kind="shipment_quantity_unaccounted"):
    checks = call_operation("checks_list", world["access"], {})["checks"]
    return next((c for c in checks if c["kind"] == kind), None)


class TestQuantityNobodyAccountedFor:
    def test_goods_that_left_and_never_arrived_are_raised(self, world):
        shipment = _despatch(world, quantity="20")
        _receive(world, shipment, accepted="18")

        found = _check(world)
        assert found is not None, "two boxes left and did not arrive"
        assert found["facts"]["unaccounted"] == "2"

    def test_a_consignment_fully_accounted_for_raises_nothing(self, world):
        """The other side of the fixture, so this cannot pass as always-on."""
        shipment = _despatch(world, quantity="20")
        _receive(world, shipment, accepted="20")

        assert _check(world) is None


class TestARejectionIsAccountedFor:
    def test_rejected_goods_are_not_counted_as_missing(self, world):
        """They arrived and were turned away. Somebody wrote down why.

        MUTATED: the rejected quantity was dropped from the arrived total, so
        the two rejected boxes were reported as unaccounted for as well as
        rejected -- the same two boxes, twice. This test went red. Reverted.
        """
        shipment = _despatch(world, quantity="20")
        _receive(world, shipment, accepted="18", rejected="2")

        assert _check(world) is None

    def test_a_rejection_does_not_hide_a_genuine_gap(self, world):
        """Eighteen accepted, one rejected, one nobody can account for."""
        shipment = _despatch(world, quantity="20")
        _receive(world, shipment, accepted="18", rejected="1")

        found = _check(world)
        assert found is not None
        assert found["facts"]["unaccounted"] == "1"


class TestItAsksRatherThanConcludes:
    def test_a_part_delivered_consignment_reports_the_gap_without_calling_it_loss(self, world):
        """Nothing in the data tells two stolen cartons from two still on the lorry.

        Both are quantity that left and has not been receipted, and only a
        person knows which. An earlier draft tried to gate this on the order
        having nothing outstanding, which made it unable to fire at all --
        goods that go missing keep an order incomplete forever, so "nothing
        more is coming" is never true exactly when the finding matters.

        So the gap is reported as a question, which is what every row on the
        checks list is. What it must not do is assert a loss.
        """
        shipment = _despatch(world, quantity="20")
        _receive(world, shipment, accepted="12")

        found = _check(world)
        assert found is not None
        assert found["facts"]["unaccounted"] == "8"
        assert found["category"] == "conflict", "two records disagree; nobody has said anything is lost"

    def test_it_is_not_worded_as_a_loss(self, world):
        """The label a reader sees must not accuse anybody of losing anything."""
        from connect_labs.supply_chain.templatetags.supply_chain_extras import CHECK_LABELS

        label = CHECK_LABELS["shipment_quantity_unaccounted"].lower()
        for word in ("lost", "stolen", "missing", "theft"):
            assert word not in label, f"{word!r} asserts what the record does not know"


class TestUnitsItCannotDifference:
    def test_a_receipt_in_another_unit_says_it_cannot_tell(self, world):
        """No pack size is invented to bridge boxes and units."""
        shipment = _despatch(world, quantity="20", unit="box")
        _receive(world, shipment, accepted="500", unit="unit")

        found = _check(world)
        assert found is not None
        assert found["facts"]["unaccounted"] is None
        assert "unit" in " ".join(found["facts"]["why_not"]).lower()
