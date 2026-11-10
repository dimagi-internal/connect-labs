"""The cost-effectiveness explorer lives at /labs/cea/; its old addresses still work."""

import pytest
from django.urls import reverse


@pytest.fixture
def signed_in(client, django_user_model):
    client.force_login(django_user_model.objects.create_user(username="cea-urls", password="x"))
    return client


def test_the_explorer_has_one_address_space():
    assert reverse("cea:implementation") == "/labs/cea/implementation/"
    assert reverse("cea:implementation_opp", args=[7]) == "/labs/cea/implementation/opp/7/"
    assert reverse("cea:pmc") == "/labs/cea/targeting/pmc/"
    assert reverse("cea:pmc_data") == "/labs/cea/targeting/pmc/api/"
    assert reverse("cea:pmc_run_status", args=[3]) == "/labs/cea/targeting/pmc/api/run/3/"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "old, new",
    [
        ("/labs/cea/", "/labs/cea/implementation/"),
        ("/labs/cea/targeting/", "/labs/cea/targeting/pmc/"),
        ("/labs/pulse/cea/", "/labs/cea/implementation/"),
        ("/labs/pulse/cea/opp/7/", "/labs/cea/implementation/opp/7/"),
        ("/labs/targeting/pmc/", "/labs/cea/targeting/pmc/"),
    ],
)
def test_old_page_addresses_redirect_and_keep_the_query(signed_in, old, new):
    resp = signed_in.get(old + "?states=Benue")
    assert resp.status_code == 302
    assert resp["Location"] == new + "?states=Benue"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "old, new",
    [
        ("/labs/targeting/api/pmc/", "/labs/cea/targeting/pmc/api/"),
        ("/labs/targeting/api/pmc/rank/", "/labs/cea/targeting/pmc/api/rank/"),
        ("/labs/targeting/api/pmc/run/", "/labs/cea/targeting/pmc/api/run/"),
        ("/labs/targeting/api/pmc/run/3/", "/labs/cea/targeting/pmc/api/run/3/"),
    ],
)
def test_old_api_addresses_redirect_with_307_so_a_post_stays_a_post(signed_in, old, new):
    for method in (signed_in.get, signed_in.post):
        resp = method(old + "?x=1")
        assert resp.status_code == 307
        assert resp["Location"] == new + "?x=1"


def test_the_baseline_explorer_stays_where_it_was():
    assert reverse("targeting:index") == "/labs/targeting/"
