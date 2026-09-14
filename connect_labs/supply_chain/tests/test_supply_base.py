"""The derived supply base.

THIS REPOSITORY IS PUBLIC. Every supplier and figure here is invented.

Unsaved model instances, like conftest's: the derivation only reads
attributes, so no database is needed.
"""

from datetime import date
from decimal import Decimal

from connect_labs.supply_chain.models import Award, Commodity, Contract, Item, Outreach, Quote, Round, Supplier
from connect_labs.supply_chain.procurement.services.supply_base import supply_base

SCOPE = "prog:10501"

RUTF = Commodity(scope_key=SCOPE, slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton")
F75 = Commodity(scope_key=SCOPE, slug="f75", name="F-75", base_unit="sachet", pack_unit="carton")


def supplier(pk, name, **kwargs):
    return Supplier(pk=pk, scope_key=SCOPE, name=name, **kwargs)


def round_(pk, *slugs):
    return Round(pk=pk, program_id=10501, label=f"Round {pk}", lines=[{"commodity_slug": s} for s in slugs])


def quote(pk, supplier_id, round_id, commodity=RUTF, **kwargs):
    fields = {
        "as_quoted_amount": Decimal("0.46"),
        "as_quoted_unit": "per_base_unit",
        "as_quoted_currency": "USD",
        "received_on": date(2026, 5, 1),
    }
    fields.update(kwargs)
    return Quote(pk=pk, supplier_id=supplier_id, round_id=round_id, commodity=commodity, **fields)


class TestWhatCountsAsEvidence:
    def test_a_quote_makes_a_supplier_part_of_the_supply_base(self):
        claims = supply_base(
            commodity_slug="rutf",
            suppliers=[supplier(1, "Northwind Foods")],
            quotes=[quote(10, supplier_id=1, round_id=5)],
        )
        assert [c.supplier_name for c in claims] == ["Northwind Foods"]
        assert claims[0].basis == "quoted"
        assert claims[0].last_heard == date(2026, 5, 1)

    def test_a_quote_for_a_different_product_does_not_carry_over(self):
        claims = supply_base(
            commodity_slug="f75",
            suppliers=[supplier(1, "Northwind Foods")],
            quotes=[quote(10, supplier_id=1, round_id=5, commodity=RUTF)],
        )
        assert claims == []

    def test_an_unanswered_invitation_is_weaker_than_a_quote_and_still_shown(self):
        claims = supply_base(
            commodity_slug="rutf",
            suppliers=[supplier(1, "Northwind Foods"), supplier(2, "Harbour Nutrition")],
            quotes=[quote(10, supplier_id=1, round_id=5)],
            outreach=[Outreach(pk=1, round_id=5, supplier_id=2, sent_on=date(2026, 4, 28), responded=False)],
            rounds=[round_(5, "rutf")],
        )
        assert [(c.supplier_name, c.basis) for c in claims] == [
            ("Northwind Foods", "quoted"),
            ("Harbour Nutrition", "invited"),
        ]

    def test_an_invitation_that_produced_a_quote_is_not_also_reported_as_silence(self):
        """Both records are real; showing them side by side would read as one
        supplier who quoted and, separately, never replied."""
        claims = supply_base(
            commodity_slug="rutf",
            suppliers=[supplier(1, "Northwind Foods")],
            quotes=[quote(10, supplier_id=1, round_id=5)],
            outreach=[Outreach(pk=1, round_id=5, supplier_id=1, sent_on=date(2026, 4, 28), responded=True)],
            rounds=[round_(5, "rutf")],
        )
        assert [e.kind for e in claims[0].evidence] == ["quoted"]

    def test_an_invitation_on_a_round_that_never_asked_for_this_product_is_not_evidence(self):
        claims = supply_base(
            commodity_slug="rutf",
            suppliers=[supplier(2, "Harbour Nutrition")],
            outreach=[Outreach(pk=1, round_id=5, supplier_id=2, sent_on=date(2026, 4, 28))],
            rounds=[round_(5, "f75")],
        )
        assert claims == []

    def test_a_contract_outranks_a_quote_and_both_are_kept(self):
        claims = supply_base(
            commodity_slug="rutf",
            suppliers=[supplier(1, "Northwind Foods")],
            quotes=[quote(10, supplier_id=1, round_id=5)],
            contracts=[Contract(pk=3, supplier_id=1, commodity=RUTF, reference="PO-114", signed_on=date(2026, 6, 2))],
        )
        assert claims[0].basis == "contracted"
        assert [e.kind for e in claims[0].evidence] == ["contracted", "quoted"]
        # The warmest date across everything, not the date of the strongest.
        assert claims[0].last_heard == date(2026, 6, 2)

    def test_a_withdrawn_quote_is_not_reported_as_a_superseded_one(self):
        """`quote_void` and `quote_correct` are different acts and the rest of
        this domain keeps them apart. A voided quote has no replacement, so
        rendering it "since superseded" asserts one that does not exist."""
        claims = supply_base(
            commodity_slug="rutf",
            suppliers=[supplier(1, "Northwind Foods")],
            quotes=[quote(10, supplier_id=1, round_id=5, voided=True, void_reason="sent in error")],
        )
        assert claims[0].basis == "quoted_voided"

    def test_a_corrected_quote_is_reported_as_superseded(self):
        replacement = quote(11, supplier_id=1, round_id=5, version=2)
        original = quote(10, supplier_id=1, round_id=5, superseded_by=replacement)
        claims = supply_base(
            commodity_slug="rutf",
            suppliers=[supplier(1, "Northwind Foods")],
            quotes=[original],
        )
        assert claims[0].basis == "quoted_superseded"

    def test_a_standing_quote_outranks_the_versions_it_replaced(self):
        replacement = quote(11, supplier_id=1, round_id=5, version=2, received_on=date(2026, 6, 20))
        original = quote(10, supplier_id=1, round_id=5, superseded_by=replacement)
        claims = supply_base(
            commodity_slug="rutf",
            suppliers=[supplier(1, "Northwind Foods")],
            quotes=[original, replacement],
        )
        assert claims[0].basis == "quoted"
        assert [e.kind for e in claims[0].evidence] == ["quoted", "quoted_superseded"]

    def test_a_supplier_outside_this_scope_is_left_out_rather_than_named_as_an_id(self):
        claims = supply_base(
            commodity_slug="rutf",
            suppliers=[],
            quotes=[quote(10, supplier_id=99, round_id=5)],
        )
        assert claims == []


class TestTheManufacturerMatch:
    def test_an_exact_name_match_is_evidence_and_says_what_it_is(self):
        claims = supply_base(
            commodity_slug="rutf",
            suppliers=[supplier(1, "Northwind Foods")],
            items=[
                Item(
                    pk=7,
                    scope_key=SCOPE,
                    sku="RUTF-NW-92",
                    name="NW RUTF",
                    commodity=RUTF,
                    manufacturer="northwind foods  ",
                )
            ],
        )
        assert claims[0].basis == "named_as_manufacturer"
        assert "RUTF-NW-92" in claims[0].evidence[0].detail

    def test_a_name_shared_by_two_suppliers_matches_neither(self):
        """`Supplier` has no uniqueness constraint on name and
        `create_supplier` does not check for one, so two rows really can
        normalise to the same string. Keeping the first would attach one
        company's trade item to another company's record."""
        claims = supply_base(
            commodity_slug="rutf",
            suppliers=[supplier(1, "Northwind Foods"), supplier(2, "northwind foods")],
            items=[
                Item(
                    pk=7,
                    scope_key=SCOPE,
                    sku="RUTF-NW-92",
                    name="NW RUTF",
                    commodity=RUTF,
                    manufacturer="Northwind Foods",
                )
            ],
        )
        assert claims == []

    def test_a_near_match_is_not_claimed(self):
        """Deciding that 'Northwind Foods' and 'Northwind Foods FZE' are one
        company is a judgement, and getting it wrong attaches one company's
        prices to another's name."""
        claims = supply_base(
            commodity_slug="rutf",
            suppliers=[supplier(1, "Northwind Foods")],
            items=[
                Item(
                    pk=7,
                    scope_key=SCOPE,
                    sku="RUTF-NW-92",
                    name="NW RUTF",
                    commodity=RUTF,
                    manufacturer="Northwind Foods FZE",
                )
            ],
        )
        assert claims == []


class TestNarrowingToOneTradeItem:
    def test_only_quotes_naming_that_item_survive(self):
        claims = supply_base(
            commodity_slug="rutf",
            item_id=7,
            suppliers=[supplier(1, "Northwind Foods"), supplier(2, "Harbour Nutrition")],
            quotes=[
                quote(10, supplier_id=1, round_id=5, item_id=7),
                quote(11, supplier_id=2, round_id=5, item_id=None),
            ],
        )
        assert [c.supplier_name for c in claims] == ["Northwind Foods"]

    def test_an_invitation_does_not_narrow_to_a_trade_item(self):
        """An RFQ is issued against a commodity line, so it says nothing about
        which of a product's trade items the supplier would have offered."""
        claims = supply_base(
            commodity_slug="rutf",
            item_id=7,
            suppliers=[supplier(2, "Harbour Nutrition")],
            outreach=[Outreach(pk=1, round_id=5, supplier_id=2, sent_on=date(2026, 4, 28))],
            rounds=[round_(5, "rutf")],
        )
        assert claims == []

    def test_an_award_follows_the_trade_item_on_the_quote_it_accepted(self):
        accepted = quote(10, supplier_id=1, round_id=5, item_id=7)
        other = quote(11, supplier_id=2, round_id=5, item_id=8)
        awards = [
            Award(pk=1, round_id=5, quote_id=10, supplier_id=1, commodity=RUTF, decided_on=date(2026, 6, 1)),
            Award(pk=2, round_id=5, quote_id=11, supplier_id=2, commodity=RUTF, decided_on=date(2026, 6, 1)),
        ]
        claims = supply_base(
            commodity_slug="rutf",
            item_id=7,
            suppliers=[supplier(1, "Northwind Foods"), supplier(2, "Harbour Nutrition")],
            quotes=[accepted, other],
            awards=awards,
        )
        assert [c.supplier_name for c in claims] == ["Northwind Foods"]
        assert claims[0].basis == "awarded"


class TestOrdering:
    def test_the_firmest_relationship_comes_first_even_when_it_is_the_oldest(self):
        """Dates deliberately run the other way. Sorted by recency alone this
        reads Invited, Quoted, Contracted -- so a version that dropped the
        strength key would still look sorted, which is how the first draft of
        this test passed against a broken ordering.
        """
        claims = supply_base(
            commodity_slug="rutf",
            suppliers=[supplier(1, "Aaa Invited"), supplier(2, "Zzz Contracted"), supplier(3, "Mmm Quoted")],
            quotes=[quote(10, supplier_id=3, round_id=5, received_on=date(2026, 6, 15))],
            contracts=[Contract(pk=3, supplier_id=2, commodity=RUTF, signed_on=date(2026, 1, 10))],
            outreach=[Outreach(pk=1, round_id=5, supplier_id=1, sent_on=date(2026, 7, 1))],
            rounds=[round_(5, "rutf")],
        )
        assert [c.supplier_name for c in claims] == ["Zzz Contracted", "Mmm Quoted", "Aaa Invited"]

    def test_within_one_kind_the_most_recent_comes_first(self):
        """Names run the opposite way to the dates, so sorting on the name
        alone cannot produce this order."""
        claims = supply_base(
            commodity_slug="rutf",
            suppliers=[supplier(1, "Aaa Quoted In March"), supplier(2, "Zzz Quoted In June")],
            quotes=[
                quote(10, supplier_id=1, round_id=5, received_on=date(2026, 3, 1)),
                quote(11, supplier_id=2, round_id=5, received_on=date(2026, 6, 1)),
            ],
        )
        assert [c.supplier_name for c in claims] == ["Zzz Quoted In June", "Aaa Quoted In March"]


class TestTheSeedCatalogue:
    def test_every_trade_item_has_a_product_to_hang_off(self):
        """`Item.commodity` is not nullable, and `catalogue_seed` refuses a
        trade item whose product is absent -- silently, into `refused`. So a
        trade item naming a slug this module does not define is not a crash,
        it is a seed that quietly ships incomplete."""
        from connect_labs.supply_chain import reference_catalogue

        slugs = {product["slug"] for product in reference_catalogue.PRODUCTS}
        missing = {item["commodity_slug"] for item in reference_catalogue.TRADE_ITEMS} - slugs
        assert missing == set()

    def test_no_gtin_is_invented(self):
        """A fabricated GTIN that passes its check digit is an identifier that
        may belong to a real, different product."""
        from connect_labs.supply_chain import reference_catalogue

        for item in reference_catalogue.TRADE_ITEMS:
            assert not any(item.get(key) for key in ("gtin_base", "gtin_pack", "gtin_case")), item["sku"]
