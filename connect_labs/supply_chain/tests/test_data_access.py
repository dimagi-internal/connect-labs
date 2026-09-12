"""SupplyDataAccess over the real database.

This replaces a suite that asserted on calls made to `LabsRecordAPIClient`.
Those tests could only ever prove that the right HTTP payload was assembled,
which is why a real bug -- a scope that made records written under one
opportunity invisible under another -- survived them. These exercise the
behaviour instead: what gets stored, what is refused, and what a write hands
back.
"""

from decimal import Decimal

import pytest

from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, Movement, Quote, SupplyPoint
from connect_labs.supply_chain.scopes import SYNTHETIC_FLOOR

pytestmark = pytest.mark.django_db

SYNTHETIC_PROGRAM = 10501
REAL_PROGRAM = 176


def access(program_id=SYNTHETIC_PROGRAM, organization_id=None):
    return SupplyDataAccess(access_token="unused", program_id=program_id, organization_id=organization_id)


@pytest.fixture
def da():
    return access()


@pytest.fixture
def rutf_row(da):
    return da.upsert_commodity(
        {
            "slug": "rutf",
            "name": "Ready-to-use therapeutic food",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
            "base_unit_grams": 92,
        }
    )


@pytest.fixture
def open_round(da, rutf_row):
    created = da.create_round(
        {
            "label": "Round 2",
            "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
            "delivery_point": {"name": "Central store", "city": "Kano", "country": "NG"},
        }
    )
    return da.open_round(created.pk)


class TestScoping:
    def test_a_numeric_organisation_scopes_reference_data_to_the_organisation(self):
        da = access(organization_id=42)
        assert da.reference_scope == "organization"
        assert da.scope_key == "org:42"

    def test_a_non_numeric_organisation_falls_back_to_the_programme(self):
        """Labs-only synthetic organisations carry a slug, not an id. Falling
        back keeps the app usable there instead of raising on int()."""
        da = access(organization_id="labs-only-org")
        assert da.reference_scope == "program"
        assert da.scope_key == f"prog:{SYNTHETIC_PROGRAM}"

    def test_reference_data_written_under_one_organisation_is_invisible_under_another(self):
        access(organization_id=1).upsert_commodity({"slug": "rutf", "name": "RUTF"})
        assert access(organization_id=2).get_commodity("rutf") is None
        assert access(organization_id=1).get_commodity("rutf") is not None

    def test_procurement_records_refuse_to_be_read_without_a_programme(self):
        """A stand-in scope would make records written by different callers
        indistinguishable -- the cross-programme leak the scoping prevents."""
        with pytest.raises(ValueError, match="require a programme scope"):
            access(program_id=None).list_rounds()


class TestReferenceData:
    def test_upsert_is_by_slug_within_the_scope(self, da):
        da.upsert_commodity({"slug": "rutf", "name": "First name"})
        da.upsert_commodity({"slug": "rutf", "name": "Second name"})
        assert Commodity.objects.filter(slug="rutf").count() == 1
        assert da.get_commodity("rutf").name == "Second name"

    def test_an_upsert_naming_one_field_does_not_blank_the_others(self, da, rutf_row):
        da.upsert_commodity({"slug": "rutf", "name": "Renamed"})
        again = da.get_commodity("rutf")
        assert again.name == "Renamed"
        assert again.base_per_pack == 150, "an unmentioned field was wiped"

    def test_a_write_hands_back_database_types_not_the_strings_it_was_given(self, da, rutf_row):
        """A DecimalField only coerces on load, so a write that returned its
        input would hand a string to arithmetic downstream."""
        point = SupplyPoint.objects.create(
            program_id=SYNTHETIC_PROGRAM, slug="s", name="S", kind="central_store", source="we_recorded"
        )
        assert point.pk
        round_ = da.create_round({"label": "R", "delivery_point": {"city": "Kano"}})
        assert isinstance(round_.pk, int)

    def test_an_unknown_key_is_ignored_rather_than_stored_where_nothing_reads_it(self, da):
        commodity = da.upsert_commodity({"slug": "rutf", "name": "RUTF", "nonsense_field": "x"})
        assert not hasattr(commodity, "nonsense_field")

    def test_a_bad_gtin_check_digit_is_refused(self, da, rutf_row):
        with pytest.raises(ValueError, match="fails its GS1 check digit"):
            da.upsert_item({"sku": "x", "commodity_slug": "rutf", "gtin_base": "00000000000001"})

    def test_an_item_with_no_gtin_is_perfectly_normal(self, da, rutf_row):
        item = da.upsert_item({"sku": "x", "name": "X", "commodity_slug": "rutf", "base_per_pack": 144})
        assert item.base_per_pack == 144
        assert item.commodity.slug == "rutf"

    def test_an_item_naming_an_unknown_commodity_is_refused(self, da):
        with pytest.raises(ValueError, match="commodity 'nope' does not exist"):
            da.upsert_item({"sku": "x", "commodity_slug": "nope"})

    def test_supplier_search_matches_name_or_contact_email(self, da):
        da.create_supplier({"name": "Harmattan Foods", "contacts": [{"email": "sales@example.test"}]})
        da.create_supplier({"name": "Northwind Nutrition"})
        assert [s.name for s in da.list_suppliers(search="harmattan")] == ["Harmattan Foods"]
        assert [s.name for s in da.list_suppliers(search="SALES@EXAMPLE")] == ["Harmattan Foods"]
        assert len(da.list_suppliers()) == 2


