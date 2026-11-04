"""First-use review of the Supply pages (program 10172): tab names, one purpose line, one heading style.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import re
from pathlib import Path

import pytest
from django.urls import reverse

from connect_labs.supply_chain import navigation
from connect_labs.supply_chain.tests import test_workflow_views as pinned

client_in_programme = pinned.client_in_programme
da = pinned.da
PROGRAM = pinned.PROGRAM
TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "supply_chain"


def test_the_team_tab_is_named_for_the_page_it_opens():
    labels = dict(navigation.SUPPLY_TABS)
    assert labels["supply_chain:programme_team"] == "Team"
    assert "Partner access" not in labels.values()
    assert "update links" in navigation.PAGE_PURPOSE["supply_chain:programme_team"]


@pytest.mark.django_db
def test_the_team_page_says_how_a_partner_gets_access(client_in_programme):
    body = client_in_programme.get(reverse("supply_chain:programme_team")).content.decode()
    assert "<h1" in body and ">Team" in body
    part = body.split('data-testid="team-partner-access"', 1)[1]
    assert "update link" in part and "Connect" in part and "Partners" in part


@pytest.mark.django_db
def test_the_classic_workers_page_names_itself_and_links_back_to_the_pinned_tab(client_in_programme, da):
    pinned._pin(da, replaces="supply_chain:workers")
    body = client_in_programme.get(reverse("supply_chain:workers")).content.decode()
    assert "(classic view)" in body
    note = body.split('data-testid="classic-workers-note"', 1)[1].split("</p>", 1)[0]
    assert f"/supply/views/stock-review/?program_id={PROGRAM}" in note and "Stock review" in note


@pytest.mark.django_db
def test_the_workers_page_is_just_workers_when_nothing_replaces_it(client_in_programme):
    body = client_in_programme.get(reverse("supply_chain:workers")).content.decode()
    assert "classic-workers-note" not in body and "(classic view)" not in body


def test_no_supply_heading_is_drawn_in_the_link_blue():
    """Distribution, Partners, Orders, Issues and Stock headed in indigo (the colour of a link) while Sourcing,
    Catalogue and Alerts were black: every page and section heading is the one dark style."""
    offenders = []
    for path in sorted(TEMPLATES.glob("*.html")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"<h[1-6]\b[^>]*text-brand-indigo", line):
                offenders.append(f"{path.name}:{n}")
    assert offenders == []
