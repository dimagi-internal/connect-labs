"""Goods that are not bought.

THIS REPOSITORY IS PUBLIC. Every organisation, product and figure here is
invented.

A donor supplies chlorine in kind; a partner buys MUAC strips out of its setup
fee. Treated as priced purchases, both raise "landed cost cannot be computed"
for ever -- cost data that was never going to exist, reported as missing
(field use cases, G2). `Contract.consideration` says which it is, and the
physical chain -- supplier, quantity, dates, shipments, receipts -- is
unchanged.
"""

import jsonschema
import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10612


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def parties(da):
    op(
        da,
        "commodity_upsert",
        data={"slug": "chlorine", "name": "Chlorine", "base_unit": "L", "pack_unit": "jerry_can"},
    )
    donor = op(da, "supplier_create", data={"name": "A donor"})
    us = op(da, "org_upsert", data={"slug": "us", "name": "The programme"})
    return donor, us


def _contract(da, parties, **extra):
    donor, us = parties
    data = {
        "commodity_slug": "chlorine",
        "supplier_id": donor["id"],
        "buyer_of_record": "programme_org",
        "buyer_org_id": us["id"],
        "source": "we_recorded",
        "reference": "DON-1",
        "quantity": "600",
        "quantity_unit": "jerry_can",
        **extra,
    }
    return op(da, "contract_create", data=data)


class TestWhatAContractSaysAboutPayment:
    def test_a_contract_is_priced_unless_it_says_otherwise(self, da, parties):
        assert _contract(da, parties)["consideration"] == "priced"

    def test_the_value_is_one_of_three(self, da, parties):
        with pytest.raises(jsonschema.ValidationError):
            _contract(da, parties, consideration="free")

    def test_an_in_kind_contract_with_a_unit_price_is_refused(self, da, parties):
        """A price on goods nobody is paying for is two statements that
        cannot both be true."""
        with pytest.raises(ValueError, match="in kind"):
            _contract(da, parties, consideration="in_kind", unit_price="2.00", unit_price_unit="per_pack")


class TestLandedCostSaysWhyThereIsNone:
    def test_in_kind_goods_are_not_purchased_rather_than_unconfirmed(self, da, parties):
        contract = _contract(da, parties, consideration="in_kind")
        landed = op(da, "contract_landed_cost", contract_id=contract["id"], compare_buyers=True)
        assert landed["consideration"] == "in_kind"
        assert landed["goods"] == {"not_costed": "not purchased (in kind)"}
        assert landed["landed_total"] == {"not_costed": "not purchased (in kind)"}
        assert "unconfirmed" not in landed["landed_total"]
        # Comparing buyers of record means nothing for goods nobody buys.
        assert all("not_costed" in cell for cell in landed["by_buyer"].values())

    def test_goods_paid_for_out_of_a_setup_fee_say_so(self, da, parties):
        contract = _contract(da, parties, consideration="bundled")
        landed = op(da, "contract_landed_cost", contract_id=contract["id"])
        assert landed["landed_total"] == {"not_costed": "bundled in setup fee"}

    def test_a_priced_contract_is_unchanged(self, da, parties):
        contract = _contract(da, parties)
        landed = op(da, "contract_landed_cost", contract_id=contract["id"])
        assert "unconfirmed" in landed["landed_total"]


class TestTheChecksDoNotAskForCostThatWillNeverExist:
    def test_an_in_kind_contract_does_not_raise_cost_unconfirmed(self, da, parties):
        _contract(da, parties, consideration="in_kind")
        kinds = [c["kind"] for c in op(da, "checks_list")["checks"]]
        assert "contract_cost_unconfirmed" not in kinds

    def test_a_priced_contract_with_no_price_still_does(self, da, parties):
        _contract(da, parties)
        kinds = [c["kind"] for c in op(da, "checks_list")["checks"]]
        assert "contract_cost_unconfirmed" in kinds


