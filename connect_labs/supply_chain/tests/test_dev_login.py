"""The DEBUG-only local sign-in refuses everywhere else.

Worth its own test because the failure mode is an authentication bypass: the
view signs a user in without a password. Two independent guards -- the URL is
only registered under DEBUG, and the view raises Http404 regardless -- and
this pins the second, which is the one that still holds if the first is ever
loosened.
"""

import pytest
from django.http import Http404
from django.test import RequestFactory, override_settings

from connect_labs.supply_chain.dev_views import DEV_USERNAME, dev_login

pytestmark = pytest.mark.django_db


@override_settings(DEBUG=False)
def test_it_refuses_outright_when_debug_is_off():
    with pytest.raises(Http404):
        dev_login(RequestFactory().get("/supply/dev-login/"))


@override_settings(DEBUG=True)
def test_it_refuses_rather_than_creating_a_user():
    """It used to get_or_create a superuser, so one misconfiguration turned an
    unauthenticated GET into full access. Refusing fails closed."""
    from django.contrib.auth import get_user_model

    assert not get_user_model().objects.filter(username=DEV_USERNAME).exists()
    with pytest.raises(Http404, match="supply_dev_seed"):
        dev_login(RequestFactory().get("/supply/dev-login/"))
    assert not get_user_model().objects.filter(username=DEV_USERNAME).exists()


# There is deliberately no test for the OTHER guard -- that the URL is only
# registered when DEBUG. The URLconf is built at import time, so whatever
# DEBUG was when the process started is what decided it, and a test cannot
# re-evaluate that by patching the setting. An earlier version of this file
# pretended to check it with `assert settings.DEBUG or True`, which passes
# unconditionally: a test asserting nothing is worse than no test, because it
# reads as coverage. The view's own Http404, pinned above, is the guard that
# holds either way.
