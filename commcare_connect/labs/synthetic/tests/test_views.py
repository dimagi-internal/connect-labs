import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from commcare_connect.labs.synthetic import registry
from commcare_connect.labs.synthetic.models import SyntheticOpportunity


@pytest.fixture(autouse=True)
def _clear_cache():
    registry.invalidate_cache()
    yield
    registry.invalidate_cache()


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="pw")


@pytest.fixture
def authed_client(client, user):
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_list_requires_login(client):
    resp = client.get(reverse("labs:synthetic:list"))
    assert resp.status_code in (302, 403)


@pytest.mark.django_db
def test_list_shows_rows(authed_client):
    SyntheticOpportunity.objects.create(opportunity_id=42, label="Demo A", gdrive_folder_id="folder-a", enabled=True)
    resp = authed_client.get(reverse("labs:synthetic:list"))
    assert resp.status_code == 200
    assert b"Demo A" in resp.content
    assert b"42" in resp.content


@pytest.mark.django_db
def test_create_round_trip(authed_client):
    resp = authed_client.post(
        reverse("labs:synthetic:new"),
        {
            "opportunity_id": 42,
            "label": "New Demo",
            "gdrive_folder_id": "folder-x",
            "enabled": "on",
            "notes": "",
        },
    )
    assert resp.status_code == 302
    assert SyntheticOpportunity.objects.filter(opportunity_id=42).exists()


@pytest.mark.django_db
def test_edit_round_trip(authed_client):
    row = SyntheticOpportunity.objects.create(opportunity_id=42, gdrive_folder_id="f", enabled=True)
    resp = authed_client.post(
        reverse("labs:synthetic:edit", args=[row.pk]),
        {
            "opportunity_id": 42,
            "label": "Updated",
            "gdrive_folder_id": "f",
            "enabled": "on",
            "notes": "changed",
        },
    )
    assert resp.status_code == 302
    row.refresh_from_db()
    assert row.label == "Updated"
    assert row.notes == "changed"


@pytest.mark.django_db
def test_delete(authed_client):
    row = SyntheticOpportunity.objects.create(opportunity_id=42, gdrive_folder_id="f", enabled=True)
    resp = authed_client.post(reverse("labs:synthetic:delete", args=[row.pk]))
    assert resp.status_code == 302
    assert not SyntheticOpportunity.objects.filter(pk=row.pk).exists()


@pytest.mark.django_db
def test_refresh_cache_button(authed_client):
    SyntheticOpportunity.objects.create(opportunity_id=42, gdrive_folder_id="f", enabled=True)
    registry.get_synthetic_opp(42)  # populate cache

    resp = authed_client.post(reverse("labs:synthetic:refresh"))
    assert resp.status_code == 302
    # After refresh, cache is empty (loaded_at reset)
    assert registry._CACHE["loaded_at"] == 0.0
