"""The supplier marketplace in a browser.

THIS REPOSITORY IS PUBLIC. Every company, product and price here is invented.

Walks the two journeys the market exists for -- a new supplier registering
and bidding, an existing supplier invited in -- and pins what an anonymous
visitor may and may not reach.
"""

import re

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace import membership
from connect_labs.marketplace.models import OrgMembership
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Quote, Round, Supplier, SupplierOffering, SupplierProfile
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10501


def op(name, **payload):
    return call_operation(name, SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM), payload)


@pytest.fixture
def open_round():
    op(
        "commodity_upsert",
        data={"slug": "rutf", "name": "RUTF", "category": "therapeutic_food", "base_unit": "sachet"},
    )
    made = op(
        "round_create",
        data={
            "label": "RUTF round 2",
            "delivery_point": {"name": "Central store", "city": "Kano", "country_name": "Nigeria"},
            "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
        },
    )
    op("round_open", round_id=made["id"])
    return Round.objects.get(pk=made["id"])


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="ada", password="x", email="ada@plateau.example")


@pytest.fixture
def signed_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def supplier(user):
    org = LabsOrg.objects.create(slug="plateau-foods", name="Plateau Foods", country="NG")
    SupplierProfile.objects.create(org=org, type="manufacturer")
    OrgMembership.objects.create(org=org, user=user, role="admin")
    return org


class TestBrowsingIsPublic:
    def test_an_anonymous_visitor_sees_the_open_round(self, client, open_round):
        body = client.get(reverse("supply_chain:market")).content.decode()
        assert "RUTF round 2" in body
        assert "Sign in to bid" in body

        page = client.get(reverse("supply_chain:market_round", args=[open_round.pk]))
        assert page.status_code == 200
        assert "Central store" in page.content.decode()

    def test_a_round_whose_delivery_point_has_only_a_city_still_renders(self, client, open_round):
        """The live CHC round's delivery point has a city and no name. A template
        reading a missing key as a filter ARGUMENT raises, and took the page down."""
        Round.objects.filter(pk=open_round.pk).update(delivery_point={"city": "Kano"})

        assert client.get(reverse("supply_chain:market")).status_code == 200
        assert client.get(reverse("supply_chain:market_round", args=[open_round.pk])).status_code == 200

    def test_a_round_with_an_empty_delivery_point_still_renders(self, client, open_round):
        Round.objects.filter(pk=open_round.pk).update(delivery_point={})

        assert client.get(reverse("supply_chain:market")).status_code == 200
        assert client.get(reverse("supply_chain:market_round", args=[open_round.pk])).status_code == 200

    def test_a_private_round_is_a_404_to_an_anonymous_visitor(self, client, open_round):
        Round.objects.filter(pk=open_round.pk).update(visibility="private")

        assert "RUTF round 2" not in client.get(reverse("supply_chain:market")).content.decode()
        assert client.get(reverse("supply_chain:market_round", args=[open_round.pk])).status_code == 404

    def test_bidding_anonymously_sends_you_to_sign_in_and_back(self, client, open_round):
        bid_url = reverse("supply_chain:market_bid", args=[open_round.pk, "rutf"])

        response = client.get(bid_url)

        assert response.status_code == 302
        assert response.url.startswith(reverse("labs:login"))
        assert "next=" in response.url and "market" in response.url


