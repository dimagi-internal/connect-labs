"""Phase 2 widening of the relationship layer: the receiving partner of an order, and points below a managed one.

THIS REPOSITORY IS PUBLIC. Every name and id is invented; see access_relations_world.py.

Owner decisions, 5 Nov: (2) an order's receiving partner (`Contract.partner_org`, and the delivery point's
manager) is a RECEIVER of the order and of the shipments, receipts, charges and invoices that concern it;
(3) a supply point below one a partner manages is that partner's too. Neither may widen what an update link
reaches, so the link path keeps its pre-phase-2 behaviour through `extended=False`.
"""

from datetime import date
from decimal import Decimal

import pytest

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain import access_relations as ar
from connect_labs.supply_chain import models
from connect_labs.supply_chain.access_relations import Relation
from connect_labs.supply_chain.tests import access_relations_world as worlds
from connect_labs.supply_chain.tests.test_access_relations_equivalence import legacy_org_contracts, ordered
from connect_labs.supply_chain.update_links.service import _org_contracts

pytestmark = pytest.mark.django_db


def pks(queryset):
    return set(queryset.values_list("pk", flat=True))


@pytest.fixture
def w():
    """The world, plus: N is the partner the order C-to-Z is FOR (Z buys, the store is Z's, but N is named),
    and a field worker's point below X's clinic store, one below Z's depot, and one managed by W below X's store."""
    world = worlds.build()
    n = world.org("N")
    contract = world.rows["contract_cz"]
    contract.partner_org = n
    contract.save(update_fields=["partner_org"])
    world.rows["worker_x"] = models.SupplyPoint.objects.create(
        program_id=worlds.C,
        name="Worker below X",
        slug="worker-below-x",
        kind="user_held",
        parent=world.rows["store_x"],
        source="we_recorded",
    )
    world.rows["worker_x_deep"] = models.SupplyPoint.objects.create(
        program_id=worlds.C,
        name="Deeper worker below X",
        slug="deeper-below-x",
        kind="user_held",
        parent=world.rows["worker_x"],
        source="we_recorded",
    )
    world.rows["worker_w"] = models.SupplyPoint.objects.create(
        program_id=worlds.C,
        name="Worker managed by W",
        slug="worker-managed-by-w",
        kind="user_held",
        parent=world.rows["store_x"],
        managed_by_org=world.org("W"),
        source="we_recorded",
    )
    world.rows["worker_z"] = models.SupplyPoint.objects.create(
        program_id=worlds.C,
        name="Worker below Z",
        slug="worker-below-z",
        kind="user_held",
        parent=world.rows["store_z"],
        source="we_recorded",
    )
    world.rows["movement_worker_x"] = models.Movement.objects.create(
        program_id=worlds.C,
        kind="consumption",
        occurred_on=date(2026, 10, 4),
        from_supply_point=world.rows["worker_x"],
        commodity=world.rows["contract_cx"].commodity,
        quantity=Decimal("2"),
        quantity_unit="sachet",
        source="we_recorded",
    )
    return world


class TestTheReceivingPartnerOfAnOrderIsItsReceiver:
    def test_it_is_a_receiver_of_the_order_and_everything_under_it_that_concerns_it(self, w):
        n = w.ids("N")
        for key in ("contract_cz", "shipment_cz", "invoice_cz"):
            assert ar.relations(n, w.rows[key]) == {Relation.RECEIVER}, key
        assert ar.relations(n, w.rows["charge_cz"]) == {Relation.RECEIVER}
        assert ar.relations(n, w.rows["contract_cx"]) == set()
        assert ar.relations(n, w.rows["shipment_cx"]) == set()

    def test_a_receipt_against_the_order_is_its_receivers_too(self, w):
        receipt = models.Receipt.objects.create(
            contract=w.rows["contract_cz"],
            supply_point=w.rows["store_z"],
            received_on=date(2026, 10, 5),
            source="we_recorded",
        )
        assert ar.relations(w.ids("N"), receipt) == {Relation.RECEIVER}

    def test_the_scope_of_the_partner_is_exactly_those_rows(self, w):
        n = w.ids("N")
        assert pks(ar.scope_for_orgs(n, worlds.C, models.Contract)) == {w.rows["contract_cz"].pk}
        assert pks(ar.scope_for_orgs(n, worlds.C, models.Shipment)) == {w.rows["shipment_cz"].pk}
        assert pks(ar.scope_for_orgs(n, worlds.C, models.Charge)) == {w.rows["charge_cz"].pk}
        assert pks(ar.scope_for_orgs(n, worlds.C, models.Invoice)) == {w.rows["invoice_cz"].pk}

    def test_a_delivery_point_manager_now_sees_the_charges_and_invoices_of_the_order_it_receives(self, w):
        x = w.ids("X")
        shipment = w.rows["shipment_cx"]
        charge = models.Charge.objects.create(
            shipment=shipment, kind="customs", payee_org=w.org("W"), amount=Decimal("3"), source="we_recorded"
        )
        invoice = models.Invoice.objects.create(contract=w.rows["contract_cx"], source="we_recorded")
        assert ar.relations(x, charge) == {Relation.RECEIVER}
        assert ar.relations(x, invoice) == {Relation.RECEIVER}
        assert charge.pk in pks(ar.scope_for_orgs(x, worlds.C, models.Charge))

    def test_a_supplier_still_does_not_see_a_charge(self, w):
        assert Relation.SUPPLIER not in ar.relations(w.ids("W"), w.rows["charge_cz"])


