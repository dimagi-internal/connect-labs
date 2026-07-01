import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.urls import reverse

from commcare_connect.users.models import User


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
    "slug": "prog-25-hub",
    "title": "Hub",
    "options": {},
    "cards": [
        {"provider": "audit", "target": {"opportunity_id": 42}, "options": {}},
        {"provider": "audit", "target": {"opportunity_id": 999}, "options": {}},
    ],
}


@patch("commcare_connect.pages.views.SurfaceDataAccess")
@patch("commcare_connect.pages.views.get_provider")
def test_surface_page_drops_unentitled_cards(mock_get_provider, mock_da_cls, logged_in_client):
    mock_da_cls.return_value.get_surface_by_slug.return_value = SURFACE
    provider = MagicMock()
    provider.entitled.side_effect = lambda request, target: target["opportunity_id"] == 42
    mock_get_provider.return_value = provider

    resp = logged_in_client.get(reverse("labs:pages:surface", args=["prog-25-hub"]))
    assert resp.status_code == 200
    shells = resp.context["cards"]
    assert len(shells) == 1
    assert shells[0]["index"] == 0


@patch("commcare_connect.pages.views.SurfaceDataAccess")
def test_surface_page_404_when_missing(mock_da_cls, logged_in_client):
    mock_da_cls.return_value.get_surface_by_slug.return_value = None
    resp = logged_in_client.get(reverse("labs:pages:surface", args=["nope"]))
    assert resp.status_code == 404


@patch("commcare_connect.pages.views.SurfaceDataAccess")
@patch("commcare_connect.pages.views.get_provider")
def test_card_data_returns_payload(mock_get_provider, mock_da_cls, logged_in_client):
    mock_da_cls.return_value.get_surface_by_slug.return_value = SURFACE
    provider = MagicMock()
    provider.entitled.return_value = True
    payload = MagicMock()
    payload.to_dict.return_value = {"title": "Opp A", "card_type": "audit_summary"}
    provider.get_card_data.return_value = payload
    mock_get_provider.return_value = provider

    resp = logged_in_client.get(reverse("labs:pages:card_data", args=["prog-25-hub", 0]))
    assert resp.status_code == 200
    assert json.loads(resp.content)["title"] == "Opp A"


@patch("commcare_connect.pages.views.SurfaceDataAccess")
@patch("commcare_connect.pages.views.get_provider")
def test_card_data_403_when_not_entitled(mock_get_provider, mock_da_cls, logged_in_client):
    mock_da_cls.return_value.get_surface_by_slug.return_value = SURFACE
    provider = MagicMock()
    provider.entitled.return_value = False
    mock_get_provider.return_value = provider

    resp = logged_in_client.get(reverse("labs:pages:card_data", args=["prog-25-hub", 0]))
    assert resp.status_code == 403


@patch("commcare_connect.pages.views.SurfaceDataAccess")
def test_card_data_404_when_index_out_of_range(mock_da_cls, logged_in_client):
    mock_da_cls.return_value.get_surface_by_slug.return_value = SURFACE

    resp = logged_in_client.get(reverse("labs:pages:card_data", args=["prog-25-hub", 99]))
    assert resp.status_code == 404


@patch("commcare_connect.pages.views.SurfaceDataAccess")
@patch("commcare_connect.pages.views.get_provider")
def test_card_data_404_when_unknown_provider(mock_get_provider, mock_da_cls, logged_in_client):
    mock_da_cls.return_value.get_surface_by_slug.return_value = SURFACE
    mock_get_provider.return_value = None

    resp = logged_in_client.get(reverse("labs:pages:card_data", args=["prog-25-hub", 0]))
    assert resp.status_code == 404