class TestANewSupplier:
    def test_signing_in_without_an_organisation_leads_to_registering(self, signed_in, open_round):
        response = signed_in.get(reverse("supply_chain:market_bid", args=[open_round.pk, "rutf"]))
        assert response.status_code == 302
        assert response.url == reverse("supply_chain:market_register")

    def test_registering_makes_the_organisation_its_profile_and_you_its_admin(self, signed_in, user):
        response = signed_in.post(
            reverse("supply_chain:market_register"),
            {
                "name": "Sahel Nutrition Industries",
                "country": "ne",
                "type": "manufacturer",
                "contact_name": "Ada Bello",
                "contact_email": "sales@sahel-nutrition.example",
            },
        )

        assert response.status_code == 302
        org = LabsOrg.objects.get(name="Sahel Nutrition Industries")
        assert org.country == "NE"
        assert org.connect_organization_id is None
        assert org.supplier_profile.contacts == [{"name": "Ada Bello", "email": "sales@sahel-nutrition.example"}]
        assert OrgMembership.objects.get(org=org, user=user).role == "admin"

    def test_registering_an_organisation_already_on_file_is_refused(self, signed_in):
        LabsOrg.objects.create(slug="eha", name="EHA Clinics")

        response = signed_in.post(
            reverse("supply_chain:market_register"),
            {
                "name": "eha clinics",
                "country": "NG",
                "type": "distributor",
                "contact_name": "X",
                "contact_email": "x@example.org",
            },
        )

        assert response.status_code == 400
        assert "invitation" in response.content.decode()
        assert LabsOrg.objects.filter(name__iexact="EHA Clinics").count() == 1

    def test_a_bid_lands_as_the_suppliers_own_quote(self, signed_in, supplier, open_round, user):
        response = signed_in.post(
            reverse("supply_chain:market_bid", args=[open_round.pk, "rutf"]),
            {
                "as_quoted_amount": "52.40",
                "as_quoted_unit": "per_pack",
                "as_quoted_currency": "USD",
                "freight_basis": "not_specified",
                "duties_basis": "not_specified",
            },
        )

        assert response.status_code == 302, response.content.decode()[:2000]
        quote = Quote.objects.get()
        assert quote.entered_by == "supplier"
        assert quote.entered_by_user == user
        assert quote.supplier.org == supplier
        assert quote.pack_spec_source == "not_stated"
        bids = signed_in.get(reverse("supply_chain:market_bids")).content.decode()
        assert "RUTF round 2" in bids
        assert "The buyer still needs from you" in bids

    def test_an_offering_is_added_from_the_organisation_page(self, signed_in, supplier):
        response = signed_in.post(
            reverse("supply_chain:market_organisation") + f"?org={supplier.pk}",
            {
                "action": "offering_add",
                "org": supplier.pk,
                "category": "therapeutic_food",
                "product_name": "Plateau RUTF 92 g",
                "certifications_text": "WHO PQ, NAFDAC",
                "countries_served_text": "ng, ne",
            },
        )

        assert response.status_code == 302
        offering = SupplierOffering.objects.get()
        assert offering.certifications == ["WHO PQ", "NAFDAC"]
        assert offering.countries_served == ["NG", "NE"]


class TestAnExistingSupplier:
    def test_an_invitation_brings_a_colleague_in(self, client, django_user_model, open_round):
        org = LabsOrg.objects.create(slug="harmattan", name="Harmattan Health Supplies")
        Supplier.objects.enrol(f"prog:{PROGRAM}", org=org, type="distributor")
        _, raw = membership.issue_invite(org, email="ops@harmattan.example")
        url = reverse("supply_chain:market_invite", args=[raw])
        accept = reverse("supply_chain:market_invite_accept")

        opened = client.get(url)
        # The token goes no further than this request: not into the Location
        # header, and not into the sign-in link's next=.
        assert opened.status_code == 302
        assert opened.url == accept
        page = client.get(accept).content.decode()
        assert "Sign in with Connect" in page
        assert raw not in page

        colleague = django_user_model.objects.create_user(username="bola", password="x")
        client.force_login(colleague)
        assert client.post(accept).status_code == 302
        assert OrgMembership.objects.filter(org=org, user=colleague).exists()
        assert client.get(url).status_code == 404, "an invitation works once"
        assert client.post(accept).status_code == 404, "and cannot be accepted twice"

    def test_the_org_page_issues_an_invitation_link_once(self, signed_in, supplier):
        response = signed_in.post(
            reverse("supply_chain:market_organisation") + f"?org={supplier.pk}",
            {"action": "invite", "org": supplier.pk, "email": "new@plateau.example"},
        )

        body = response.content.decode()
        assert response["Cache-Control"] == "no-store"
        link = re.search(r"/supply/market/invites/([^/\"]+)/", body)
        assert link is not None
        assert membership.find_invite(link.group(1)) is not None


class TestNotYours:
    def test_you_cannot_withdraw_another_suppliers_bid(self, signed_in, supplier, open_round, django_user_model):
        from connect_labs.supply_chain.market import service

        theirs = LabsOrg.objects.create(slug="theirs", name="Theirs Ltd")
        SupplierProfile.objects.create(org=theirs)
        quote = service.bid(
            open_round.pk,
            "rutf",
            org=theirs,
            orgs=[theirs],
            user=None,
            data={"as_quoted_amount": "50", "as_quoted_unit": "per_pack", "as_quoted_currency": "USD"},
        )

        assert signed_in.post(reverse("supply_chain:market_withdraw", args=[quote.pk])).status_code == 404
        assert signed_in.get(reverse("supply_chain:market_revise", args=[quote.pk])).status_code == 404
        assert not Quote.objects.get(pk=quote.pk).voided


