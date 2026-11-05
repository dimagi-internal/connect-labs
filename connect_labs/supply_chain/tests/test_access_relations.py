"""The relationship layer: who a row is to an organisation, and what that scopes (phase 1, shadow mode).

THIS REPOSITORY IS PUBLIC. Every name and id is invented; see access_relations_world.py.
"""

import pytest

from connect_labs.supply_chain import access_relations as ar
from connect_labs.supply_chain import identity, models
from connect_labs.supply_chain.access_relations import Capability, Relation
from connect_labs.supply_chain.tests import access_relations_world as worlds

pytestmark = pytest.mark.django_db


@pytest.fixture
def w():
    return worlds.build()


class Viewer:
    """An access object that is not the system, not a member of anything, with whatever Connect orgs it is given."""

    def __init__(self, program_id, connect_orgs=(), user=None):
        self.program_id = program_id
        self.connect_orgs = [{"id": i, "slug": f"conn-{i}"} for i in connect_orgs]
        self.user = user
        self.caller = None
        self.request = None


@pytest.fixture(autouse=True)
def connect_orgs_come_from_the_viewer(monkeypatch):
    monkeypatch.setattr(identity, "_caller_organizations", lambda access: getattr(access, "connect_orgs", None))


def pks(queryset):
    return set(queryset.values_list("pk", flat=True))


# ---- relations of one row ---------------------------------------------------


class TestRelationsGiveXTheRightSetPerObjectInEachProgramme:
    def test_in_the_programme_x_runs_it_is_the_runner_of_every_row(self, w):
        x = w.ids("X")
        for key in ("tender_a", "quote_a", "contract_a", "point_a", "supplier_a"):
            assert Relation.RUNNER in ar.relations(x, w.rows[key]), key

    def test_on_the_tender_it_is_invited_to_it_is_an_invited_bidder_and_nothing_else(self, w):
        assert ar.relations(w.ids("X"), w.rows["tender_b"]) == {Relation.INVITED_BIDDER}

    def test_on_its_own_bid_it_is_the_supplier_and_on_a_competitors_bid_nothing(self, w):
        x = w.ids("X")
        assert ar.relations(x, w.rows["quote_bx"]) == {Relation.SUPPLIER}
        assert ar.relations(x, w.rows["supplier_bx"]) == {Relation.SUPPLIER}
        assert ar.relations(x, w.rows["quote_bw"]) == set()
        assert ar.relations(x, w.rows["supplier_bw"]) == set()

    def test_on_programme_c_it_receives_the_order_delivered_to_its_store_and_not_the_other_one(self, w):
        x = w.ids("X")
        assert ar.relations(x, w.rows["contract_cx"]) == {Relation.RECEIVER}
        assert ar.relations(x, w.rows["contract_cz"]) == set()
        assert ar.relations(x, w.rows["store_x"]) == {Relation.POINT_OPERATOR}
        assert ar.relations(x, w.rows["store_z"]) == set()
        assert ar.relations(x, w.rows["receipt_cx"]) == {Relation.RECEIVER}
        assert ar.relations(x, w.rows["movement_cx"]) == {Relation.POINT_OPERATOR}
        assert ar.relations(x, w.rows["shipment_cx"]) == {Relation.RECEIVER}
        assert ar.relations(x, w.rows["shipment_cz"]) == set()

    def test_one_organisation_holds_different_relations_in_different_programmes(self, w):
        x = w.ids("X")
        held = {
            worlds.A: ar.relations(x, w.rows["contract_a"]),
            worlds.B: ar.relations(x, w.rows["contract_b"]),
            worlds.C: ar.relations(x, w.rows["contract_cx"]),
        }
        assert held == {
            worlds.A: {Relation.RUNNER, Relation.BUYER, Relation.RECEIVER},
            worlds.B: {Relation.SUPPLIER},
            worlds.C: {Relation.RECEIVER},
        }

    def test_the_other_roles_in_the_world(self, w):
        assert ar.relations(w.ids("Y"), w.rows["tender_b"]) == {Relation.RUNNER, Relation.TENDER_OWNER}
        assert ar.relations(w.ids("Y"), w.rows["quote_bx"]) == {Relation.RUNNER, Relation.TENDER_OWNER}
        assert ar.relations(w.ids("Z"), w.rows["approval_b"]) == {Relation.APPROVER}
        assert ar.relations(w.ids("Z"), w.rows["award_b"]) == {Relation.APPROVER}
        assert ar.relations(w.ids("Z"), w.rows["contract_cz"]) == {Relation.RUNNER, Relation.BUYER, Relation.RECEIVER}
        assert ar.relations(w.ids("W"), w.rows["charge_cz"]) == {Relation.PAYEE}
        assert ar.relations(w.ids("W"), w.rows["commitment_cw"]) == {Relation.OWED_TO}
        assert ar.relations(w.ids("W"), w.rows["contract_cx"]) == {Relation.SUPPLIER}
        assert ar.relations(w.ids("W"), w.rows["invoice_cz"]) == {Relation.SUPPLIER}

    def test_a_cost_is_not_the_suppliers_to_see(self, w):
        assert Relation.SUPPLIER not in ar.relations(w.ids("W"), w.rows["charge_cz"])

    def test_an_organisation_with_no_relation_holds_none_anywhere(self, w):
        n = w.ids("N")
        for key, row in w.rows.items():
            assert ar.relations(n, row) == set(), key

    def test_it_fails_closed(self, w):
        row = w.rows["quote_bx"]
        assert ar.relations(set(), row) == set()
        assert ar.relations(None, row) == set()
        assert ar.relations(w.ids("X"), None) == set()
        assert ar.relations(w.ids("X"), models.Custodian()) == set()  # unsaved, and not modelled

    def test_several_organisations_union_their_relations(self, w):
        assert ar.relations(w.ids("X", "Y"), w.rows["tender_b"]) == {
            Relation.RUNNER,
            Relation.TENDER_OWNER,
            Relation.INVITED_BIDDER,
        }


