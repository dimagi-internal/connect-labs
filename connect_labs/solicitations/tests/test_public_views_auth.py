"""Who needs a labs login, and who does not.

The directory, a call's page and an organisation's page are public: calls are
sent to organisations outside labs, and nothing private is on these pages.
Responding and everything an owner does need a login, and an anonymous request
is sent to the labs login with `next` pointing back.
"""

from urllib.parse import unquote, urlsplit

import pytest
from django.urls import reverse

from connect_labs.solicitations.tests.factories import make_call, make_org

pytestmark = pytest.mark.django_db


def _assert_login_redirect(resp, url):
    assert resp.status_code == 302
    parts = urlsplit(resp["Location"])
    assert parts.path == "/labs/login/"
    next_param = dict(p.split("=", 1) for p in parts.query.split("&"))["next"]
    assert unquote(next_param) == url


@pytest.fixture
def call():
    return make_call(make_org("owner-org", name="Owner Org"))


def test_directory_is_public(client, call):
    resp = client.get(reverse("solicitations:public_list"))
    assert resp.status_code == 200
    assert call.title in resp.content.decode()


def test_call_page_is_public(client, call):
    resp = client.get(reverse("solicitations:public_detail", kwargs={"pk": call.public_id}))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert call.title in body
    assert "Sign in to respond" in body


def test_org_page_is_public(client, call):
    resp = client.get(reverse("solicitations:org_page", kwargs={"org_slug": "owner-org"}))
    assert resp.status_code == 200
    assert call.title in resp.content.decode()


@pytest.mark.parametrize(
    "name,kwargs",
    [
        ("solicitations:respond", lambda c: {"pk": c.public_id}),
        ("solicitations:manage_list", lambda c: {}),
        ("solicitations:create", lambda c: {}),
        ("solicitations:edit", lambda c: {"pk": c.public_id}),
        ("solicitations:responses_list", lambda c: {"pk": c.public_id}),
        ("solicitations:org_manage", lambda c: {"org_slug": "owner-org"}),
    ],
)
def test_everything_else_needs_a_login(client, call, name, kwargs):
    url = reverse(name, kwargs=kwargs(call))
    _assert_login_redirect(client.get(url), url)
