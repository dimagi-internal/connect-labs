"""The two hand-written scopes, re-expressed through the relationship layer, must return the same rows.

THIS REPOSITORY IS PUBLIC. Every name and id is invented.

`update_links.service._org_contracts` and `market.service._own_quotes` were each
a bespoke query. They now go through `access_relations.rows_for`. The ORIGINAL
queries are copied here verbatim and run beside the new ones on the same
fixtures, for every organisation and every programme, comparing the rows AND the
order. If a later change to a relation path moves either, this is what says so.
"""

import pytest
from django.db.models import Q

from connect_labs.supply_chain.market.service import _own_quotes
from connect_labs.supply_chain.models import Contract, Quote
from connect_labs.supply_chain.tests import access_relations_world as worlds
from connect_labs.supply_chain.update_links.service import _NOT_YET_OR_NO_LONGER, _org_contracts

pytestmark = pytest.mark.django_db


def legacy_org_contracts(program_id, org_id):
    return (
        Contract.objects.filter(program_id=program_id)
        .filter(
            Q(supplier__org_id=org_id) | Q(buyer_org_id=org_id) | Q(delivery_supply_point__managed_by_org_id=org_id)
        )
        .exclude(status__in=_NOT_YET_OR_NO_LONGER)
    )


def legacy_own_quotes(orgs):
    return (
        Quote.objects.filter(supplier__org__in=orgs, entered_by="supplier")
        .select_related("tender", "commodity", "supplier__org", "item")
        .order_by("-created_at")
    )


@pytest.fixture
def w():
    world = worlds.build()
    # A quote a programme team typed in from an email is the programme's record, not the supplier's bid.
    Quote.objects.filter(pk=world.rows["quote_bw"].pk).update(entered_by="program")
    # And an order that is only a draft is no link's to reach.
    Contract.objects.filter(pk=world.rows["contract_cz"].pk).update(status="draft")
    return world


def ordered(queryset):
    return list(queryset.values_list("pk", flat=True))


@pytest.mark.parametrize("program", worlds.PROGRAMMES)
@pytest.mark.parametrize("who", ["X", "Y", "Z", "W", "N"])
def test_org_contracts_returns_the_same_rows_in_the_same_order(w, program, who):
    org_id = w.org(who).pk
    assert ordered(_org_contracts(program, org_id)) == ordered(legacy_org_contracts(program, org_id))


def test_org_contracts_is_not_vacuous(w):
    assert ordered(_org_contracts(worlds.C, w.org("X").pk)) == [w.rows["contract_cx"].pk]
    assert ordered(_org_contracts(worlds.C, w.org("W").pk)) == [w.rows["contract_cx"].pk]  # cz is a draft


@pytest.mark.parametrize("who", [("X",), ("Y",), ("W",), ("N",), ("X", "W"), ()])
def test_own_quotes_returns_the_same_rows_in_the_same_order(w, who):
    orgs = [w.org(k) for k in who]
    assert ordered(_own_quotes(orgs)) == ordered(legacy_own_quotes(orgs))


def test_own_quotes_accepts_a_queryset_of_organisations_as_the_marketplace_passes_it(w):
    from connect_labs.labs.models import LabsOrg

    orgs = LabsOrg.objects.filter(slug__in=["acl-x", "acl-w"])
    assert ordered(_own_quotes(orgs)) == ordered(legacy_own_quotes(orgs))


def test_own_quotes_is_not_vacuous_and_leaves_a_program_entered_quote_out(w):
    assert ordered(_own_quotes([w.org("X")])) == [w.rows["quote_bx"].pk]
    assert ordered(_own_quotes([w.org("W")])) == [w.rows["quote_a"].pk]  # quote_bw was typed in by the program