class TestCapabilities:
    def test_the_runner_implies_everything(self):
        assert ar.capabilities({Relation.RUNNER}) == set(Capability)
        assert ar.capabilities({Relation.RUNNER, Relation.PAYEE}) == set(Capability)

    def test_nothing_held_allows_nothing(self):
        assert ar.capabilities(set()) == set()

    def test_every_relation_can_at_least_read(self):
        for relation in Relation:
            assert Capability.READ in ar.capabilities({relation}), relation

    def test_only_the_approver_answers_approvals_and_only_the_runner_besides(self):
        answering = {r for r in Relation if Capability.ANSWER_APPROVAL in ar.capabilities({r})}
        assert answering == {Relation.APPROVER, Relation.RUNNER}

    def test_relations_union(self):
        assert ar.capabilities({Relation.APPROVER, Relation.SUPPLIER}) == set(Capability)


# ---- scope ------------------------------------------------------------------


class TestScopeForAPartnerIsOnlyTheirOwnRelationships:
    def test_x_in_the_programme_it_bids_in_sees_its_own_bid_and_the_tender_it_was_invited_to(self, w):
        x = w.ids("X")
        assert pks(ar.scope_for_orgs(x, worlds.B, models.Quote)) == {w.rows["quote_bx"].pk}
        assert pks(ar.scope_for_orgs(x, worlds.B, models.Tender)) == {w.rows["tender_b"].pk}
        assert pks(ar.scope_for_orgs(x, worlds.B, models.Supplier)) == {w.rows["supplier_bx"].pk}
        assert pks(ar.scope_for_orgs(x, worlds.B, models.Contract)) == {w.rows["contract_b"].pk}

    def test_x_in_the_programme_it_receives_in_sees_that_order_its_store_and_the_ledger_at_its_store(self, w):
        x = w.ids("X")
        assert pks(ar.scope_for_orgs(x, worlds.C, models.Contract)) == {w.rows["contract_cx"].pk}
        assert pks(ar.scope_for_orgs(x, worlds.C, models.SupplyPoint)) == {w.rows["store_x"].pk}
        assert pks(ar.scope_for_orgs(x, worlds.C, models.Movement)) == {w.rows["movement_cx"].pk}
        assert pks(ar.scope_for_orgs(x, worlds.C, models.Charge)) == set()
        assert pks(ar.scope_for_orgs(x, worlds.C, models.Commitment)) == set()

    def test_the_runner_organisation_holds_the_whole_programme_even_without_standing(self, w):
        x = w.ids("X")
        for model in (models.Tender, models.Quote, models.Contract, models.SupplyPoint):
            assert pks(ar.scope_for_orgs(x, worlds.A, model)) == pks(ar.programme_rows(model, worlds.A))

    def test_an_organisation_with_no_relation_gets_nothing_in_any_programme(self, w):
        for program in worlds.PROGRAMMES:
            for model in ar.MODELS:
                assert not ar.scope_for_orgs(w.ids("N"), program, model).exists(), (program, model)

    def test_fails_closed_without_organisations_or_a_programme(self, w):
        assert not ar.scope_for_orgs(set(), worlds.B, models.Quote).exists()
        assert not ar.scope_for_orgs(None, worlds.B, models.Quote).exists()
        assert not ar.scope_for_orgs(w.ids("X"), None, models.Quote).exists()
        assert not ar.scope_for_orgs(w.ids("X"), "", models.Quote).exists()

    def test_an_unmodelled_model_is_refused_loudly_not_scoped_to_nothing(self, w):
        with pytest.raises(ar.NotModelled):
            ar.scope_for_orgs(w.ids("X"), worlds.B, models.Custodian)

    @pytest.mark.parametrize("program", [worlds.B, worlds.C])
    @pytest.mark.parametrize("model", sorted(ar.MODELS, key=lambda m: m.__name__), ids=lambda m: m.__name__)
    @pytest.mark.parametrize("who", ["X", "Y", "Z", "W", "N"])
    def test_a_row_is_in_scope_exactly_when_the_viewer_holds_a_relation_to_it(self, w, who, model, program):
        """relations() and scope() are built from the same paths; this keeps them from drifting apart."""
        ids = w.ids(who)
        runner = ar.runner_org_id(program)
        if runner in ids:
            pytest.skip("the runner holds the whole programme; covered above")
        in_scope = pks(ar.scope_for_orgs(ids, program, model))
        related = {obj.pk for obj in ar.programme_rows(model, program) if ar.relations(ids, obj)}
        assert in_scope == related


