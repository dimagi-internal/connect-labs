"""A tender can go to several places, or be collected from the supplier.

THIS REPOSITORY IS PUBLIC. Every company, place and price here is invented.
"""

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.market import service
from connect_labs.supply_chain.models import Supplier, SupplierProfile, Tender
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10501
SCOPE = f"prog:{PROGRAM}"

KANO = {"name": "Central store", "city": "Kano", "country_name": "Nigeria"}
SOKOTO = {"name": "Sokoto store", "city": "Sokoto", "country_name": "Nigeria"}


def op(name, **payload):
    return call_operation(name, SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM), payload)


@pytest.fixture
def rutf():
    return op(
        "commodity_upsert",
        data={
            "slug": "rutf",
            "name": "RUTF",
            "category": "therapeutic_food",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
        },
    )


def tender(places=(KANO, SOKOTO), pickup=False, open_it=True):
    made = op(
        "tender_create",
        data={
            "label": "RUTF — two stores",
            "delivery_points": list(places),
            "pickup_accepted": pickup,
            "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
        },
    )
    if open_it:
        op("tender_open", tender_id=made["id"])
    return made


def supplier():
    return Supplier.objects.enrol(SCOPE, name="Plateau Foods")


def complete(**extra):
    """A quote that states everything except what the test is about."""
    return {
        "as_quoted_amount": "50.00",
        "as_quoted_unit": "per_pack",
        "as_quoted_currency": "USD",
        "quantity_basis": "2000",
        "quantity_basis_unit": "carton",
        "pack_spec_source": "stated_on_quote",
        "base_per_pack_stated": 150,
        "base_unit_grams_stated": 92,
        "freight_basis": "included",
        "duties_basis": "included",
        **extra,
    }


class TestThePlaces:
    def test_each_place_gets_a_stable_key(self, rutf):
        made = tender()
        assert [p["key"] for p in made["delivery_points"]] == ["central-store", "sokoto-store"]

    def test_two_places_with_one_name_get_two_keys(self, rutf):
        made = tender(places=({"name": "Store", "city": "Kano"}, {"name": "Store", "city": "Sokoto"}))
        assert [p["key"] for p in made["delivery_points"]] == ["store", "store-2"]

    def test_one_old_style_delivery_point_is_still_accepted(self, rutf):
        made = op(
            "tender_create",
            data={"label": "Old caller", "delivery_point": {**KANO, "incoterm_requested": "DAP"}},
        )
        assert made["delivery_points"] == [{**KANO, "key": "central-store"}]
        assert made["incoterm_requested"] == "DAP"
        assert made["delivery_point"]["city"] == "Kano", "the first place, for readers of the old key"


class TestOpening:
    def test_a_tender_with_neither_places_nor_collection_does_not_open(self, rutf):
        made = tender(places=(), open_it=False)
        with pytest.raises(ValueError, match="delivery point, or to accept collection"):
            op("tender_open", tender_id=made["id"])

    def test_collection_alone_is_enough_to_open(self, rutf):
        made = tender(places=(), pickup=True)
        assert Tender.objects.get(pk=made["id"]).status == "open"


class TestABidSaysWhereItGoes:
    def test_a_place_the_tender_does_not_list_is_refused(self, rutf):
        made = tender()
        with pytest.raises(ValueError, match="no delivery place abuja"):
            op(
                "quote_record",
                data=complete(
                    tender_id=made["id"],
                    commodity_slug="rutf",
                    supplier_id=supplier().pk,
                    delivery_point_keys=["abuja"],
                ),
            )

    def test_collection_is_refused_on_a_tender_that_does_not_accept_it(self, rutf):
        made = tender(pickup=False)
        with pytest.raises(ValueError, match="does not accept collection"):
            op(
                "quote_record",
                data=complete(
                    tender_id=made["id"],
                    commodity_slug="rutf",
                    supplier_id=supplier().pk,
                    delivery_mode="pickup",
                    pickup_location="Their warehouse",
                ),
            )

    def test_a_multi_place_bid_that_names_no_place_is_asked_which(self, rutf):
        made = tender()
        quote = op(
            "quote_record", data=complete(tender_id=made["id"], commodity_slug="rutf", supplier_id=supplier().pk)
        )
        missing = op("quote_get", quote_id=quote["id"])["missing"]
        asked = [m for m in missing if m["key"] == "delivery_places"]
        assert asked and "Kano" in asked[0]["question"] and "Sokoto" in asked[0]["question"]

    def test_the_comparison_says_where_each_bid_goes(self, rutf):
        made = tender(pickup=True)
        who = supplier()
        op(
            "quote_record",
            data=complete(
                tender_id=made["id"], commodity_slug="rutf", supplier_id=who.pk, delivery_point_keys=["sokoto-store"]
            ),
        )
        op(
            "quote_record",
            data=complete(
                tender_id=made["id"],
                commodity_slug="rutf",
                supplier_id=who.pk,
                delivery_mode="pickup",
                pickup_location="Their warehouse, Kano",
            ),
        )
        compared = op("tender_compare", tender_id=made["id"], commodity_slug="rutf")
        said = {row["delivery"] for row in compared["comparable"] + compared["blocked"]}
        assert said == {"to Sokoto store, Sokoto, Nigeria", "collected from Their warehouse, Kano"}


