"""The supplier marketplace has no item of its own in the Labs sidebar (Jonathan, 2026-11-06).

It is reached from the Marketplace menu's Suppliers group and the Supply rail's foot instead.
"""

import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_the_sidebar_has_no_supplier_marketplace_item(client, django_user_model):
    client.force_login(django_user_model.objects.create(username="sidenav-viewer"))
    body = client.get(reverse("solicitations:public_list")).content.decode()
    nav = body[body.index('id="labs-sidenav"') :]
    nav = nav[: nav.index("</nav>")] if "</nav>" in nav else nav[:20000]
    assert ">Supply marketplace<" not in nav
    assert "fa-store" not in nav
    # It is still one click away, in the Marketplace menu.
    assert ">Supplier marketplace<" in body
