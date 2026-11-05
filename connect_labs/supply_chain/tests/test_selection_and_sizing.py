"""The selection email, a handover for each partner, the trade item offer, and the purchase sizing helper.

Sophie Feintuch's RUTF feedback: "Selection and handover", "Help working out how much to buy", and the
Catalogue note "offer to create it from the winning quote at award time".
THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented.
"""

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.services.sizing import size_purchase

pytestmark = pytest.mark.django_db

PROGRAM = 10773


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def world(da):
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "rutf",
            "name": "RUTF",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
            "course_definition": {"base_units_per_course": 168},
        },
    )
    supplier = op(
        da,
        "supplier_create",
        data={
            "name": "Quill Foods",
            "contacts": [
                {"name": "Jane Okoro", "email": "jane@quill.example", "phone": "+234 800 000 0000", "use": "to"},
                {"name": "Sales desk", "email": "sales@quill.example", "use": "cc"},
            ],
        },
    )
    orgs = [op(da, "org_upsert", data={"slug": f"p{i}", "name": f"Partner {i}"}) for i in (1, 2)]
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
                    "quantity_unit": "carton",
                    "delivery_point": {"name": "Partner 1 warehouse", "city": "Kano"},
                    "receiver_name": "Amina",
                    "receiver_contact": "Amina <amina@p1.example>",
                },
                {
                    "org_id": orgs[1]["id"],
                    "quantity": 600,
                    "quantity_unit": "carton",
                    "delivery_point": {"name": "Partner 2 warehouse", "city": "Sokoto"},
                    "buying_model": "programme_buys",
                },
            ],
        },
    )
    quote = op(
        da,
        "quote_record",
        data={
            "tender_id": tender["id"],
            "commodity_slug": "rutf",
            "supplier_id": supplier["id"],
            "as_quoted_amount": "52.40",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "1000",
            "quantity_basis_unit": "carton",
            "received_on": "2026-11-01",
            "lead_time_days": 60,
            "incoterm": "DAP",
            "base_per_pack_stated": 150,
            "base_unit_grams_stated": 92,
            "shelf_life_months_stated": 24,
        },
    )
    award = op(da, "award_create", tender_id=tender["id"], quote_id=quote["id"])
    return {"supplier": supplier, "orgs": orgs, "tender": tender, "quote": quote, "award": award}


