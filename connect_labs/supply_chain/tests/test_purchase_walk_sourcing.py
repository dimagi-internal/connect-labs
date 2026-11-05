"""Round 2, "Sourcing list": the guide tracks the steps per tender and the list says what the Overview says.

Sophie Feintuch's walk of the demo (Nov 2026). THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.supply_chain.guide import guide_tenders, steps_for
from connect_labs.supply_chain.tests import purchase_walk_world as walk
from connect_labs.supply_chain.tests.chlorine_world import PROGRAM, op

pytestmark = pytest.mark.django_db

da = walk.da
account = walk.account
client_in_programme = walk.client_in_programme


def test_a_strict_step_is_ticked_only_when_the_tender_did_it():
    facts = {"suppliers": 1, "tenders": 1, "tenders_open_or_later": 1, "outreach": 3, "quotes": 1}
    steps = {s.key: s for s in steps_for(facts, strict=True)}
    assert not steps["quantities"].done  # nothing was sized
    assert steps["quantities"].next
    assert steps["replies"].done and not steps["compare"].done
    # the programme-wide read still implies it from later steps, as before
    assert {s.key: s for s in steps_for(facts)}["quantities"].done


def test_one_tender_finished_does_not_finish_the_other(da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north", "river", "savanna"))
    for key in ("north", "river", "savanna"):
        walk.deliver(da, world, key)
    op(
        da,
        "tender_create",
        data={
            "label": "Feb re-quote",
            "lines": [{"commodity_slug": "rutf", "quantity": "800", "quantity_unit": "carton"}],
        },
    )
    focus, listing = guide_tenders(PROGRAM)
    assert focus[0].label == "Feb re-quote"
    assert any(s.next for s in focus[1]) and not all(s.done for s in focus[1])
    chips = {c["label"]: c for c in listing}
    assert chips["Demo split tender"]["next"] == ""  # all of its steps done
    assert chips["Feb re-quote"]["current"] and chips["Feb re-quote"]["next"]


def test_a_split_tender_is_not_ordered_or_delivered_until_every_partner_has(da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north", "river"))
    walk.deliver(da, world, "north")
    focus, _ = guide_tenders(PROGRAM, world["tender"]["id"])
    done = {s.key: s.done for s in focus[1]}
    assert done["award"] and not done["order"] and not done["deliveries"]


def test_the_sourcing_page_reads_per_tender_and_shows_the_overview_columns(client_in_programme, da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north", "river"))
    walk.deliver(da, world, "north")
    walk.in_transit(da, world, "river")
    op(
        da,
        "tender_create",
        data={
            "label": "Feb re-quote",
            "lines": [{"commodity_slug": "rutf", "quantity": "800", "quantity_unit": "carton"}],
        },
    )
    response = client_in_programme.get(reverse("supply_chain:procurement_tender_board"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "All 11 steps done" not in body
    assert 'data-testid="guide-tenders"' in body and "Feb re-quote" in body
    for heading in ("Stage", "Quotes in", "Local partners (LLOs)", "Needed by", "Next move"):
        assert heading in body
    assert "Ordered 2 of 3 · Delivered 1 of 3" in body
    assert "To do: Savanna to order" in body
    assert "North Star, Riverbend, Savanna" in body
    assert "RUTF" in body and ">rutf<" not in body
    assert ">Compare<" in body and "Compare rutf" not in body
    # the marketplace is not used, so "Restricted" is not said
    assert "Restricted" not in body
    assert "with a reason" not in body


def test_restricted_is_explained_where_the_marketplace_is_used(client_in_programme, da, monkeypatch):
    from connect_labs.supply_chain.models import Tender

    world = walk.build(da, monkeypatch, ordered=())
    Tender.objects.filter(pk=world["tender"]["id"]).update(visibility="private")
    other = op(
        da,
        "tender_create",
        data={
            "label": "Open round",
            "lines": [{"commodity_slug": "rutf", "quantity": "10", "quantity_unit": "carton"}],
        },
    )
    Tender.objects.filter(pk=other["id"]).update(visibility="public", status="open")
    body = client_in_programme.get(reverse("supply_chain:procurement_tender_board")).content.decode()
    assert "Invited suppliers only" in body and "Only the suppliers you invited" in body