class TestScopeThroughAnAccessObject:
    @pytest.mark.real_standing
    def test_a_partner_viewer_gets_only_their_relationships(self, w):
        viewer = Viewer(worlds.C, connect_orgs=[501])
        assert ar.viewer_org_ids(viewer) == w.ids("X")
        assert pks(ar.scope(viewer, models.Contract)) == {w.rows["contract_cx"].pk}

    @pytest.mark.real_standing
    def test_a_viewer_whose_organisations_are_unknown_gets_nothing(self, w):
        viewer = Viewer(worlds.C)
        viewer.connect_orgs = None
        assert ar.viewer_org_ids(viewer) == frozenset()
        assert not ar.scope(viewer, models.Contract).exists()

    @pytest.mark.real_standing
    def test_a_viewer_whose_resolution_raises_gets_nothing(self, w, monkeypatch):
        def boom(access):
            raise RuntimeError("connect is down")

        monkeypatch.setattr(identity, "_caller_organizations", boom)
        viewer = Viewer(worlds.C, connect_orgs=[501])
        assert ar.viewer_org_ids(viewer) == frozenset()
        assert not ar.scope(viewer, models.Contract).exists()

    @pytest.mark.real_standing
    @pytest.mark.parametrize("program", [worlds.A, worlds.B, worlds.C])
    @pytest.mark.parametrize(
        "model", [models.Tender, models.Quote, models.Contract, models.SupplyPoint, models.Movement]
    )
    def test_programme_standing_gets_the_same_queryset_as_today(self, w, program, model):
        """The member of the organisation that runs the programme: today's unfiltered programme rows, row for row."""
        runner_connect_id = {worlds.A: 501, worlds.B: 502, worlds.C: 503}[program]
        viewer = Viewer(program, connect_orgs=[runner_connect_id])
        assert pks(ar.scope(viewer, model)) == pks(ar.programme_rows(model, program))
        assert str(ar.scope(viewer, model).query) == str(ar.programme_rows(model, program).query)

    @pytest.mark.real_standing
    def test_operator_standing_gets_the_programme_unfiltered(self, w, django_user_model):
        staff = django_user_model.objects.create_user(
            username="support", email="s@example.org", is_staff=True, password="x"
        )
        viewer = Viewer(worlds.B, user=staff)
        assert pks(ar.scope(viewer, models.Quote)) == pks(ar.programme_rows(models.Quote, worlds.B))

    def test_the_system_caller_gets_the_programme_unfiltered(self, w):
        from connect_labs.labs.access.scopes import SYSTEM
        from connect_labs.supply_chain.data_access import SupplyDataAccess

        da = SupplyDataAccess(program_id=worlds.B, caller=SYSTEM)
        assert pks(ar.scope(da, models.Quote)) == pks(ar.programme_rows(models.Quote, worlds.B))

    def test_a_dimagi_address_is_dimagi_the_organisation(self, w, django_user_model):
        from connect_labs.labs.models import LabsOrg

        dimagi = LabsOrg.objects.create(slug="dimagi", name="Dimagi")
        user = django_user_model.objects.create_user(username="d", email="d@dimagi.com", password="x")
        viewer = Viewer(worlds.B, user=user)
        assert dimagi.pk in ar.viewer_org_ids(viewer)

    def test_a_membership_granted_for_an_organisation_makes_the_user_that_organisation(self, w, django_user_model):
        from connect_labs.marketplace.models import OrgMembership

        user = django_user_model.objects.create_user(username="m", email="m@example.org", password="x")
        org = w.org("N")
        OrgMembership.objects.create(org=org, user=user, role="member", granted_for_org_id=org.pk)
        assert org.pk in ar.viewer_org_ids(Viewer(worlds.B, user=user))
