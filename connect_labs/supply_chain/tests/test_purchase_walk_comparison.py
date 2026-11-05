"""Round 2, "Comparison": plain headers, a key that says only what applies, the warning said once.

Sophie Feintuch's walk of the demo (Nov 2026). THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.supply_chain.tests import purchase_walk_world as walk
from connect_labs.supply_chain.tests.chlorine_world import op

pytestmark = pytest.mark.django_db

da = walk.da
account = walk.account
client_in_programme = walk.client_in_programme


def _page(client, world):
    url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def test_headers_are_plain_and_the_port_and_exchange_columns_are_explained(client_in_programme, da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=())
    body = _page(client_in_programme, world)
    assert "Delivered cost per carton" in body and "Landed per" not in body
    assert "Clearing and handling at the port" in body and "the agent who gets the goods through customs" in body
    assert "Exchange rate to USD" in body and "turns a quote in another currency into dollars" in body


def test_the_key_names_only_the_marks_on_the_grid(client_in_programme, da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=())
    body = _page(client_in_programme, world)
    legend = body.split('data-testid="owner-legend"', 1)[1].split("</span></span></div>", 1)[0]
    # nothing is waiting on a supplier here, so "waiting" is not explained
    assert "on the supplier" not in legend.split("Recorded")[0] or "waiting" not in legend


def test_the_qualification_warning_is_a_line_pointing_back_not_the_whole_banner(client_in_programme, da, monkeypatch):
    op(
        da,
        "commodity_upsert",
        data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton", "category": "therapeutic_food"},
    )
    world = walk.build(da, monkeypatch, ordered=())
    body = _page(client_in_programme, world)
    assert 'data-testid="qualification-gaps-note"' in body and 'data-testid="qualification-gaps"' not in body
    assert "Qualifications to check" not in body
