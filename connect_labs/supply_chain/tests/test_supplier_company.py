"""A supplier is a company, linked into each program that buys from it.

THIS REPOSITORY IS PUBLIC. Every company, contact and id here is invented.

The company -- name, country, type, contacts, qualifications -- lives once on
the organisation and its supplier profile. A program's `Supplier` is its
relationship with that company: its own status and notes. These pin the
consequences: one company across programs, no duplicate within one, and
edits landing where the fact lives.
"""

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Supplier, SupplierProfile
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

CHC, RUTF = 10501, 10502


def access(program_id):
    return SupplyDataAccess(access_token="unused", program_id=program_id, caller=SYSTEM)


def create(program_id, **data):
    return call_operation("supplier_create", access(program_id), {"data": data})


class TestOneCompanyAcrossPrograms:
    def test_the_same_name_in_two_programs_is_one_company(self):
        first = create(CHC, name="Harmattan Distributors", type="distributor", country="NG")
        second = create(RUTF, name="Harmattan Distributors")

        assert first["id"] != second["id"]
        assert first["org_id"] == second["org_id"]
        assert LabsOrg.objects.filter(name="Harmattan Distributors").count() == 1
        # The second program sees what the first recorded about the company.
        assert second["type"] == "distributor"
        assert second["country"] == "NG"

    def test_linking_into_a_second_program_does_not_overwrite_the_company(self):
        create(CHC, name="Harmattan Distributors", city="Kano")
        create(RUTF, name="Harmattan Distributors", city="Lagos")

        assert SupplierProfile.objects.get(org__name="Harmattan Distributors").city == "Kano"

    def test_status_is_the_programs_own(self):
        chc = create(CHC, name="Harmattan Distributors", status="awarded")
        rutf = create(RUTF, name="Harmattan Distributors", status="contacted")

        assert chc["status"] == "awarded"
        assert rutf["status"] == "contacted"

    def test_a_similar_name_is_a_different_company(self):
        one = create(CHC, name="Nutriset")
        two = create(CHC, name="Nutriset Nigeria")

        assert one["org_id"] != two["org_id"]


class TestNoDuplicateInOneProgram:
    def test_creating_twice_returns_the_first(self):
        first = create(CHC, name="Harmattan Distributors")
        again = create(CHC, name="Harmattan Distributors")

        assert again["id"] == first["id"]
        assert Supplier.objects.filter(scope_key="prog:10501").count() == 1

    def test_supplier_list_is_this_programs(self):
        create(CHC, name="Harmattan Distributors")

        assert call_operation("supplier_list", access(RUTF), {}) == []


class TestFindingTheCompany:
    def test_org_id_names_the_company(self):
        org = LabsOrg.objects.create(slug="sahel-clinics", name="Sahel Clinics")

        made = create(CHC, org_id=org.pk, type="distributor")

        assert made["org_id"] == org.pk
        assert made["name"] == "Sahel Clinics"

    def test_connect_id_names_the_company(self):
        org = LabsOrg.objects.create(slug="sahel-clinics-x", name="Sahel Clinics", connect_organization_id=9101)

        made = create(CHC, name="anything they typed", connect_organization_id=9101)

        assert made["org_id"] == org.pk

    def test_the_exact_name_of_an_organisation_is_that_organisation(self):
        # Recorded under a short slug, so the name's own slug would not find it.
        org = LabsOrg.objects.create(slug="harmattan", name="Harmattan Health Supplies")

        made = create(CHC, name="harmattan health supplies")

        assert made["org_id"] == org.pk
        assert LabsOrg.objects.filter(name__iexact="Harmattan Health Supplies").count() == 1

    def test_an_unknown_company_is_minted_without_a_connect_id(self):
        made = create(CHC, name="Plateau Foods Ltd", country="NG")

        org = LabsOrg.objects.get(pk=made["org_id"])
        assert org.name == "Plateau Foods Ltd"
        assert org.country == "NG"
        assert org.connect_organization_id is None

    def test_same_name_different_connect_identity_is_refused(self):
        LabsOrg.objects.create(slug="plateau-foods", name="Plateau Foods", connect_organization_id=9201)

        with pytest.raises(ValueError, match="already Connect organisation 9201"):
            create(CHC, name="Plateau Foods", connect_organization_id=9202)