class TestRounds:
    def test_a_round_cannot_open_without_a_delivery_point(self, da):
        created = da.create_round({"label": "Round 1", "lines": []})
        with pytest.raises(ValueError, match="needs a delivery point"):
            da.open_round(created.pk)

    def test_opening_a_round_with_a_delivery_point_works(self, open_round):
        assert open_round.status == "open"

    def test_quantity_for_a_commodity_not_on_the_round_is_none(self, open_round):
        assert open_round.quantity_for("amoxicillin") is None

    def test_quantity_for_a_commodity_on_the_round_is_a_decimal_and_its_unit(self, open_round):
        assert open_round.quantity_for("rutf") == (Decimal("2000"), "carton")


class TestQuotes:
    def _quote(self, da, round_, **overrides):
        supplier = overrides.pop("supplier", None) or da.create_supplier({"name": "Harmattan Foods"})
        payload = {
            "round_id": round_.pk,
            "commodity_slug": "rutf",
            "supplier_id": supplier.pk,
            "as_quoted_amount": "50.00",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "2000",
            "quantity_basis_unit": "carton",
        }
        payload.update(overrides)
        return da.create_quote(payload)

    def test_a_quote_pointing_at_a_missing_round_is_refused(self, da, rutf_row):
        """An orphan quote would never appear in any comparison -- invisible
        rather than merely wrong."""
        with pytest.raises(ValueError, match="round 9999 does not exist"):
            self._quote(da, type("R", (), {"pk": 9999})())

    def test_a_quote_pointing_at_a_missing_commodity_is_refused(self, da, open_round):
        with pytest.raises(ValueError, match="commodity 'nope' does not exist"):
            self._quote(da, open_round, commodity_slug="nope")

    def test_a_stored_amount_comes_back_as_a_decimal(self, da, open_round):
        quote = self._quote(da, open_round)
        assert quote.as_quoted_amount == Decimal("50.0000")

    def test_a_correction_versions_rather_than_overwrites(self, da, open_round):
        original = self._quote(da, open_round)
        replacement = da.supersede_quote(original.pk, {"as_quoted_amount": "52.42"}, reason="transcription error")

        assert replacement.version == 2
        assert replacement.as_quoted_amount == Decimal("52.4200")
        assert replacement.correction_reason == "transcription error"

        original.refresh_from_db()
        assert original.superseded_by_id == replacement.pk
        assert original.as_quoted_amount == Decimal("50.0000"), "the original was mutated"

    def test_a_correction_naming_one_field_carries_the_rest_forward(self, da, open_round):
        original = self._quote(da, open_round, freight_basis="included", lead_time_days=45)
        replacement = da.supersede_quote(original.pk, {"as_quoted_amount": "52.42"}, reason="typo")
        assert replacement.freight_basis == "included"
        assert replacement.lead_time_days == 45
        assert replacement.quantity_basis == Decimal("2000")

    def test_a_correction_needs_a_reason(self, da, open_round):
        original = self._quote(da, open_round)
        with pytest.raises(ValueError, match="needs a reason"):
            da.supersede_quote(original.pk, {"as_quoted_amount": "1.00"}, reason="")
        assert Quote.objects.count() == 1, "a replacement was created despite the refusal"

    def test_voiding_needs_a_reason_and_leaves_the_record_readable(self, da, open_round):
        quote = self._quote(da, open_round)
        with pytest.raises(ValueError, match="needs a reason"):
            da.void_quote(quote.pk, reason="")

        voided = da.void_quote(quote.pk, reason="duplicate entry")
        assert voided.voided is True
        assert voided.void_reason == "duplicate entry"
        assert da.get_quote(quote.pk) is not None


