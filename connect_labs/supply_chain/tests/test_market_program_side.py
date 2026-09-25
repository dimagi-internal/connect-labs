"""The program team's side of the supplier marketplace.

THIS REPOSITORY IS PUBLIC. Every company, product and figure here is invented.
"""

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace import membership
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.market import service
from connect_labs.supply_chain.models import Supplier, SupplierOffering, SupplierProfile, Tender
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10501
SCOPE = f"prog:{PROGRAM}"


def op(name, **payload):
    return call_operation(name, SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM), payload)


@pytest.fixture
def rutf():
    return op(
        "commodity_upsert",
        data={"slug": "rutf", "name": "RUTF", "category": "therapeutic_food", "unicef_material_number": "S0000240"},
    )


def self_registered_bid(rutf_tender):
    org = LabsOrg.objects.create(slug="plateau-foods", name="Plateau Foods")
    SupplierProfile.objects.create(org=org)
    return service.bid(
        rutf_tender.pk,
        "rutf",
        org=org,
        orgs=[org],
        user=None,
        data={"as_quoted_amount": "50", "as_quoted_unit": "per_pack", "as_quoted_currency": "USD"},
    )


@pytest.fixture
def rutf_tender(rutf):
    made = op(
        "tender_create",
        data={
            "label": "R2",
            "delivery_point": {"name": "Central store"},
            "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
        },
    )
    op("tender_open", tender_id=made["id"])
    return Tender.objects.get(pk=made["id"])


class TestReviewingASelfRegisteredSupplier:
    def test_marking_reviewed_clears_the_flag_on_its_quotes(self, rutf_tender):
        quote = self_registered_bid(rutf_tender)
        before = op("tender_compare", tender_id=rutf_tender.pk, commodity_slug="rutf")
        assert all(r["supplier_awaiting_review"] for r in before["comparable"] + before["blocked"])

        reviewed = op("supplier_mark_reviewed", supplier_id=quote.supplier_id)

        assert reviewed["reviewed_on"] is not None
        after = op("tender_compare", tender_id=rutf_tender.pk, commodity_slug="rutf")
        assert not any(r["supplier_awaiting_review"] for r in after["comparable"] + after["blocked"])

    def test_a_supplier_the_team_added_is_never_flagged(self, rutf_tender):
        supplier = Supplier.objects.enrol(SCOPE, name="Harmattan Health Supplies")
        assert not supplier.awaiting_review


class TestInvitingAnExistingSupplier:
    def test_the_invitation_lets_its_opener_act_for_the_suppliers_company(self, django_user_model):
        supplier = Supplier.objects.enrol(SCOPE, name="Harmattan Health Supplies", type="distributor")

        issued = op("supplier_market_invite", supplier_id=supplier.pk, email="ops@harmattan.example")

        token = issued["path"].rstrip("/").rsplit("/", 1)[-1]
        invite = membership.find_invite(token)
        assert invite.org == supplier.org
        user = django_user_model.objects.create_user(username="bola", password="x")
        membership.accept_invite(invite, user)
        assert supplier.org in {m.org for m in user.org_memberships.all()}

    def test_the_link_is_rendered_once_and_never_redirected(self, client, django_user_model, monkeypatch):
        from connect_labs.supply_chain import form_views
        from connect_labs.supply_chain.api_views import _access as real_access

        supplier = Supplier.objects.enrol(SCOPE, name="Harmattan Health Supplies")
        client.force_login(django_user_model.objects.create_user(username="ngozi", password="x"))

        def scoped(request):
            access = real_access(request)
            access.program_id = PROGRAM
            return access

        monkeypatch.setattr(form_views, "has_program_context", lambda request: True)
        monkeypatch.setattr(form_views, "_access", scoped)
        from django.urls import reverse

        response = client.post(reverse("supply_chain:supplier_market_invite", args=[supplier.pk]), {"email": ""})

        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        assert "/supply/market/invites/" in response.content.decode()


class TestTenderVisibility:
    def test_a_tender_is_public_unless_made_private(self, rutf):
        made = op("tender_create", data={"label": "R", "delivery_point": {"name": "x"}})
        assert made["visibility"] == "public"

        updated = op("tender_update", tender_id=made["id"], data={"visibility": "private"})
        assert updated["visibility"] == "private"


class TestWhatSuppliersSayTheyOffer:
    def _offering(self, name, **fields):
        org = LabsOrg.objects.create(slug=name.lower().replace(" ", "-"), name=name)
        profile = SupplierProfile.objects.create(org=org)
        SupplierOffering.objects.create(profile=profile, product_name=f"{name} RUTF", **fields)
        return org

    def test_a_program_suppliers_offering_is_declared_evidence(self, rutf):
        org = self._offering("Plateau Foods", category="therapeutic_food")
        Supplier.objects.enrol(SCOPE, org=org)

        (claim,) = op("commodity_supply_base", commodity_slug="rutf")

        assert claim["basis"] == "declared"
        assert "not confirmed as this one" in claim["evidence"][0]["detail"]

    def test_an_exact_product_number_is_not_hedged(self, rutf):
        org = self._offering("Plateau Foods", category="", unicef_material_number="S0000240")
        Supplier.objects.enrol(SCOPE, org=org)

        (claim,) = op("commodity_supply_base", commodity_slug="rutf")

        assert claim["evidence"][0]["detail"] == "Plateau Foods RUTF"

    def test_market_offers_are_the_companies_not_yet_supplying_this_program(self, rutf):
        ours = self._offering("Plateau Foods", category="therapeutic_food")
        Supplier.objects.enrol(SCOPE, org=ours)
        self._offering("Sahel Nutrition", category="therapeutic_food")
        self._offering("Water Kits Ltd", category="diagnostic")

        offers = op("commodity_market_offers", commodity_slug="rutf")

        assert [o["org_name"] for o in offers] == ["Sahel Nutrition"]
        assert offers[0]["match"] == "category"


class TestAProgramCannotTakeOverACompany:
    def test_no_invitation_for_a_company_connect_governs(self):
        org = LabsOrg.objects.create(slug="sahel", name="Sahel Clinics", connect_organization_id=8801)
        supplier = Supplier.objects.enrol(SCOPE, org=org)

        with pytest.raises(ValueError, match="Connect organisation"):
            op("supplier_market_invite", supplier_id=supplier.pk)

    def test_no_invitation_once_its_own_people_are_on_the_marketplace(self, django_user_model):
        from connect_labs.marketplace.models import OrgMembership

        supplier = Supplier.objects.enrol(SCOPE, name="Harmattan Health Supplies")
        owner = django_user_model.objects.create_user(username="owner", password="x")
        OrgMembership.objects.create(org=supplier.org, user=owner, role="admin")

        with pytest.raises(ValueError, match="already has people"):
            op("supplier_market_invite", supplier_id=supplier.pk)
