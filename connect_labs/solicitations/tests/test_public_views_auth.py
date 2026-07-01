"""Marketplace views require login.

Anonymous viewers used to be served via `_get_public_data_access`, which fell
back to a CLI token at `~/.commcare-connect/token.json`. That file never
existed on the AWS deployment, and the production data export endpoint always
required `IsAuthenticated + TokenHasScope(['export'])` anyway, so the fallback
was both unreachable and unnecessary. These tests pin the new contract: anon
GET → redirect to `/labs/login/?next=…`.
"""

from urllib.parse import unquote, urlsplit

import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_public_list_redirects_anonymous_to_labs_login(client):
    url = reverse("solicitations:public_list")
    resp = client.get(url)

    assert resp.status_code == 302
    parts = urlsplit(resp["Location"])
    assert parts.path == "/labs/login/"
    # ?next=/solicitations/ — the original URL is preserved so post-login
    # the user lands back on the marketplace.
    assert "next=" in parts.query
    next_param = dict(p.split("=", 1) for p in parts.query.split("&"))["next"]
    assert unquote(next_param) == url


@pytest.mark.django_db
def test_public_detail_redirects_anonymous_to_labs_login(client):
    url = reverse("solicitations:public_detail", kwargs={"pk": 42})
    resp = client.get(url)

    assert resp.status_code == 302
    parts = urlsplit(resp["Location"])
    assert parts.path == "/labs/login/"
    next_param = dict(p.split("=", 1) for p in parts.query.split("&"))["next"]
    assert unquote(next_param) == url
