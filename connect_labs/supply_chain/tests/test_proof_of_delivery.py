"""Delivered, according to whom?

THIS REPOSITORY IS PUBLIC. Every organisation and figure here is invented.

`proof_of_delivery` was one of fifteen document kinds and the single line in
`records.py` naming it was the only line in the application that mentioned it.
Nothing required one, nothing read one, nothing derived anything from one. So
a shipment reached `delivered` because somebody said so, and a carrier could
be paid for a delivery no one had evidenced.

A POD is not a goods received note and the difference is where money is
argued about. A GRN is the receiving side's own record -- what we counted,
what we accepted into stock. A POD is evidence about the CARRIER: handed over
at this place, on this day, signed for by this person. They describe one
event and they routinely disagree; the classic case is a POD signed for
twenty pallets against a GRN recording eighteen accepted and two damaged.

Two checks, both from data already stored, and neither of them a new opinion
about what a consignment ought to carry:

  1. a consignment recorded as delivered with no proof of delivery on file --
     the delivery is an assertion, and this says so;
  2. a charge paid to a carrier for a consignment with no proof of delivery --
     money out for a leg nobody evidenced.

This deliberately does NOT refuse either act. The domain records what people
did; a store really may have taken goods before the paperwork caught up. It
surfaces the gap and names who can close it, which is what every other check
here does.
"""

from datetime import date, timedelta

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10956


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
    carrier = call_operation("org_upsert", access, {"data": {"slug": "a-carrier", "name": "A Placeholder Carrier"}})
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
                "quantity": "100",
                "quantity_unit": "box",
                "currency": "USD",
                "status": "placed",
                "signed_on": days_ago(40),
                "source": "we_recorded",
            }
        },
    )
    return {"access": access, "contract": contract, "store": store, "carrier": carrier}


def _shipment(world, *, status, dispatched=None):
    return call_operation(
        "shipment_record",
        world["access"],
        {
            "data": {
                "contract_id": world["contract"]["id"],
                "reference": "A-PLACEHOLDER-WAYBILL",
                "status": status,
                "dispatched_on": dispatched or days_ago(20),
                "source": "we_recorded",
                "lines": [{"quantity": "100", "quantity_unit": "box"}],
            }
        },
    )


def _pod(world, shipment):
    return call_operation(
        "document_attach",
        world["access"],
        {
            "data": {
                "shipment_id": shipment["id"],
                "kind": "proof_of_delivery",
                "reference": "A-PLACEHOLDER-POD",
                "external_url": "https://example.invalid/a-placeholder-pod",
                "source": "document",
            }
        },
    )


def _charge(world, shipment, *, paid_on):
    return call_operation(
        "charge_record",
        world["access"],
        {
            "data": {
                "shipment_id": shipment["id"],
                "kind": "inland_freight",
                "payee_org_id": world["carrier"]["id"],
                "amount": "500.00",
                "currency": "USD",
                **({"paid_on": paid_on} if paid_on else {}),
                "source": "we_recorded",
            }
        },
    )


def _kinds(world):
    checks = call_operation("checks_list", world["access"], {})["checks"]
    return [c["kind"] for c in checks]


class TestADeliveryNobodyEvidenced:
    def test_a_delivered_consignment_with_no_proof_is_raised(self, world):
        _shipment(world, status="delivered")
        assert "shipment_delivered_unevidenced" in _kinds(world)

    def test_the_same_consignment_with_a_proof_on_file_is_not(self, world):
        """The other side of the fixture, so the check cannot pass as always-on."""
        shipment = _shipment(world, status="delivered")
        _pod(world, shipment)
        assert "shipment_delivered_unevidenced" not in _kinds(world)

    def test_a_consignment_still_in_transit_is_not_asked_for_one_yet(self, world):
        """A POD evidences an arrival. Nothing has arrived."""
        _shipment(world, status="in_transit")
        assert "shipment_delivered_unevidenced" not in _kinds(world)


class TestPayingForALegNobodyEvidenced:
    def test_a_carrier_paid_without_a_proof_of_delivery_is_raised(self, world):
        shipment = _shipment(world, status="delivered")
        _charge(world, shipment, paid_on=days_ago(2))
        assert "charge_paid_unevidenced" in _kinds(world)

    def test_a_charge_assessed_but_not_yet_paid_is_not(self, world):
        """`paid_on` is null while assessed. Nothing has left the account."""
        shipment = _shipment(world, status="delivered")
        _charge(world, shipment, paid_on=None)
        assert "charge_paid_unevidenced" not in _kinds(world)

    def test_a_carrier_paid_with_a_proof_on_file_is_not(self, world):
        shipment = _shipment(world, status="delivered")
        _pod(world, shipment)
        _charge(world, shipment, paid_on=days_ago(2))
        assert "charge_paid_unevidenced" not in _kinds(world)


class TestItRecordsRatherThanRefuses:
    def test_marking_a_consignment_delivered_without_a_proof_still_works(self, world):
        """The domain records what people did.

        A store really may take goods before the paperwork catches up, and a
        system that refused the entry would simply not be told about the
        delivery -- which is worse than knowing about it and saying the
        evidence is missing.
        """
        shipment = _shipment(world, status="delivered")
        assert shipment["status"] == "delivered"


def test_every_check_kind_has_words_for_a_reader():
    """A kind with no label renders its slug on the checks page.

    Not specific to proof of delivery: it caught these two, and the next kind
    somebody adds will be caught the same way. `test_no_raw_codes` walks the
    screens, so it can only see kinds its fixtures happen to produce -- this
    compares the two tables directly and needs no fixture at all.
    """
    from connect_labs.supply_chain.checks import KIND_CATEGORIES
    from connect_labs.supply_chain.templatetags.supply_chain_extras import CHECK_LABELS

    missing = sorted(set(KIND_CATEGORIES) - set(CHECK_LABELS))
    assert not missing, f"these check kinds would render as raw slugs: {missing}"
