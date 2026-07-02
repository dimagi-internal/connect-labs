import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.urls import reverse

from connect_labs.users.models import User


@pytest.fixture
def logged_in_client(db):
    user = User.objects.create(username="viewer")
    client = Client()
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "tok"}
    session.save()
    return client


SURFACE = {
    "id": 5,
    "slug": "eha-muac",
    "title": "EHA",
    "options": {},
    "scope": {"type": "opp", "id": 1973},
    "cards": [
        {"provider": "workflow", "target": {"definition_id": 5049}, "options": {}},
        {"provider": "workflow", "target": {"definition_id": 9999}, "options": {}},
    ],
}


@patch("connect_labs.pages.views.resolve_surface")
@patch("connect_labs.pages.views.get_provider")
def test_surface_page_drops_unentitled_cards(mock_get_provider, mock_resolve, logged_in_client):
    mock_resolve.return_value = SURFACE
    provider = MagicMock()
    provider.entitled.side_effect = lambda request, target: target["definition_id"] == 5049
    mock_get_provider.return_value = provider

    resp = logged_in_client.get(reverse("labs:pages:surface", args=["eha-muac"]))
    assert resp.status_code == 200
    assert [c["index"] for c in resp.context["cards"]] == [0]
    assert resp.context["surface"]["slug"] == "eha-muac"


@patch("connect_labs.pages.views.resolve_surface")
def test_surface_page_soft_not_found_renders_switcher(mock_resolve, logged_in_client):
    mock_resolve.return_value = None
    resp = logged_in_client.get(reverse("labs:pages:surface", args=["nope"]))
    assert resp.status_code == 200
    assert b"No page" in resp.content  # not-found copy is rendered in-chrome
    assert resp.templates[0].name == "pages/surface_not_found.html"


@patch("connect_labs.pages.views.resolve_surface")
@patch("connect_labs.pages.views.get_provider")
def test_card_data_returns_payload(mock_get_provider, mock_resolve, logged_in_client):
    mock_resolve.return_value = SURFACE
    provider = MagicMock()
    provider.entitled.return_value = True
    payload = MagicMock()
    payload.to_dict.return_value = {"title": "EHA", "card_type": "summary"}
    provider.get_card_data.return_value = payload
    mock_get_provider.return_value = provider

    resp = logged_in_client.get(reverse("labs:pages:card_data", args=["eha-muac", 0]))
    assert resp.status_code == 200
    assert json.loads(resp.content)["title"] == "EHA"


@patch("connect_labs.pages.views.resolve_surface")
def test_card_data_404_when_surface_unresolved(mock_resolve, logged_in_client):
    mock_resolve.return_value = None
    resp = logged_in_client.get(reverse("labs:pages:card_data", args=["nope", 0]))
    assert resp.status_code == 404


@patch("connect_labs.pages.views.resolve_surface")
@patch("connect_labs.pages.views.get_provider")
def test_card_data_403_when_not_entitled(mock_get_provider, mock_resolve, logged_in_client):
    mock_resolve.return_value = SURFACE
    provider = MagicMock()
    provider.entitled.return_value = False
    mock_get_provider.return_value = provider
    resp = logged_in_client.get(reverse("labs:pages:card_data", args=["eha-muac", 0]))
    assert resp.status_code == 403


@patch("connect_labs.pages.views.resolve_surface")
@patch("connect_labs.pages.views.get_provider")
def test_card_data_404_when_index_out_of_range(mock_get_provider, mock_resolve, logged_in_client):
    mock_resolve.return_value = SURFACE
    resp = logged_in_client.get(reverse("labs:pages:card_data", args=["eha-muac", 99]))
    assert resp.status_code == 404


@patch("connect_labs.pages.views.resolve_surface")
@patch("connect_labs.pages.views.get_provider")
def test_card_data_404_when_unknown_provider(mock_get_provider, mock_resolve, logged_in_client):
    mock_resolve.return_value = SURFACE
    mock_get_provider.return_value = None
    resp = logged_in_client.get(reverse("labs:pages:card_data", args=["eha-muac", 0]))
    assert resp.status_code == 404