class TestEditsLandWhereTheFactLives:
    def test_company_facts_go_to_the_company_and_status_to_the_link(self):
        chc = create(CHC, name="Harmattan Distributors", status="contacted")
        rutf = create(RUTF, name="Harmattan Distributors", status="contacted")

        call_operation(
            "supplier_update",
            access(CHC),
            {"supplier_id": chc["id"], "data": {"city": "Kaduna", "status": "quoting"}},
        )

        seen_by_rutf = call_operation("supplier_get", access(RUTF), {"supplier_id": rutf["id"]})
        assert seen_by_rutf["city"] == "Kaduna"
        assert seen_by_rutf["status"] == "contacted"

    def test_a_company_connect_names_is_not_renamed_here(self):
        org = LabsOrg.objects.create(slug="sahel-clinics", name="Sahel Clinics", connect_organization_id=9301)
        made = create(CHC, org_id=org.pk)

        with pytest.raises(ValueError, match="named by Connect"):
            call_operation("supplier_update", access(CHC), {"supplier_id": made["id"], "data": {"name": "Sahel"}})

    def test_an_unlinked_company_can_be_renamed(self):
        made = create(CHC, name="Plateau Food")

        call_operation("supplier_update", access(CHC), {"supplier_id": made["id"], "data": {"name": "Plateau Foods"}})

        assert LabsOrg.objects.get(pk=made["org_id"]).name == "Plateau Foods"

    def test_binding_to_a_connect_id_another_company_holds_moves_the_supplier(self):
        holder = LabsOrg.objects.create(slug="sahel-clinics", name="Sahel Clinics", connect_organization_id=9401)
        made = create(CHC, name="Sahel (typed by hand)")

        updated = call_operation(
            "supplier_update",
            access(CHC),
            {"supplier_id": made["id"], "data": {"connect_organization_id": 9401}},
        )

        assert updated["id"] == made["id"]
        assert updated["org_id"] == holder.pk

    def test_rebinding_leaves_the_company_it_turned_out_to_be_alone(self):
        # What the edit screen sends: every field, pre-filled from the OLD
        # company. Rebinding must neither refuse on the name nor write the old
        # company's facts over the one it turned out to be.
        holder = LabsOrg.objects.create(slug="sahel-clinics", name="Sahel Clinics", connect_organization_id=9601)
        SupplierProfile.objects.create(org=holder, city="Maiduguri", type="distributor")
        made = create(CHC, name="Sahel (typed by hand)", city="Kano", type="trader")

        updated = call_operation(
            "supplier_update",
            access(CHC),
            {
                "supplier_id": made["id"],
                "data": {
                    "name": "Sahel (typed by hand)",
                    "type": "trader",
                    "city": "Kano",
                    "status": "quoting",
                    "connect_organization_id": 9601,
                },
            },
        )

        assert updated["org_id"] == holder.pk
        assert updated["status"] == "quoting"
        holder.refresh_from_db()
        assert holder.name == "Sahel Clinics"
        assert SupplierProfile.objects.get(org=holder).city == "Maiduguri"

    def test_a_blank_country_on_a_company_connect_names_can_be_filled(self):
        org = LabsOrg.objects.create(slug="sahel-clinics", name="Sahel Clinics", connect_organization_id=9701)
        made = create(CHC, org_id=org.pk)

        call_operation(
            "supplier_update",
            access(CHC),
            {"supplier_id": made["id"], "data": {"name": "Sahel Clinics", "country": "NG"}},
        )

        assert LabsOrg.objects.get(pk=org.pk).country == "NG"

    def test_a_directory_company_is_not_renamed_here(self):
        from connect_labs.marketplace.models import OrgProfile

        org = LabsOrg.objects.create(slug="sahel-clinics", name="Sahel Clinics")
        OrgProfile.objects.create(org=org)
        made = create(CHC, org_id=org.pk)

        with pytest.raises(ValueError, match="LLO directory"):
            call_operation("supplier_update", access(CHC), {"supplier_id": made["id"], "data": {"name": "Sahel"}})

    def test_a_linked_companys_connect_id_does_not_change(self):
        org = LabsOrg.objects.create(slug="sahel-clinics", name="Sahel Clinics", connect_organization_id=9501)
        made = create(CHC, org_id=org.pk)

        with pytest.raises(ValueError, match="does not change"):
            call_operation(
                "supplier_update",
                access(CHC),
                {"supplier_id": made["id"], "data": {"connect_organization_id": 9502}},
            )


class TestTheRecordReadsAsBefore:
    def test_the_published_shape_is_unchanged(self):
        made = create(
            CHC,
            name="Harmattan Distributors",
            type="distributor",
            country="NG",
            city="Kano",
            contacts=[{"name": "A. Bello", "email": "sales@harmattan.example"}],
        )

        assert set(made) == {
            "id",
            "name",
            "type",
            "country",
            "city",
            "status",
            "contacts",
            "qualifications",
            "connect_organization_id",
            "org_id",
            "notes",
            "reference_scope",
        }
        assert made["contacts"] == [{"name": "A. Bello", "email": "sales@harmattan.example"}]

    def test_search_still_finds_by_contact_email(self):
        create(CHC, name="Harmattan Distributors", contacts=[{"email": "sales@harmattan.example"}])

        found = call_operation("supplier_list", access(CHC), {"search": "harmattan.example"})

        assert [s["name"] for s in found] == ["Harmattan Distributors"]
