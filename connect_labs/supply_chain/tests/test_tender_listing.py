"""An organisation's own tender: its address, its brief, and who it invites.

THIS REPOSITORY IS PUBLIC. Every company and figure here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.models import OrgMembership
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.market import service
from connect_labs.supply_chain.models import SupplierProfile, Tender
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.tests.test_write_screens import scoped, user  # noqa: F401 -- fixtures

pytestmark = pytest.mark.django_db

PROGRAM = 10501


def op(name, **payload):
    return call_operation(name, SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM), payload)


@pytest.fixture
def publisher():
    return LabsOrg.objects.create(slug="sahel-health", name="Sahel Health Initiative")


@pytest.fixture
def listing(publisher):
    op("commodity_upsert", data={"slug": "rutf", "name": "RUTF", "category": "therapeutic_food"})
    made = op(
        "tender_create",
        data={
            "label": "RUTF for Sokoto, 2026",
            "delivery_points": [{"name": "Sokoto store", "city": "Sokoto"}],
            "lines": [{"commodity_slug": "rutf", "quantity": "800", "quantity_unit": "carton"}],
            "owner_org_id": publisher.pk,
            "slug": "RUTF Sokoto 2026",
            "brief": "Our second season in Sokoto.",
            "hue": "#0c8599",
            "visibility": "private",
        },
    )
    op("tender_open", tender_id=made["id"])
    return Tender.objects.get(pk=made["id"])


def supplier_org(name):
    org = LabsOrg.objects.create(slug=name.lower().replace(" ", "-"), name=name)
    SupplierProfile.objects.create(org=org)
    return org


class TestTheAddress:
    def test_the_slug_is_normalised_and_the_listing_lives_there(self, listing, client, publisher):
        assert listing.slug == "rutf-sokoto-2026"
        op("tender_update", tender_id=listing.pk, data={"visibility": "public"})

        page = client.get(reverse("supply_chain:market_tender_listing", args=["rutf-sokoto-2026"]))

        assert page.status_code == 200
        body = page.content.decode()
        assert "Our second season in Sokoto." in body
        assert "SAHEL HEALTH INITIATIVE" in body

    def test_an_address_already_taken_is_refused_by_name(self, listing):
        with pytest.raises(ValueError, match="already another tender"):
            op("tender_create", data={"label": "Another", "slug": "rutf-sokoto-2026"})

    def test_a_colour_outside_the_palette_is_refused(self, listing):
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            op("tender_update", tender_id=listing.pk, data={"hue": "#ff00ff"})


class TestRestrictedToTheInvited:
    def test_a_restricted_listing_is_a_404_to_those_not_invited(self, listing, client):
        assert client.get(reverse("supply_chain:market_tender_listing", args=[listing.slug])).status_code == 404
        with pytest.raises(service.NotAvailable):
            service.tender_by_slug(listing.slug, [supplier_org("Uninvited Foods")])

    def test_an_invited_supplier_sees_it_and_can_bid(self, listing):
        invited = supplier_org("Plateau Foods")
        op("tender_invite_org", tender_id=listing.pk, org_id=invited.pk)

        assert service.tender_by_slug(listing.slug, [invited]).invited
        quote = service.bid(
            listing.pk,
            "rutf",
            org=invited,
            orgs=[invited],
            user=None,
            data={"as_quoted_amount": "50", "as_quoted_unit": "per_pack", "as_quoted_currency": "USD"},
        )
        assert quote.tender_id == listing.pk

    def test_taking_them_off_the_list_hides_it_again(self, listing):
        invited = supplier_org("Plateau Foods")
        op("tender_invite_org", tender_id=listing.pk, org_id=invited.pk)
        op("tender_uninvite_org", tender_id=listing.pk, org_id=invited.pk)

        with pytest.raises(service.NotAvailable):
            service.tender_by_slug(listing.slug, [invited])

    def test_only_a_registered_supplier_can_be_invited(self, listing):
        not_a_supplier = LabsOrg.objects.create(slug="a-clinic", name="A Clinic")
        with pytest.raises(ValueError, match="not registered as a supplier"):
            op("tender_invite_org", tender_id=listing.pk, org_id=not_a_supplier.pk)


class TestManagingTheListing:
    def test_the_publishers_admin_invites_a_supplier(self, listing, publisher, client, django_user_model):
        admin = django_user_model.objects.create_user(username="amina", password="x")
        OrgMembership.objects.create(org=publisher, user=admin, role="admin")
        client.force_login(admin)
        invited = supplier_org("Plateau Foods")
        url = reverse("supply_chain:market_tender_manage", args=[listing.slug])

        assert client.get(url + "?q=plateau").status_code == 200
        assert client.post(url, {"action": "invite", "org": invited.pk}).status_code == 302
        assert list(listing.invited_orgs.all()) == [invited]

        client.post(url, {"action": "listing", "brief": "Updated brief.", "hue": "#5c940d", "visibility": "public"})
        listing.refresh_from_db()
        assert (listing.brief, listing.hue, listing.visibility) == ("Updated brief.", "#5c940d", "public")

    def test_anyone_else_finds_no_manage_page(self, listing, client, django_user_model):
        stranger = django_user_model.objects.create_user(username="kemi", password="x")
        client.force_login(stranger)
        url = reverse("supply_chain:market_tender_manage", args=[listing.slug])

        assert client.get(url).status_code == 404
        assert client.post(url, {"action": "invite", "org": supplier_org("Plateau Foods").pk}).status_code == 404
        assert not listing.invited_orgs.exists()

    def test_a_member_who_is_not_an_admin_cannot_manage(self, listing, publisher, client, django_user_model):
        member = django_user_model.objects.create_user(username="bola", password="x")
        OrgMembership.objects.create(org=publisher, user=member, role="member")
        client.force_login(member)

        assert client.get(reverse("supply_chain:market_tender_manage", args=[listing.slug])).status_code == 404


class TestTheProgramTeamsScreen:
    """The tender's own page is where the program team keeps the invited list."""

    def test_the_tender_page_invites_and_removes_a_supplier(self, listing, scoped):  # noqa: F811 -- the fixture
        invited = supplier_org("Plateau Foods")
        page = scoped.get(reverse("supply_chain:procurement_tender_detail", args=[listing.pk]))
        assert f'<option value="{invited.pk}">Plateau Foods</option>' in page.content.decode()

        scoped.post(reverse("supply_chain:procurement_tender_invite_org", args=[listing.pk]), {"org": invited.pk})
        assert list(listing.invited_orgs.all()) == [invited]

        scoped.post(reverse("supply_chain:procurement_tender_uninvite_org", args=[listing.pk]), {"org": invited.pk})
        assert not listing.invited_orgs.exists()

    def test_a_refusal_comes_back_on_the_tender_page(self, listing, scoped):  # noqa: F811 -- the fixture
        clinic = LabsOrg.objects.create(slug="a-clinic", name="A Clinic")
        response = scoped.post(
            reverse("supply_chain:procurement_tender_invite_org", args=[listing.pk]), {"org": clinic.pk}, follow=True
        )
        assert any("not registered as a supplier" in str(m) for m in response.context["messages"])
        assert not listing.invited_orgs.exists()
