"""The Suppliers page says what Organisations is for (Sophie's RUTF feedback, Suppliers)."""

import pytest
from django.urls import reverse

from connect_labs.supply_chain.tests import chlorine_world

pytestmark = pytest.mark.django_db

da = chlorine_world.da
account = chlorine_world.account
client_in_programme = chlorine_world.client_in_programme
world = chlorine_world.world


def test_suppliers_page_says_new_supplier_is_enough(client_in_programme, world):
    body = client_in_programme.get(reverse("supply_chain:suppliers")).content.decode()
    assert 'data-testid="suppliers-vs-organisations"' in body
    assert "finds the company" in body and "partners who buy or hold stock on your behalf" in body


def test_organisations_page_is_where_partners_start(client_in_programme, world):
    body = client_in_programme.get(reverse("supply_chain:organisations")).content.decode()
    assert "Start here for <strong>partners</strong>" in body
    assert "Add a partner or organisation" in body