class TestACollectedBid:
    def test_its_delivered_cost_is_unconfirmed_until_our_transport_is_entered(self, rutf):
        made = tender(pickup=True)
        who = supplier()
        quote = op(
            "quote_record",
            data=complete(
                tender_id=made["id"],
                commodity_slug="rutf",
                supplier_id=who.pk,
                delivery_mode="pickup",
                pickup_location="Their warehouse",
            ),
        )

        compared = op("tender_compare", tender_id=made["id"], commodity_slug="rutf")
        assert [r["quote_id"] for r in compared["blocked"]] == [quote["id"]]
        missing = op("quote_get", quote_id=quote["id"])["missing"]
        transport = [m for m in missing if m["key"] == "pickup_transport"]
        assert transport and transport[0]["audience"] == "internal", "our gap, not the supplier's"

        costed = op(
            "quote_correct",
            quote_id=quote["id"],
            data={"buyer_transport_amount": "400.00"},
            reason="our transport cost entered",
        )
        compared = op("tender_compare", tender_id=made["id"], commodity_slug="rutf")
        assert [r["quote_id"] for r in compared["comparable"]] == [costed["id"]]

    def test_it_is_never_asked_about_supplier_freight(self, rutf):
        made = tender(pickup=True)
        quote = op(
            "quote_record",
            data=complete(
                tender_id=made["id"],
                commodity_slug="rutf",
                supplier_id=supplier().pk,
                delivery_mode="pickup",
                pickup_location="Their warehouse",
                freight_basis="not_specified",
            ),
        )
        keys = {m["key"] for m in op("quote_get", quote_id=quote["id"])["missing"]}
        assert "freight_basis" not in keys


class TestOnTheMarketplace:
    def test_a_supplier_bids_for_collection(self, rutf):
        made = tender(pickup=True)
        org = LabsOrg.objects.create(slug="plateau", name="Plateau Foods")
        SupplierProfile.objects.create(org=org)

        quote = service.bid(
            made["id"],
            "rutf",
            org=org,
            orgs=[org],
            user=None,
            data={**complete(), "delivery_mode": "pickup", "pickup_location": "Our warehouse"},
        )

        assert quote.delivery_mode == "pickup"
        assert quote.pickup_location == "Our warehouse"

    def test_the_bid_form_asks_which_places_when_there_are_several(self):
        from connect_labs.supply_chain.market.forms import BidForm

        places = [{**KANO, "key": "central-store"}, {**SOKOTO, "key": "sokoto-store"}]
        form = BidForm(data={**complete(), "delivery_mode": "delivered"}, places=places)
        assert not form.is_valid()
        assert "delivery_point_keys" in form.errors

    def test_the_bid_form_fills_in_the_only_place(self):
        from connect_labs.supply_chain.market.forms import BidForm

        form = BidForm(data={**complete()}, places=[{**KANO, "key": "central-store"}])
        assert form.is_valid(), form.errors
        assert form.payload()["delivery_point_keys"] == ["central-store"]


def _migrate(targets):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.mark.django_db(transaction=True)
def test_the_migration_turns_each_delivery_point_into_a_one_place_list():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    old = _migrate([("supply_chain", "0027_round_is_a_tender")])
    OldTender = old.get_model("supply_chain", "Tender")
    placed = OldTender.objects.create(
        program_id=PROGRAM,
        label="Old",
        delivery_point={"name": "Central store", "city": "Kano", "incoterm_requested": "DAP"},
    )
    nowhere = OldTender.objects.create(program_id=PROGRAM, label="Nowhere", delivery_point={})
    try:
        new = _migrate([("supply_chain", "0029_tender_drops_single_delivery_point")])
        NewTender = new.get_model("supply_chain", "Tender")
        moved = NewTender.objects.get(pk=placed.pk)
        assert moved.delivery_points == [{"key": "main", "name": "Central store", "city": "Kano"}]
        assert moved.incoterm_requested == "DAP"
        assert NewTender.objects.get(pk=nowhere.pk).delivery_points == []
    finally:
        _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