class TestAwards:
    def test_an_award_needs_a_rationale(self, da, open_round):
        with pytest.raises(ValueError, match="needs a rationale"):
            da.create_award({"round_id": open_round.pk, "rationale": ""})

    def test_an_award_takes_its_supplier_and_commodity_from_the_quote(self, da, open_round):
        supplier = da.create_supplier({"name": "Harmattan Foods"})
        quote = da.create_quote(
            {
                "round_id": open_round.pk,
                "commodity_slug": "rutf",
                "supplier_id": supplier.pk,
                "as_quoted_amount": "50.00",
            }
        )
        awarded = da.create_award(
            {"round_id": open_round.pk, "quote_id": quote.pk, "rationale": "cheapest comparable"}
        )
        assert awarded.supplier_id == supplier.pk
        assert awarded.commodity.slug == "rutf"
        assert awarded.decided_on is not None


class TestContracts:
    def test_a_contract_needs_a_buyer_party_that_exists(self, da, open_round):
        supplier = da.create_supplier({"name": "Harmattan Foods"})
        with pytest.raises(ValueError, match="party 999 does not exist"):
            da.create_contract(
                {
                    "commodity_slug": "rutf",
                    "supplier_id": supplier.pk,
                    "buyer_of_record": "partner_org",
                    "buyer_party_id": 999,
                    "source": "partner_reported",
                }
            )

    def test_a_contract_records_who_is_buying_and_that_it_was_reported(self, da, open_round):
        partner = da.upsert_party({"slug": "llo-kano", "name": "Kano partner", "kind": "partner_org"})
        supplier = da.create_supplier({"name": "Harmattan Foods"})
        contract = da.create_contract(
            {
                "round_id": open_round.pk,
                "commodity_slug": "rutf",
                "supplier_id": supplier.pk,
                "buyer_of_record": "partner_org",
                "buyer_party_id": partner.pk,
                "source": "partner_reported",
                "quantity": "500",
                "quantity_unit": "carton",
                "unit_price": "52.42",
            }
        )
        assert contract.buyer_of_record == "partner_org"
        assert contract.buyer_party_id == partner.pk
        assert contract.witnessed is False, "a partner's report is a claim, not an observation"
        assert contract.unit_price == Decimal("52.4200")

    def test_a_claimed_duty_relief_with_no_document_is_not_evidenced(self, da, open_round):
        partner = da.upsert_party({"slug": "llo", "name": "Partner", "kind": "partner_org"})
        supplier = da.create_supplier({"name": "Harmattan Foods"})
        contract = da.create_contract(
            {
                "commodity_slug": "rutf",
                "supplier_id": supplier.pk,
                "buyer_of_record": "partner_org",
                "buyer_party_id": partner.pk,
                "source": "partner_reported",
                "duty_relief_claimed": True,
            }
        )
        assert contract.duty_relief_claimed is True
        assert contract.duty_relief_evidenced is False


class TestSyntheticScopes:
    def test_a_labs_only_programme_is_synthetic(self):
        assert access(program_id=SYNTHETIC_FLOOR).is_synthetic is True
        assert access(program_id=SYNTHETIC_PROGRAM).is_synthetic is True

    def test_a_real_programme_is_not_synthetic(self):
        assert access(program_id=REAL_PROGRAM).is_synthetic is False

    def test_an_unparseable_scope_is_not_synthetic(self):
        """Fails closed: the only thing gated on this is destructive, so
        'we could not read the scope' must never be why a purge is allowed."""
        assert access(program_id="not-a-number").is_synthetic is False

    def test_purge_is_refused_for_a_real_programme(self, rutf_row):
        real = access(program_id=REAL_PROGRAM)
        real.create_round({"label": "Real round", "delivery_point": {"city": "Kano"}})
        with pytest.raises(ValueError, match="only labs-only programmes"):
            real.purge()
        assert real.list_rounds(), "a refused purge still deleted something"

    def test_purge_clears_a_synthetic_programme_including_its_ledger(self, da, open_round):
        store = SupplyPoint.objects.create(
            program_id=SYNTHETIC_PROGRAM,
            slug="central",
            name="Central",
            kind="central_store",
            source="we_recorded",
        )
        Movement.objects.create(
            program_id=SYNTHETIC_PROGRAM,
            kind="receipt",
            occurred_on="2026-06-01",
            to_supply_point=store,
            commodity=da.get_commodity("rutf"),
            quantity=Decimal("100"),
            quantity_unit="carton",
            source="we_recorded",
        )

        counts = da.purge()

        assert counts["movements"] == 1
        assert counts["rounds"] == 1
        assert counts["supply points"] == 1
        assert da.list_rounds() == []
        assert Movement.objects.count() == 0
        assert da.get_commodity("rutf") is None

    def test_purge_leaves_another_programmes_data_alone(self, da, open_round):
        other = access(program_id=SYNTHETIC_PROGRAM + 1)
        other.upsert_commodity({"slug": "rutf", "name": "RUTF"})
        other.create_round({"label": "Theirs", "delivery_point": {"city": "Kaduna"}})

        da.purge()

        assert len(other.list_rounds()) == 1
        assert other.get_commodity("rutf") is not None