class TestTheReviewFixes:
    def test_a_quote_the_program_typed_in_is_not_the_suppliers_to_see_or_touch(self, signed_in, supplier, open_round):
        """Another program's transcription of this company's email stays that program's."""
        link = Supplier.objects.enrol(f"prog:{PROGRAM}", org=supplier)
        typed = op(
            "quote_record",
            data={
                "round_id": open_round.pk,
                "commodity_slug": "rutf",
                "supplier_id": link.pk,
                "as_quoted_amount": "47.00",
                "as_quoted_unit": "per_pack",
            },
        )

        assert "47.00" not in signed_in.get(reverse("supply_chain:market_bids")).content.decode()
        assert (
            "47.00" not in signed_in.get(reverse("supply_chain:market_round", args=[open_round.pk])).content.decode()
        )
        assert signed_in.post(reverse("supply_chain:market_withdraw", args=[typed["id"]])).status_code == 404
        assert not Quote.objects.get(pk=typed["id"]).voided

    def test_revising_can_clear_a_figure(self, signed_in, supplier, open_round):
        from connect_labs.supply_chain.market import service

        first = service.bid(
            open_round.pk,
            "rutf",
            org=supplier,
            orgs=[supplier],
            user=None,
            data={
                "as_quoted_amount": "52.40",
                "as_quoted_unit": "per_pack",
                "as_quoted_currency": "USD",
                "freight_basis": "excluded",
                "freight_amount": "9.00",
            },
        )

        response = signed_in.post(
            reverse("supply_chain:market_revise", args=[first.pk]),
            {
                "as_quoted_amount": "52.40",
                "as_quoted_unit": "per_pack",
                "as_quoted_currency": "USD",
                "freight_basis": "included",
                "freight_amount": "",
                "duties_basis": "not_specified",
            },
        )

        assert response.status_code == 302, response.content.decode()[:1500]
        revised = Quote.objects.get(pk=Quote.objects.get(pk=first.pk).superseded_by_id)
        assert revised.freight_basis == "included"
        assert revised.freight_amount is None

    def test_registering_a_differently_punctuated_name_is_refused(self, signed_in):
        LabsOrg.objects.create(slug="acme-ltd", name="Acme Ltd.")

        response = signed_in.post(
            reverse("supply_chain:market_register"),
            {"name": "ACME Ltd", "country": "NG", "type": "trader", "contact_name": "X", "contact_email": "x@e.org"},
        )

        assert response.status_code == 400
        assert LabsOrg.objects.count() == 1

    def test_opening_the_organisation_page_does_not_make_it_a_supplier(self, signed_in, user):
        org = LabsOrg.objects.create(slug="sahel", name="Sahel Clinics")
        OrgMembership.objects.create(org=org, user=user, role="admin")

        page = signed_in.get(reverse("supply_chain:market_organisation") + f"?org={org.pk}")

        assert page.status_code == 200
        assert "not a supplier on the marketplace yet" in page.content.decode()
        assert not SupplierProfile.objects.filter(org=org).exists()

    def test_a_member_who_is_not_an_admin_cannot_change_what_the_company_offers(
        self, client, supplier, django_user_model
    ):
        member = django_user_model.objects.create_user(username="kemi", password="x")
        OrgMembership.objects.create(org=supplier, user=member, role="member")
        client.force_login(member)

        response = client.post(
            reverse("supply_chain:market_organisation") + f"?org={supplier.pk}",
            {"action": "offering_add", "org": supplier.pk, "category": "therapeutic_food", "product_name": "X"},
        )

        assert response.status_code == 404
        assert not SupplierOffering.objects.exists()

    def test_a_malformed_offering_id_is_a_404_not_a_500(self, signed_in, supplier):
        response = signed_in.post(
            reverse("supply_chain:market_organisation") + f"?org={supplier.pk}",
            {"action": "offering_delete", "org": supplier.pk, "offering": "not-a-number"},
        )
        assert response.status_code == 404
