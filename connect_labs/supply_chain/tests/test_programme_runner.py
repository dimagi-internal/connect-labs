"""Every organisation runs its own programme; none acts on another's (#2458).

The properties in docs/superpowers/specs/2026-10-12-supply-running-organisation-design.md
§5 and §8, as tests. Most of them are refusals: what an outsider must not be able to do
in a programme it does not run, however it can see it.

THIS REPOSITORY IS PUBLIC. Every organisation and person here is invented.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from connect_labs.labs.access.scopes import SYSTEM, Caller
from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.models import OrgMembership
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.runner import OPERATOR, PARTNER, PROGRAMME, declare, dimagi_org, standing

pytestmark = [pytest.mark.django_db, pytest.mark.real_standing]

KILELE_PROGRAM = 10901
DIMAGI_PROGRAM = 10902
UNDECLARED = 10903


def _user(username, email, **flags):
    return get_user_model().objects.create(username=username, email=email, **flags)


def _labs_only_org(slug, name):
    return LabsOrg.objects.create(slug=f"labs-synthetic-{slug}", name=name, labs_only=True)


def _access(user, program_id):
    """A non-system caller who holds the programme (as visibility grants it)."""
    with patch("connect_labs.supply_chain.data_access.may_use", return_value=None):
        access = SupplyDataAccess(program_id=program_id, user=user, caller=Caller(user=user))
    return access


def op(access, name, **payload):
    return call_operation(name, access, payload)


@pytest.fixture
def kilele():
    org = _labs_only_org("kilele", "Kilele Community Health")
    declare(KILELE_PROGRAM, org)
    return org


@pytest.fixture
def kilele_admin(kilele):
    user = _user("dev-kilele-admin", "admin@kilele.example")
    OrgMembership.objects.create(org=kilele, user=user, role="admin")
    return user


@pytest.fixture
def outsider():
    """A member of another organisation who can see Kilele's programme (labs-only visibility)."""
    other = _labs_only_org("baobab", "Baobab Trust")
    user = _user("dev-baobab", "staff@baobab.example")
    OrgMembership.objects.create(org=other, user=user, role="admin")
    return user


@pytest.fixture(autouse=True)
def no_connect():
    """Nobody here is a Connect member of anything: membership is by grant or not at all."""
    with patch("connect_labs.supply_chain.identity._caller_organizations", return_value=[]):
        yield


class TestStanding:
    def test_a_member_of_the_running_organisation_is_the_programme(self, kilele, kilele_admin):
        held = standing(_access(kilele_admin, KILELE_PROGRAM))
        assert (held.role, held.runner) == (PROGRAMME, kilele)

    def test_a_member_of_another_organisation_is_a_partner(self, kilele, outsider):
        assert standing(_access(outsider, KILELE_PROGRAM)).role == PARTNER

    def test_a_dimagi_address_is_a_partner_where_dimagi_does_not_run(self, kilele):
        dimagi_person = _user("lead", "lead@dimagi.com")
        assert standing(_access(dimagi_person, KILELE_PROGRAM)).role == PARTNER

    def test_a_dimagi_address_is_the_programme_where_dimagi_runs(self):
        declare(DIMAGI_PROGRAM, dimagi_org())
        dimagi_person = _user("lead", "lead@dimagi.com")
        assert standing(_access(dimagi_person, DIMAGI_PROGRAM)).role == PROGRAMME

    def test_staff_are_support_not_the_buyer(self, kilele):
        support = _user("support", "support@dimagi.com", is_staff=True)
        held = standing(_access(support, KILELE_PROGRAM))
        assert (held.role, held.runner) == (OPERATOR, kilele)

    def test_an_undeclared_programme_has_no_programme_but_support(self):
        dimagi_person = _user("lead", "lead@dimagi.com")
        assert standing(_access(dimagi_person, UNDECLARED)).role == PARTNER
        support = _user("support", "support@dimagi.com", is_staff=True)
        assert standing(_access(support, UNDECLARED)).role == OPERATOR

    def test_a_membership_carried_by_a_merge_is_not_a_grant(self, kilele):
        user = _user("dev-merged", "someone@elsewhere.example")
        merged = OrgMembership.objects.create(org=_labs_only_org("gone", "Gone"), user=user, role="admin")
        OrgMembership.objects.filter(pk=merged.pk).update(org=kilele)  # what org_merge does
        assert standing(_access(user, KILELE_PROGRAM)).role == PARTNER

    def test_no_membership_row_makes_anyone_dimagi(self):
        declare(DIMAGI_PROGRAM, dimagi_org())
        user = _user("dev-intruder", "someone@elsewhere.example")
        OrgMembership.objects.create(org=dimagi_org(), user=user, role="admin")
        assert standing(_access(user, DIMAGI_PROGRAM)).role == PARTNER

    def test_seeing_a_labs_only_organisation_is_not_belonging_to_it(self, kilele, outsider):
        seen = [{"id": kilele.slug, "slug": kilele.slug, "labs_only": True}]
        with patch("connect_labs.supply_chain.identity._caller_organizations", return_value=seen):
            assert standing(_access(outsider, KILELE_PROGRAM)).role == PARTNER