class TestThePhysicalChainIsUnchanged:
    def test_an_in_kind_contract_still_receives_and_matches(self, da, parties):
        contract = _contract(da, parties, consideration="in_kind")
        store = op(
            da,
            "supply_point_upsert",
            data={"slug": "store", "name": "Store", "kind": "central_store", "source": "we_recorded"},
        )
        op(
            da,
            "receipt_record",
            data={
                "contract_id": contract["id"],
                "supply_point_id": store["id"],
                "received_on": "2026-09-01",
                "source": "we_recorded",
                "lines": [{"quantity_accepted": "600", "quantity_unit": "jerry_can"}],
            },
        )
        match = op(da, "contract_match", contract_id=contract["id"])
        assert match["status"] == "fully_received"
        # Nothing is owed for goods nobody bought -- and that is a statement,
        # not a missing price.
        assert match["payable_now"] == {"not_costed": "not purchased (in kind)"}


# ---- screens --------------------------------------------------------------


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, fulfilment_views, views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access

    account = django_user_model.objects.create_user(username="donor", password="x", email="donor@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "fulfilment_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    return client


class TestTheScreens:
    def test_the_order_form_asks_how_it_is_paid_for(self, scoped, da, parties):
        body = scoped.get(reverse("supply_chain:contract_create")).content.decode()
        assert 'name="consideration"' in body
        assert "In kind" in body

    def test_the_order_page_says_the_goods_were_not_bought(self, scoped, da, parties):
        contract = _contract(da, parties, consideration="in_kind")
        body = scoped.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert "not purchased (in kind)" in body
        assert "Unconfirmed" not in body.split("Landed cost", 1)[1].split("Ordered", 1)[0]


class TestTheMatchSaysWhatHappened:
    """What the order page's match panel said about a donation on its way.

    Both were on screen while a donated import sat at customs: the panel read
    "part received" with nothing received, and a red "billed beyond what
    arrived" row for a donation nobody will ever bill.
    """

    def _store(self, da):
        return op(
            da,
            "supply_point_upsert",
            data={"slug": "store", "name": "Store", "kind": "central_store", "source": "we_recorded"},
        )

    def _receive(self, da, contract, accepted, **line):
        op(
            da,
            "receipt_record",
            data={
                "contract_id": contract["id"],
                "supply_point_id": self._store(da)["id"],
                "received_on": "2026-09-01",
                "source": "we_recorded",
                "lines": [{"quantity_accepted": accepted, "quantity_unit": "jerry_can", **line}],
            },
        )

    def test_nothing_received_yet_is_not_part_received(self, da, parties):
        contract = _contract(da, parties, consideration="in_kind")
        assert op(da, "contract_match", contract_id=contract["id"])["status"] == "not_received"

    def test_a_partial_receipt_is_still_part_received(self, da, parties):
        contract = _contract(da, parties, consideration="in_kind")
        self._receive(da, contract, "598", quantity_rejected="2", rejection_reason="cracked")
        assert op(da, "contract_match", contract_id=contract["id"])["status"] == "part_received"

    def test_the_page_raises_billed_beyond_arrived_only_when_it_is(self, scoped, da, parties):
        contract = _contract(da, parties, consideration="in_kind")
        self._receive(da, contract, "598")
        body = scoped.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert "Billed beyond what arrived" not in body

    def test_the_page_still_raises_it_when_more_is_billed_than_arrived(self, scoped, da, parties):
        contract = _contract(da, parties, unit_price="2.00", unit_price_unit="per_pack", currency="USD")
        self._receive(da, contract, "100")
        op(
            da,
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "reference": "INV-1",
                "issued_on": "2026-09-02",
                "amount": "300.00",
                "currency": "USD",
                "quantity_billed": "150",
                "quantity_unit": "jerry_can",
                "source": "we_recorded",
            },
        )
        body = scoped.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert "Billed beyond what arrived" in body