class TestSelectionAndHandover:
    def test_the_selection_email_names_the_partners_and_copies_them(self, da, world):
        drafts = op(da, "selection_drafts_render", award_id=world["award"]["id"])["drafts"]
        selection = drafts[0]
        assert selection["kind"] == "selection" and selection["to"] == "jane@quill.example"
        assert set(selection["cc"]) == {"sales@quill.example", "amina@p1.example"}
        text = selection["text"]
        assert "Dear Jane" in text and "selected" in text
        assert "Partner 1: 400 cartons, delivered to Partner 1 warehouse, Kano" in text and "Amina" in text
        assert "Partner 2: 600 cartons" in text and "ordered and paid by us" in text
        assert "[your name]" in text  # no signed-in person here: a visible gap, not a bare sign-off

    @pytest.mark.parametrize(
        "name, username, email, signed",
        [
            ("Sophie Feintuch", "sfeintuch", "sfeintuch@dimagi.com", "Sophie Feintuch"),
            # A person whose profile holds no name but their login: signed, not left as a placeholder.
            ("sophie.feintuch", "sophie.feintuch", "sophie.feintuch@dimagi.com", "Sophie Feintuch"),
            ("", "hauwa_bello", "hauwa@example.org", "Hauwa Bello"),
            # A service account is never signed.
            ("ace", "ace", "ace@dimagi-ai.com", "[your name]"),
        ],
    )
    def test_the_emails_are_signed_by_the_person_viewing_them(
        self, django_user_model, world, name, username, email, signed
    ):
        user = django_user_model.objects.create(name=name, username=username, email=email)
        access = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM, user=user)
        drafts = op(access, "selection_drafts_render", award_id=world["award"]["id"])["drafts"]
        assert drafts and all(signed in d["text"] for d in drafts)
        if signed != "[your name]":
            assert all("[your name]" not in d["text"] for d in drafts)

    def test_each_partner_gets_a_handover_with_its_own_quantity_and_price(self, da, world):
        drafts = op(da, "selection_drafts_render", award_id=world["award"]["id"])["drafts"]
        handovers = [d for d in drafts if d["kind"] == "handover"]
        assert [h["label"] for h in handovers] == ["Hand over to Partner 1", "Hand over to Partner 2"]
        first = handovers[0]["text"]
        assert "jane@quill.example" in first and "+234 800 000 0000" in first
        assert "USD 52.40 per carton" in first and "400 cartons" in first and "USD 20,960.00" in first
        assert "Partner 1 warehouse, Kano" in first and "lead time 60 days" in first
        assert "place your order with the supplier" in first and handovers[0]["to"] == "amina@p1.example"
        assert "We will place and pay for this order" in handovers[1]["text"]

    def test_an_unsplit_tender_gets_only_the_selection_email(self, da):
        op(da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton"})
        supplier = op(da, "supplier_create", data={"name": "Solo Foods"})
        tender = op(
            da,
            "tender_create",
            data={
                "label": "Plain",
                "delivery_point": {"city": "Kano"},
                "lines": [{"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"}],
            },
        )
        quote = op(
            da,
            "quote_record",
            data={
                "tender_id": tender["id"],
                "commodity_slug": "rutf",
                "supplier_id": supplier["id"],
                "as_quoted_amount": "50",
                "as_quoted_unit": "per_pack",
                "quantity_basis": "500",
                "quantity_basis_unit": "carton",
            },
        )
        award = op(da, "award_create", tender_id=tender["id"], quote_id=quote["id"])
        drafts = op(da, "selection_drafts_render", award_id=award["id"])["drafts"]
        assert [d["kind"] for d in drafts] == ["selection"]
        assert "partner organisation" not in drafts[0]["text"]


class TestTheTradeItemOffer:
    def test_it_carries_only_what_the_supplier_stated(self, da, world):
        offer = op(da, "item_offer_from_quote", quote_id=world["quote"]["id"])
        assert offer["already_linked"] is False
        assert offer["item"]["base_per_pack"] == 150 and offer["item"]["base_unit_grams"] == 92
        assert offer["item"]["shelf_life_months"] == 24 and offer["item"]["manufacturer"] == "Quill Foods"
        assert "sku" not in offer["item"]

    def test_the_item_form_opens_pre_filled(self, da, world, client, monkeypatch):
        from connect_labs.supply_chain.models import Quote
        from connect_labs.supply_chain.procurement.services.selection import item_from_quote

        quote = Quote.objects.get(pk=world["quote"]["id"])
        initial = item_from_quote(quote, quote.supplier, quote.commodity)
        assert initial["commodity"] == quote.commodity.pk and initial["base_per_pack"] == 150


class TestPurchaseSizing:
    def test_target_cases_times_the_course_plus_a_buffer(self, da, world):
        got = op(
            da,
            "purchase_sizing",
            commodity_slug="rutf",
            partners=[
                {"org_id": world["orgs"][0]["id"], "target_cases": 500},
                {"org_id": world["orgs"][1]["id"], "target_cases": 100, "buffer_percent": 0},
            ],
        )
        # One carton (150 sachets) a child by default, not the measured or protocol course.
        assert got["course_basis"] == "pack_per_child"
        first, second = got["partners"]
        assert first["units"] == "82500" and first["packs"] == 550  # 500 x 150 x 1.10 = 82,500 sachets
        assert second["units"] == "15000" and second["packs"] == 100
        assert got["total_packs"] == 650
        assert "500 cases x 150 + 10% buffer = 82,500 base units = 550 packs of 150" in first["working"]

    def test_the_programme_can_change_the_default_packs_per_child(self, da, world):
        from connect_labs.scope_config.models import ScopeConfig
        from connect_labs.supply_chain.config import SUPPLY

        ScopeConfig.objects.create(
            scope_type="program", scope_key=str(da.program_id), namespace=SUPPLY.key, data={"purchase_packs_per_child": 0.5}
        )
        got = op(
            da,
            "purchase_sizing",
            commodity_slug="rutf",
            partners=[{"org_id": world["orgs"][0]["id"], "target_cases": 100, "buffer_percent": 0}],
        )
        assert got["partners"][0]["units"] == "7500" and got["course_basis"] == "pack_per_child"

    def test_a_product_with_no_pack_size_still_uses_its_protocol_course(self, da, world):
        op(
            da,
            "commodity_upsert",
            data={"slug": "zinc2", "name": "Zinc 2", "base_unit": "tablet", "course_definition": {"base_units_per_course": 10}},
        )
        got = op(
            da,
            "purchase_sizing",
            commodity_slug="zinc2",
            partners=[{"org_id": world["orgs"][0]["id"], "target_cases": 10, "buffer_percent": 0}],
        )
        assert got["course_basis"] == "protocol" and got["partners"][0]["units"] == "100"

    def test_a_given_course_overrides_and_is_said_so(self, da, world):
        got = op(
            da,
            "purchase_sizing",
            commodity_slug="rutf",
            partners=[{"org_id": world["orgs"][0]["id"], "target_cases": 100}],
            course_units=120,
            buffer_percent=20,
        )
        assert got["course_basis"] == "given" and got["partners"][0]["units"] == "14400"

    def test_no_course_anywhere_is_refused_not_guessed(self, da):
        op(da, "commodity_upsert", data={"slug": "zinc", "name": "Zinc"})
        org = op(da, "org_upsert", data={"slug": "px", "name": "Px"})
        with pytest.raises(ValueError, match="course"):
            op(da, "purchase_sizing", commodity_slug="zinc", partners=[{"org_id": org["id"], "target_cases": 1}])

    def test_the_arithmetic_alone(self):
        got = size_purchase([{"org_name": "A", "target_cases": 10}], 168, 10, 150)
        assert got["partners"][0]["units"] == "1848" and got["partners"][0]["packs"] == 13
        with pytest.raises(ValueError):
            size_purchase([{"target_cases": -1}], 168)


class TestAStoreWithNothingComingSaysWhy:
    def test_a_partner_that_has_not_ordered_its_share_is_named_with_a_link_to_the_award(self, da, world):
        from django.urls import reverse

        from connect_labs.labs.models import LabsOrg
        from connect_labs.supply_chain.models import SupplyPoint
        from connect_labs.supply_chain.plan.service import no_delivery_reasons

        partner = LabsOrg.objects.get(slug="p2")
        store = SupplyPoint.objects.create(
            program_id=PROGRAM, slug="p2-wh", name="Partner 2 warehouse", kind="central_store", managed_by_org=partner,
            source="we_recorded",
        )  # fmt: skip
        reasons = no_delivery_reasons(PROGRAM)
        assert reasons[store.pk]["text"] == "Partner 2 hasn't ordered yet"
        assert reasons[store.pk]["href"] == reverse("supply_chain:award_detail", args=[world["award"]["id"]])