class TestPointsBelowAManagedPointAreTheManagersToo:
    def test_a_worker_below_the_partners_store_is_the_partners_point_at_any_depth(self, w):
        x = w.ids("X")
        assert ar.relations(x, w.rows["worker_x"]) == {Relation.POINT_OPERATOR}
        assert ar.relations(x, w.rows["worker_x_deep"]) == {Relation.POINT_OPERATOR}
        assert pks(ar.scope_for_orgs(x, worlds.C, models.SupplyPoint)) == {
            w.rows["store_x"].pk,
            w.rows["worker_x"].pk,
            w.rows["worker_x_deep"].pk,
        }

    def test_the_nearest_managing_ancestor_wins(self, w):
        assert ar.relations(w.ids("X"), w.rows["worker_w"]) == set()
        assert ar.relations(w.ids("W"), w.rows["worker_w"]) == {Relation.POINT_OPERATOR}

    def test_another_partners_workers_are_not_ours(self, w):
        assert ar.relations(w.ids("X"), w.rows["worker_z"]) == set()

    def test_the_ledger_at_a_workers_point_is_the_partners_ledger(self, w):
        assert ar.relations(w.ids("X"), w.rows["movement_worker_x"]) == {Relation.POINT_OPERATOR}
        assert w.rows["movement_worker_x"].pk in pks(ar.scope_for_orgs(w.ids("X"), worlds.C, models.Movement))
        assert ar.relations(w.ids("Z"), w.rows["movement_worker_x"]) == {Relation.RUNNER}

    def test_the_descendant_rule_stays_inside_the_programme(self, w):
        assert ar.owned_point_ids(worlds.A, w.ids("X")) == {w.rows["point_a"].pk}
        assert ar.owned_point_ids(worlds.C, w.ids("X")) >= {w.rows["store_x"].pk, w.rows["worker_x"].pk}
        assert ar.owned_point_ids(None, w.ids("X")) == set()
        assert ar.owned_point_ids(worlds.C, set()) == set()

    def test_a_receipt_into_a_workers_point_is_received_by_the_partner(self, w):
        receipt = models.Receipt.objects.create(
            contract=w.rows["contract_cz"],
            supply_point=w.rows["worker_x"],
            received_on=date(2026, 10, 6),
            source="we_recorded",
        )
        assert Relation.RECEIVER in ar.relations(w.ids("X"), receipt)


@pytest.mark.parametrize("program", [worlds.C])
@pytest.mark.parametrize("model", sorted(ar.MODELS, key=lambda m: m.__name__), ids=lambda m: m.__name__)
@pytest.mark.parametrize("who", ["X", "Y", "Z", "W", "N"])
def test_a_row_is_still_in_scope_exactly_when_the_viewer_holds_a_relation_to_it(w, who, model, program):
    """The invariant of phase 1, re-run on a world that has partner_org set and workers below stores."""
    ids = w.ids(who)
    if ar.runner_org_id(program) in ids:
        pytest.skip("the runner holds the whole programme")
    in_scope = pks(ar.scope_for_orgs(ids, program, model))
    related = {obj.pk for obj in ar.programme_rows(model, program) if ar.relations(ids, obj)}
    assert in_scope == related


class TestTheUpdateLinkPathKeepsItsReach:
    """`_org_contracts` is pinned to the original hand-written query; the widening must not move it."""

    @pytest.mark.parametrize("who", ["X", "Y", "Z", "W", "N"])
    def test_org_contracts_equals_the_legacy_query_even_with_partner_org_set(self, w, who):
        org_id = w.org(who).pk
        for program in worlds.PROGRAMMES:
            assert ordered(_org_contracts(program, org_id)) == ordered(legacy_org_contracts(program, org_id))

    def test_the_named_partner_is_not_reached_by_its_link_but_is_reached_by_the_scope(self, w):
        n = w.org("N").pk
        assert ordered(_org_contracts(worlds.C, n)) == []
        assert w.rows["contract_cz"].pk in pks(ar.scope_for_orgs({n}, worlds.C, models.Contract))

    def test_a_workers_point_does_not_widen_what_a_link_reaches_as_receiver(self, w):
        contract = models.Contract.objects.create(
            program_id=worlds.C,
            supplier=w.rows["supplier_c"],
            commodity=w.rows["contract_cx"].commodity,
            buyer_of_record="programme_org",
            buyer_org=w.org("Z"),
            delivery_supply_point=w.rows["worker_x"],
            status="placed",
            source="we_recorded",
        )
        x = w.org("X").pk
        assert contract.pk not in ordered(_org_contracts(worlds.C, x))
        assert contract.pk in pks(ar.scope_for_orgs({x}, worlds.C, models.Contract))

    def test_the_wider_reach_is_the_default_and_the_narrow_one_is_explicit(self, w):
        n = {w.org("N").pk}
        wide = ar.rows_for(models.Contract, n, program_id=worlds.C, relations=(Relation.RECEIVER,))
        narrow = ar.rows_for(models.Contract, n, program_id=worlds.C, relations=(Relation.RECEIVER,), extended=False)
        assert pks(wide) == {w.rows["contract_cz"].pk}
        assert pks(narrow) == set()

    def test_organisation_instances_and_querysets_are_accepted_as_before(self, w):
        orgs = LabsOrg.objects.filter(slug="acl-n")
        assert pks(ar.rows_for(models.Contract, orgs, program_id=worlds.C, relations=(Relation.RECEIVER,))) == {
            w.rows["contract_cz"].pk
        }
