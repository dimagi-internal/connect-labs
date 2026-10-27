"""The site root sends you into the labs app.

Labs serves no public pages — the marketing site lives in
dimagi-internal/connect-marketing — so `/` is a redirect, not a page. It points at
the overview, which is login-gated, so an anonymous visitor is carried on to the
sign-in page with `?next=` preserved.
"""

import pytest
from django.urls import reverse


def test_root_redirects_to_the_labs_overview(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp["Location"] == "/labs/overview/"


def test_root_is_named_so_it_can_be_reversed(client):
    assert reverse("root") == "/"


@pytest.mark.django_db
def test_an_anonymous_visitor_lands_on_the_sign_in_page(client):
    resp = client.get("/", follow=True)
    final_url, _ = resp.redirect_chain[-1]
    assert final_url == "/labs/login/?next=/labs/overview/"


@pytest.mark.django_db
def test_a_signed_in_visitor_is_not_sent_to_sign_in(client, user):
    client.force_login(user)
    resp = client.get("/", follow=True)
    assert [url for url, _ in resp.redirect_chain] == ["/labs/overview/"]