class TestWrites:
    def test_the_running_organisation_tenders_for_itself(self, kilele, kilele_admin):
        tender = op(_access(kilele_admin, KILELE_PROGRAM), "tender_create", data={"label": "Chlorine tablets"})
        assert tender["id"]

    def test_an_outsider_who_can_see_the_programme_cannot_write_to_it(self, kilele, outsider):
        with pytest.raises(PermissionDenied, match="Kilele Community Health"):
            op(_access(outsider, KILELE_PROGRAM), "tender_create", data={"label": "Not theirs"})

    def test_a_write_that_takes_provenance_is_refused_too(self, kilele, outsider):
        """Provenance is not an authorisation boundary: a payment carries it, and is still not theirs."""
        payment = {
            "contract_id": 1,
            "paid_on": "2026-10-01",
            "amount": "1",
            "currency": "KES",
            "source": "we_recorded",
        }
        with pytest.raises(PermissionDenied):
            op(_access(outsider, KILELE_PROGRAM), "payment_record", data=payment)

    def test_the_system_caller_is_unchanged(self, kilele):
        access = SupplyDataAccess(program_id=KILELE_PROGRAM, caller=SYSTEM)
        assert op(access, "tender_create", data={"label": "Seeded"})["id"]

    def test_the_running_organisation_records_first_hand_as_itself(self, kilele, kilele_admin):
        """What an LLO's own staff saw is `we_recorded` -- before #2458 only Dimagi could say so."""
        from connect_labs.supply_chain.models import SupplyPoint

        access = _access(kilele_admin, KILELE_PROGRAM)
        point = op(access, "supply_point_upsert", data={
            "slug": "store", "name": "Store", "kind": "central_store", "source": "we_recorded",
        })  # fmt: skip
        row = SupplyPoint.objects.get(pk=point["id"])
        assert (row.source, row.recorded_by_org_id) == ("we_recorded", kilele.pk)

    def test_the_running_organisation_may_record_a_partners_word_on_its_behalf(self, kilele, kilele_admin):
        from connect_labs.supply_chain.models import SupplyPoint

        partner = LabsOrg.objects.create(slug="kilifi-dispensary", name="Kilifi Dispensary")
        point = op(_access(kilele_admin, KILELE_PROGRAM), "supply_point_upsert", data={
            "slug": "dispensary", "name": "Dispensary", "kind": "facility", "source": "partner_reported",
            "recorded_by_org_id": partner.pk,
        })  # fmt: skip
        assert SupplyPoint.objects.get(pk=point["id"]).recorded_by_org_id == partner.pk

    def test_support_acts_for_the_running_organisation(self, kilele):
        from connect_labs.supply_chain.models import SupplyPoint

        support = _user("support", "support@dimagi.com", is_staff=True)
        point = op(_access(support, KILELE_PROGRAM), "supply_point_upsert", data={
            "slug": "store", "name": "Store", "kind": "central_store", "source": "partner_reported",
        })  # fmt: skip
        assert SupplyPoint.objects.get(pk=point["id"]).recorded_by_org_id == kilele.pk


