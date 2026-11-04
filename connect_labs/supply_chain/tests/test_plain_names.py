"""Two plain names for two different things, and an editable value that looks editable.

"Invite" meant both asking a supplier for a quote by email and giving a supplier access to the marketplace
(Sophie's RUTF feedback, "Structure and wording across the site"). THIS REPOSITORY IS PUBLIC. Every name is invented.
"""

from pathlib import Path

import pytest
from django.urls import reverse

from connect_labs.supply_chain.tests import test_workflow_views as pinned

client_in_programme = pinned.client_in_programme
da = pinned.da

TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "supply_chain"


def test_no_screen_still_calls_asking_for_a_quote_an_invitation():
    # Marketplace access has its own name now; the word "invite" is left to neither.
    offenders = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        if "market" in path.parts or path.name.startswith("market_"):
            continue  # the supplier's own marketplace shell speaks to suppliers
        text = path.read_text()
        for line in text.splitlines():
            visible = line.strip()
            if visible.startswith(("{% comment", "{#")):
                continue
            if (
                "Invite a registered supplier" in visible
                or ">Invite<" in visible
                or "Invite to the marketplace" in visible
            ):
                offenders.append((path.name, visible[:80]))
    assert not offenders, offenders


@pytest.mark.django_db
def test_the_supplier_page_offers_marketplace_access_by_that_name(client_in_programme, da):
    from connect_labs.supply_chain.models import Supplier, scope_key

    supplier = Supplier.objects.enrol(scope_key(program_id=pinned.PROGRAM), name="Northwind Foods")
    body = client_in_programme.get(reverse("supply_chain:supplier_detail", args=[supplier.pk])).content.decode()
    assert "Give marketplace access" in body and "Invite to the marketplace" not in body
    # The status carries its meaning on hover.
    assert "We know they exist" in body


@pytest.mark.django_db
def test_the_sourcing_page_is_called_sourcing_like_its_tab(client_in_programme):
    body = client_in_programme.get(reverse("supply_chain:procurement_tender_board")).content.decode()
    assert "Quote tenders" not in body
    assert ">Sourcing</h1>" in body


@pytest.mark.django_db
def test_the_quote_request_form_is_called_asking_for_a_quote(client_in_programme):
    from connect_labs.supply_chain.models import Tender

    tender = Tender.objects.create(program_id=pinned.PROGRAM, label="Round 1", status="open")
    body = client_in_programme.get(reverse("supply_chain:procurement_outreach_log", args=[tender.pk])).content.decode()
    assert "Ask for a quote" in body and "Record a quote request" not in body


def test_an_editable_value_says_it_can_be_edited():
    from django.template import Context, Template

    html = Template(
        '{% load supply_chain_extras %}<td {% edit_cell "tender" 5 "response_deadline" None %}>not set</td>'
    ).render(Context({}))
    assert 'data-edit="' in html and 'title="Click to edit' in html


def test_the_page_styles_every_editable_value_the_same_way():
    css = (TEMPLATES / "base.html").read_text()
    assert ".supply-page [data-edit]" in css and "underline dotted" in css and "::after" in css
