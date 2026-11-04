"""Plain-language wording on the stock pages (Sophie Feintuch's RUTF feedback, 2026-11-02,
"Stock pages, tab by tab"). Invented data only: this repository is public."""

from pathlib import Path

import pytest
from django.urls import reverse

from connect_labs.supply_chain.models import SupplyPoint

from .test_stock_screens import PROGRAM, scoped, user  # noqa: F401

pytestmark = pytest.mark.django_db

STATIC = Path(__file__).resolve().parents[2] / "static" / "supply_chain"


def test_the_stock_page_explains_its_two_forms_and_which_figure_is_official(scoped):  # noqa: F811
    html = scoped.get(reverse("supply_chain:stock")).content.decode()
    assert "Two ways to record stock" in html
    assert "only if you mark it as an override" in html
    assert "Supply's records are the official figure" in html
    assert "self-report" in html


def test_the_network_labels_a_stores_own_shelf_against_everything_below_it(scoped, monkeypatch):  # noqa: F811
    from connect_labs.supply_chain.network import views as network_views

    monkeypatch.setattr(network_views, "has_program_context", lambda request: True)
    SupplyPoint.objects.create(
        program_id=PROGRAM, slug="cs", name="Central store", kind="central_store", source="we_recorded"
    )
    html = scoped.get(reverse("supply_chain:network")).content.decode()
    assert 'data-testid="network-readings"' in html
    assert "On its own shelf" in html


def test_the_map_says_what_its_colours_and_dots_mean_in_words():
    js = (STATIC / "portfolio_map.js").read_text()
    assert "Overdue, a count mismatch or low stock" in js
    assert "not where they live" in js
    assert "No partner recorded for this place" in js
    assert "pm-attn-empty" in js