class TestWhoDeclares:
    def test_not_even_the_running_organisation_reassigns_its_programme(self, kilele, kilele_admin, outsider):
        other = LabsOrg.objects.get(slug="labs-synthetic-baobab")
        with pytest.raises(PermissionDenied):
            op(_access(kilele_admin, KILELE_PROGRAM), "programme_runner_set", org_id=other.pk)

    def test_support_declares(self, kilele):
        support = _user("support", "support@dimagi.com", is_staff=True)
        result = op(_access(support, UNDECLARED), "programme_runner_set", org_id=kilele.pk)
        assert result["org_id"] == kilele.pk

    def test_another_organisation_is_not_declared_on_a_programme_open_to_every_domain(self, kilele):
        from connect_labs.labs.synthetic.provisioning import register_labs_only_opp

        register_labs_only_opp(opportunity_id=UNDECLARED, label="Open", program_id=UNDECLARED, allowed_domains=[])
        support = _user("support", "support@dimagi.com", is_staff=True)
        with pytest.raises(ValueError, match="allowed domains"):
            op(_access(support, UNDECLARED), "programme_runner_set", org_id=kilele.pk)
        # Dimagi's own invented programmes stay open, as they always were.
        assert op(_access(support, UNDECLARED), "programme_runner_set", org_id=dimagi_org().pk)["org_name"] == "Dimagi"

    def test_it_is_declared_once_the_programme_names_its_domains(self, kilele):
        from connect_labs.labs.synthetic.provisioning import register_labs_only_opp

        register_labs_only_opp(
            opportunity_id=UNDECLARED, label="Kilele", program_id=UNDECLARED, allowed_domains=["@kilele.example"]
        )
        support = _user("support", "support@dimagi.com", is_staff=True)
        assert op(_access(support, UNDECLARED), "programme_runner_set", org_id=kilele.pk)["org_id"] == kilele.pk

    def test_declaring_revokes_the_links_its_new_runner_already_held(self, kilele):
        from django.utils import timezone

        from connect_labs.supply_chain.update_links.models import UpdateLink

        def link(program_id, org, token):
            return UpdateLink.objects.create(
                program_id=program_id,
                org=org,
                token_hash=token,
                expires_at=timezone.now() + timezone.timedelta(days=30),
            )

        held = link(UNDECLARED, kilele, "a" * 64)
        elsewhere = link(KILELE_PROGRAM + 100, kilele, "b" * 64)
        support = _user("support", "support@dimagi.com", is_staff=True)
        op(_access(support, UNDECLARED), "programme_runner_set", org_id=kilele.pk)
        held.refresh_from_db()
        elsewhere.refresh_from_db()
        assert (held.state, held.revoked_by_id) == ("revoked", support.pk)
        assert elsewhere.state == "active"

    def test_anyone_who_holds_it_can_read_who_runs_it(self, kilele, outsider):
        got = op(_access(outsider, KILELE_PROGRAM), "programme_runner_get")
        assert (got["org_name"], got["your_standing"]) == ("Kilele Community Health", PARTNER)


class TestARunningOrganisationCannotBeTakenOver:
    def test_it_takes_no_marketplace_invitation(self, kilele):
        dimagi_org()
        declare(DIMAGI_PROGRAM, dimagi_org())
        access = SupplyDataAccess(program_id=KILELE_PROGRAM, caller=SYSTEM)
        supplier = op(access, "supplier_create", data={"org_id": dimagi_org().pk})
        with pytest.raises(ValueError, match="runs a program"):
            op(access, "supplier_market_invite", supplier_id=supplier["id"])

    def test_a_supplier_edit_cannot_re_key_it(self, kilele, kilele_admin):
        access = _access(kilele_admin, KILELE_PROGRAM)
        supplier = op(access, "supplier_create", data={"org_id": dimagi_org().pk})
        with pytest.raises(PermissionDenied, match="platform support"):
            op(access, "supplier_update", supplier_id=supplier["id"], data={"connect_organization_id": 4242})
        assert dimagi_org().connect_organization_id is None

    def test_its_own_admin_cannot_rewrite_its_record(self, kilele, kilele_admin):
        from connect_labs.supply_chain.org_access import require_change

        with pytest.raises(PermissionDenied, match="platform support"):
            require_change(_access(kilele_admin, KILELE_PROGRAM), kilele)

    def test_no_update_link_is_issued_in_its_name(self, kilele, kilele_admin):
        with pytest.raises(ValueError, match="runs this program"):
            op(_access(kilele_admin, KILELE_PROGRAM), "update_link_issue", data={"org_id": kilele.pk,
               "coverage": "organisation"})  # fmt: skip


class TestTheListing:
    def test_seeing_the_programme_does_not_manage_its_listing(self, kilele, outsider, kilele_admin, rf):
        from connect_labs.supply_chain.market.service import can_manage
        from connect_labs.supply_chain.models import Tender

        tender = Tender.objects.create(program_id=KILELE_PROGRAM, label="Chlorine tablets")
        request = rf.get("/")
        request.user = outsider
        assert not can_manage(request, tender)
        request.user = kilele_admin
        assert can_manage(request, tender)
