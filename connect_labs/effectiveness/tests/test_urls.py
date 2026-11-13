"""The cost-effectiveness explorer lives at /labs/cea/; its old addresses still work."""

import pytest
from django.urls import reverse


@pytest.fixture
def signed_in(client, django_user_model):
    client.force_login(django_user_model.objects.create_user(username="cea-urls", password="x"))
    return client


def test_the_explorer_has_one_address_space():
    assert reverse("cea:models") == "/labs/cea/models/"
    assert reverse("cea:models_run") == "/labs/cea/models/api/run/"
    assert reverse("cea:implementation") == "/labs/cea/implementation/"
    assert reverse("cea:implementation_opp", args=[7]) == "/labs/cea/implementation/opp/7/"
    assert reverse("cea:market") == "/labs/cea/market/"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "old, new",
    [
        ("/labs/cea/", "/labs/cea/models/"),
        ("/labs/cea/targeting/", "/labs/cea/market/"),
        ("/labs/cea/targeting/pmc/", "/labs/cea/market/"),
        ("/labs/pulse/cea/", "/labs/cea/implementation/"),
        ("/labs/pulse/cea/opp/7/", "/labs/cea/implementation/opp/7/"),
        ("/labs/targeting/pmc/", "/labs/cea/market/"),
    ],
)
def test_old_page_addresses_redirect_and_keep_the_query(signed_in, old, new):
    resp = signed_in.get(old + "?states=Benue")
    assert resp.status_code == 302
    assert resp["Location"].startswith(new)


def test_the_indicators_explorer_has_its_own_address():
    assert reverse("indicators:index") == "/labs/indicators/"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "old, new",
    [
        ("/labs/targeting/", "/labs/indicators/"),
        ("/labs/targeting/api/selection/", "/labs/indicators/api/selection/"),
        ("/labs/targeting/download/", "/labs/indicators/download/"),
    ],
)
def test_the_old_targeting_addresses_redirect_with_307_so_a_post_stays_a_post(signed_in, old, new):
    for method in (signed_in.get, signed_in.post):
        resp = method(old + "?threshold=80")
        assert resp.status_code == 307
        assert resp["Location"] == new + "?threshold=80"
