"""Orders explains why choosing and buying are separate; the Catalogue shows its steps and why no "add batch".

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.supply_chain.tests import test_workflow_views as pinned

client_in_programme = pinned.client_in_programme
da = pinned.da


@pytest.mark.django_db
def test_orders_explains_choosing_versus_buying_up_front(client_in_programme):
    body = client_in_programme.get(reverse("supply_chain:orders")).content.decode()
    assert 'data-testid="orders-intro"' in body
    assert "Choosing a supplier and placing the order are separate steps" in body
    assert "Each order records who bought it" in body
    assert "About orders" not in body  # no longer folded away behind a disclosure


@pytest.mark.django_db
def test_the_catalogue_shows_the_steps_and_why_there_is_no_add_batch(client_in_programme):
    body = client_in_programme.get(reverse("supply_chain:catalogue")).content.decode()
    steps = body.split('data-testid="catalogue-steps"', 1)[1].split("</ol>", 1)[0]
    assert "1. Add a product" in steps and "2. Add a" in steps and "3. Batches are not added here" in steps
    assert "physical delivery" in steps and reverse("supply_chain:stock") in steps
    assert "Nobody can ship you one" not in body
