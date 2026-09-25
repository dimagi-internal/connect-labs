"""The supplier marketplace's rules, over the real database.

THIS REPOSITORY IS PUBLIC. Every company, product and price here is invented.

Mostly refusals, because the market's job is to let a stranger in without
letting them see or touch anything that is not theirs: a private round, a
closed one, or another supplier's bid.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace import membership
from connect_labs.marketplace.models import OrgMembership
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.market import service
from connect_labs.supply_chain.models import Outreach, Quote, Round, Supplier, SupplierOffering, SupplierProfile
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10501


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
            "unicef_material_number": "S0000240",
        },
    )


def make_round(label="Round 2", *, visibility="public", open_it=True):
    made = op(
        "round_create",
        data={
            "label": label,
            "delivery_point": {"name": "Central store", "city": "Kano"},
            "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
        },
    )
    Round.objects.filter(pk=made["id"]).update(visibility=visibility)
    if open_it:
        op("round_open", round_id=made["id"])
    return Round.objects.get(pk=made["id"])


def supplier_org(name, *, profile=True):
    org = LabsOrg.objects.create(slug=name.lower().replace(" ", "-"), name=name)
    if profile:
        SupplierProfile.objects.create(org=org, type="manufacturer")
    return org


BID = {
    "as_quoted_amount": "52.40",
    "as_quoted_unit": "per_pack",
    "as_quoted_currency": "USD",
    "quantity_basis": "2000",
}


class TestWhoSeesWhichRounds:
    def test_an_anonymous_visitor_sees_open_public_rounds_only(self, rutf):
        public = make_round("Public")
        make_round("Private", visibility="private")
        make_round("Draft", open_it=False)

        assert [r.round.pk for r in service.listed_rounds()] == [public.pk]

    def test_a_private_round_is_not_found_by_those_not_invited(self, rutf):
        private = make_round("Private", visibility="private")

        with pytest.raises(service.NotAvailable):
            service.visible_round(private.pk)
        with pytest.raises(service.NotAvailable):
            service.visible_round(private.pk, [supplier_org("Uninvited Foods")])

    def test_an_invited_organisation_sees_the_private_round(self, rutf):
        private = make_round("Private", visibility="private")
        org = supplier_org("Invited Foods")
        link = Supplier.objects.enrol(f"prog:{PROGRAM}", org=org)
        Outreach.objects.create(round=private, supplier=link)

        listed = service.visible_round(private.pk, [org])

        assert listed.invited
        assert [r.round.pk for r in service.listed_rounds([org])] == [private.pk]

    def test_a_closed_round_is_gone_from_the_market(self, rutf):
        round_ = make_round()
        op("round_close", round_id=round_.pk)

        with pytest.raises(service.NotAvailable):
            service.visible_round(round_.pk)


class TestBidding:
    def test_a_bid_is_the_suppliers_own_quote_in_the_programs_comparison(self, rutf, django_user_model):
        round_ = make_round()
        org = supplier_org("Plateau Foods")
        user = django_user_model.objects.create_user(username="ada", password="x")

        quote = service.bid(round_.pk, "rutf", org=org, orgs=[org], user=user, data=BID)

        assert quote.entered_by == "supplier"
        assert quote.entered_by_user == user
        supplier = quote.supplier
        assert supplier.org == org
        assert supplier.origin == "self_registered"
        assert supplier.awaiting_review
        compared = op("round_compare", round_id=round_.pk, commodity_slug="rutf")
        (row,) = (r for r in compared["comparable"] + compared["blocked"] if r["quote_id"] == quote.pk)
        assert row["entered_by"] == "supplier"
        assert row["supplier_awaiting_review"] is True

    def test_a_supplier_the_program_already_knows_keeps_its_row(self, rutf):
        round_ = make_round()
        org = supplier_org("Plateau Foods")
        known = Supplier.objects.enrol(f"prog:{PROGRAM}", org=org, status="contacted")

        quote = service.bid(round_.pk, "rutf", org=org, orgs=[org], user=None, data=BID)

        assert quote.supplier_id == known.pk
        assert not Supplier.objects.get(pk=known.pk).awaiting_review

    def test_no_bid_without_a_supplier_profile(self, rutf):
        round_ = make_round()
        org = supplier_org("No Profile Ltd", profile=False)

        with pytest.raises(service.NeedsProfile):
            service.bid(round_.pk, "rutf", org=org, orgs=[org], user=None, data=BID)

    def test_no_bid_for_an_organisation_you_do_not_act_for(self, rutf):
        round_ = make_round()
        theirs = supplier_org("Theirs Ltd")

        with pytest.raises(service.NotAvailable):
            service.bid(round_.pk, "rutf", org=theirs, orgs=[supplier_org("Mine Ltd")], user=None, data=BID)

    def test_no_bid_on_a_private_round_you_were_not_invited_to(self, rutf):
        round_ = make_round(visibility="private")
        org = supplier_org("Plateau Foods")

        with pytest.raises(service.NotAvailable):
            service.bid(round_.pk, "rutf", org=org, orgs=[org], user=None, data=BID)
        assert not Quote.objects.exists()

    def test_no_bid_on_a_product_the_round_is_not_asking_for(self, rutf):
        round_ = make_round()
        org = supplier_org("Plateau Foods")

        with pytest.raises(service.NotAvailable):
            service.bid(round_.pk, "f75", org=org, orgs=[org], user=None, data=BID)

    def test_no_bid_once_the_round_closes(self, rutf):
        round_ = make_round()
        org = supplier_org("Plateau Foods")
        op("round_close", round_id=round_.pk)

        with pytest.raises(service.NotAvailable):
            service.bid(round_.pk, "rutf", org=org, orgs=[org], user=None, data=BID)


class TestSealedBids:
    def test_a_supplier_sees_only_its_own_quotes(self, rutf):
        round_ = make_round()
        mine, theirs = supplier_org("Mine Ltd"), supplier_org("Theirs Ltd")
        service.bid(round_.pk, "rutf", org=mine, orgs=[mine], user=None, data=BID)
        service.bid(round_.pk, "rutf", org=theirs, orgs=[theirs], user=None, data=BID)

        own = service.own_quotes([mine])
        assert [o.quote.supplier.org for o in own] == [mine]
        assert [q.supplier.org for q in service.visible_round(round_.pk, [mine]).own_quotes] == [mine]

    def test_another_suppliers_quote_cannot_be_revised_or_withdrawn(self, rutf):
        round_ = make_round()
        mine, theirs = supplier_org("Mine Ltd"), supplier_org("Theirs Ltd")
        their_quote = service.bid(round_.pk, "rutf", org=theirs, orgs=[theirs], user=None, data=BID)

        with pytest.raises(service.NotAvailable):
            service.revise(their_quote.pk, org=mine, orgs=[mine], user=None, data={"as_quoted_amount": "1.00"})
        with pytest.raises(service.NotAvailable):
            service.withdraw(their_quote.pk, org=mine, orgs=[mine], user=None)
        assert not Quote.objects.get(pk=their_quote.pk).voided


class TestReviseAndWithdraw:
    def test_revising_writes_a_new_version_and_keeps_the_old(self, rutf):
        round_ = make_round()
        org = supplier_org("Plateau Foods")
        first = service.bid(round_.pk, "rutf", org=org, orgs=[org], user=None, data=BID)

        second = service.revise(first.pk, org=org, orgs=[org], user=None, data={"as_quoted_amount": "49.90"})

        assert second.pk != first.pk
        assert Quote.objects.get(pk=first.pk).superseded_by_id == second.pk
        assert second.entered_by == "supplier"
        assert [o.quote.pk for o in service.own_quotes([org])] == [second.pk]

    def test_withdrawing_voids_it(self, rutf):
        round_ = make_round()
        org = supplier_org("Plateau Foods")
        quote = service.bid(round_.pk, "rutf", org=org, orgs=[org], user=None, data=BID)

        service.withdraw(quote.pk, org=org, orgs=[org], user=None)

        assert Quote.objects.get(pk=quote.pk).voided
        with pytest.raises(service.NotAvailable):
            service.withdraw(quote.pk, org=org, orgs=[org], user=None)

    def test_a_closed_round_takes_no_revisions(self, rutf):
        round_ = make_round()
        org = supplier_org("Plateau Foods")
        quote = service.bid(round_.pk, "rutf", org=org, orgs=[org], user=None, data=BID)
        op("round_close", round_id=round_.pk)

        with pytest.raises(service.NotAvailable):
            service.revise(quote.pk, org=org, orgs=[org], user=None, data={"as_quoted_amount": "1.00"})


class TestWhatTheBuyerStillNeeds:
    def test_a_quote_missing_its_freight_basis_is_asked_for_it(self, rutf):
        round_ = make_round()
        org = supplier_org("Plateau Foods")
        service.bid(round_.pk, "rutf", org=org, orgs=[org], user=None, data=BID)

        (own,) = service.own_quotes([org])

        assert own.standing == "open"
        assert own.needs, "a price with no freight, duties or shelf life leaves the buyer questions"
        assert any("freight" in need.lower() for need in own.needs)


class TestWhatTheyOffer:
    def test_a_round_matching_an_offering_is_listed_first(self, rutf):
        op("commodity_upsert", data={"slug": "ors", "name": "ORS", "category": "oral_rehydration"})
        ors_round = op(
            "round_create",
            data={
                "label": "ORS round",
                "delivery_point": {"name": "Central store"},
                "lines": [{"commodity_slug": "ors", "quantity": "10", "quantity_unit": "carton"}],
            },
        )
        op("round_open", round_id=ors_round["id"])
        rutf_round = make_round("RUTF round")
        org = supplier_org("Plateau Foods")
        SupplierOffering.objects.create(
            profile=org.supplier_profile, category="therapeutic_food", product_name="Plateau RUTF"
        )

        listed = service.listed_rounds([org])

        assert listed[0].round.pk == rutf_round.pk
        assert listed[0].matches
        assert not listed[1].matches

    def test_an_exact_unicef_number_is_an_exact_match(self, rutf):
        from connect_labs.supply_chain.models import Commodity

        offering = SupplierOffering(category="", product_name="x", unicef_material_number="S0000240")

        assert service.offering_match_kind(offering, Commodity.objects.get(slug="rutf"), items=[]) == "exact"


class TestMembership:
    def test_an_invitation_makes_its_opener_a_member_once(self, django_user_model):
        org = supplier_org("Plateau Foods")
        invite, raw = membership.issue_invite(org, email="someone@plateau.example")
        user = django_user_model.objects.create_user(username="ada", password="x")

        found = membership.find_invite(raw)
        membership.accept_invite(found, user)

        assert OrgMembership.objects.get(org=org, user=user).role == "admin", "the first member is its admin"
        assert membership.find_invite(raw) is None, "an invitation is used once"

    def test_an_expired_or_unknown_invitation_is_nothing(self):
        org = supplier_org("Plateau Foods")
        invite, raw = membership.issue_invite(org)
        type(invite).objects.filter(pk=invite.pk).update(expires_at=timezone.now() - timedelta(minutes=1))

        assert membership.find_invite(raw) is None
        assert membership.find_invite("not-a-token") is None

    def test_orgs_for_joins_connect_and_local_membership(self, rf, django_user_model):
        user = django_user_model.objects.create_user(username="ada", password="x")
        connect_org = LabsOrg.objects.create(slug="sahel", name="Sahel Clinics", connect_organization_id=8101)
        local_org = supplier_org("Plateau Foods")
        supplier_org("Nobody's Foods")
        OrgMembership.objects.create(org=local_org, user=user)
        request = rf.get("/")
        request.user = user
        request.session = {"labs_oauth": {"organization_data": {"organizations": [{"id": 8101, "slug": "sahel"}]}}}

        assert {o.pk for o in membership.orgs_for(request)} == {connect_org.pk, local_org.pk}
