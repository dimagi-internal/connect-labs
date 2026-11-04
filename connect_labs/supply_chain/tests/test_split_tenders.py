"""A tender split by partner, and its orders read as a set.

Sophie Feintuch's RUTF feedback, "Split tenders by partner" and "Track the orders of one award as a
set". THIS REPOSITORY IS PUBLIC. Every organisation, product and figure here is invented.
"""

from datetime import date

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.overview import next_step
from connect_labs.supply_chain.procurement.services.splits import order_set, split_price_status

pytestmark = pytest.mark.django_db

PROGRAM = 10772


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def world(da):
    op(da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton"})
    supplier = op(da, "supplier_create", data={"name": "Quill Foods"})
    orgs = [op(da, "org_upsert", data={"slug": f"p{i}", "name": f"Partner {i}"}) for i in range(1, 4)]
    tender = op(
        da,
        "tender_create",
        data={
            "label": "Tender 1",
            "delivery_point": {"city": "Abuja"},
            "lines": [{"commodity_slug": "rutf", "quantity": "10", "quantity_unit": "carton"}],
            "partner_splits": [
                {
                    "org_id": orgs[0]["id"],
                    "quantity": 400,
                    "delivery_point": {"name": "Partner 1 warehouse", "city": "Kano"},
                    "receiver_name": "Amina",
                },
                {
                    "org_id": orgs[1]["id"],
                    "quantity": 600,
                    "delivery_point": {"name": "Partner 2 warehouse", "city": "Sokoto"},
                },
                {
                    "org_id": orgs[2]["id"],
                    "quantity": 1000,
                    "delivery_point": {"name": "Partner 3 warehouse", "city": "Lagos"},
                    "buying_model": "programme_buys",
                },
            ],
        },
    )
    return {"supplier": supplier, "orgs": orgs, "tender": tender}


def _award(da, world, holds=None):
    quote = op(
        da,
        "quote_record",
        data={
            "tender_id": world["tender"]["id"],
            "commodity_slug": "rutf",
            "supplier_id": world["supplier"]["id"],
            "as_quoted_amount": "52.40",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "2000",
            "quantity_basis_unit": "carton",
            **({"price_holds_for_purchases": holds} if holds is not None else {}),
        },
    )
    return quote, op(da, "award_create", tender_id=world["tender"]["id"], quote_id=quote["id"])


class TestTheSplit:
    def test_the_line_quantity_is_the_total_of_the_splits(self, world):
        tender = world["tender"]
        assert tender["lines"][0]["quantity"] == "2000"
        assert len(tender["partner_splits"]) == 3
        keys = {p["key"] for p in tender["delivery_points"]}
        assert {s["delivery_point_key"] for s in tender["partner_splits"]} <= keys

    def test_a_tender_with_no_split_is_unchanged(self, da):
        op(da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF"})
        tender = op(
            da,
            "tender_create",
            data={
                "label": "Plain",
                "delivery_point": {"city": "Kano"},
                "lines": [{"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"}],
            },
        )
        assert tender["partner_splits"] == [] and tender["lines"][0]["quantity"] == "500"

    def test_a_partner_twice_or_unknown_is_refused(self, da, world):
        org = world["orgs"][0]["id"]
        with pytest.raises(ValueError, match="split twice"):
            op(
                da,
                "tender_update",
                tender_id=world["tender"]["id"],
                data={"partner_splits": [{"org_id": org, "quantity": 1}, {"org_id": org, "quantity": 2}]},
            )
        with pytest.raises(ValueError, match="organisation"):
            op(
                da,
                "tender_update",
                tender_id=world["tender"]["id"],
                data={"partner_splits": [{"org_id": 999999, "quantity": 1}]},
            )

    def test_the_request_email_asks_for_the_split_price(self, da, world):
        draft = op(
            da,
            "request_render",
            tender_id=world["tender"]["id"],
            supplier_id=world["supplier"]["id"],
            commodity_slug="rutf",
        )
        assert "split between 3 deliveries" in draft["text"]
        assert "your price holds if we buy as 3 separate purchases" in draft["text"]
        assert "Partner 1 warehouse" in draft["text"] and "Amina" in draft["text"]


class TestThePriceHolds:
    def test_not_said_is_not_no(self, world):
        status = split_price_status(world["tender"], {"commodity_slug": "rutf", "price_holds_for_purchases": None})
        assert status["holds"] is None and "Not said" in status["note"]

    def test_holds_for_enough_purchases(self, world):
        assert split_price_status(world["tender"], {"commodity_slug": "rutf", "price_holds_for_purchases": 3})["holds"]
        short = split_price_status(world["tender"], {"commodity_slug": "rutf", "price_holds_for_purchases": 2})
        assert short["holds"] is False and "needs 3" in short["note"]

    def test_the_quote_records_it(self, da, world):
        quote, _ = _award(da, world, holds=3)
        assert quote["price_holds_for_purchases"] == 3


class TestOrdersFollowTheSplit:
    def test_one_order_per_partner_and_the_set_is_tracked(self, da, world, monkeypatch):
        from connect_labs.labs.models import LabsOrg
        from connect_labs.supply_chain import runner

        ours = op(da, "org_upsert", data={"slug": "us", "name": "The programme"})
        monkeypatch.setattr(runner, "running_org", lambda program_id: LabsOrg.objects.get(pk=ours["id"]))
        quote, award = _award(da, world, holds=3)
        before = op(da, "award_orders_progress", award_id=award["id"])
        assert before["text"] == "0 of 3 partners have ordered · 0 of 2,000 cartons" and not before["complete"]

        made = op(da, "award_orders_create", award_id=award["id"])
        assert len(made) == 3
        by_partner = {c["partner_org_id"]: c for c in made}
        first = by_partner[world["orgs"][0]["id"]]
        assert first["buyer_org_id"] == world["orgs"][0]["id"] and first["buyer_of_record"] == "partner_org"
        assert first["buying_model"] == "partner_buys" and first["quantity"] == "400" and first["unit_price"] == "52.4"
        done = op(da, "award_orders_progress", award_id=award["id"])
        assert done["complete"] and done["partners_ordered"] == 3
        # the partner we buy for: we are the buyer, the partner is who it is for
        third = by_partner[world["orgs"][2]["id"]]
        assert third["buyer_org_id"] == ours["id"] and third["buying_model"] == "programme_buys"
        # again: nothing more to draft
        assert op(da, "award_orders_create", award_id=award["id"]) == []

    def test_a_partner_we_buy_for_needs_a_running_organisation(self, da, world):
        _, award = _award(da, world)
        with pytest.raises(ValueError, match="running organisation"):
            op(da, "award_orders_create", award_id=award["id"])


class TestTheSetIsNotDoneAfterTheFirstOrder:
    def test_four_of_five(self):
        tender = {
            "id": 1,
            "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
            "partner_splits": [
                {"org_id": i, "org_name": f"P{i}", "commodity_slug": "rutf", "quantity": q, "quantity_unit": "carton"}
                for i, q in [(1, "300"), (2, "300"), (3, "600"), (4, "600"), (5, "200")]
            ],
        }
        contracts = [
            {"commodity_slug": "rutf", "status": "placed", "buyer_org_id": i, "quantity": q}
            for i, q in [(1, "300"), (2, "300"), (3, "600"), (4, "600")]
        ]
        got = order_set(tender, contracts, "rutf")
        assert got["text"] == "4 of 5 partners have ordered · 1,800 of 2,000 cartons"
        assert got["waiting_partners"] == ["P5"] and not got["complete"]
        line = next_step(
            tenders=[{**tender, "status": "awarded"}],
            contracts=[{**c, "tender_id": 1, "id": i} for i, c in enumerate(contracts)],
            points=[],
            today=date(2026, 11, 4),
        )
        assert "4 of 5 partners have ordered" in line["line"] and line["next"] == "Place an order for P5"

    def test_a_single_quantity_award_is_done_when_the_quantity_is_ordered(self):
        tender = {"id": 1, "lines": [{"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"}]}
        part = order_set(tender, [{"commodity_slug": "rutf", "status": "placed", "quantity": "200"}], "rutf")
        assert not part["complete"] and part["text"] == "200 of 500 cartons"
        full = order_set(tender, [{"commodity_slug": "rutf", "status": "placed", "quantity": "500"}], "rutf")
        assert full["complete"]
