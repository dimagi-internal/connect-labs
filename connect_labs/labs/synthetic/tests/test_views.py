import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.tests.test_settings import LABS_SETTINGS


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
    session = client.session
    session["labs_oauth"] = {
        "access_token": "tok",
        "organization_data": {"opportunities": [{"id": 42, "name": "Demo A"}]},
    }
    session.save()
    return client


@pytest.fixture
def authed_client_no_context(client, user):
    """Authenticated client with multiple opportunities (disables auto-select) and no context selection."""
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "tok",
        "organization_data": {
            "opportunities": [
                {"id": 42, "name": "Demo A"},
                {"id": 43, "name": "Demo B"},
            ]
        },
    }
    session.save()
    return client


@pytest.fixture
def authed_client_with_context(client, user):
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "tok",
        "organization_data": {"opportunities": [{"id": 42, "name": "Demo A"}]},
    }
    session["labs_context"] = {"opportunity_id": 42}
    session.save()
    return client


@pytest.mark.django_db
def test_list_requires_login(client):
    resp = client.get(reverse("labs:synthetic:list"))
    assert resp.status_code in (302, 403)


@pytest.mark.django_db
def test_list_shows_accessible_row(authed_client):
    SyntheticOpportunity.objects.create(opportunity_id=42, label="Demo A", gdrive_folder_id="folder-a", enabled=True)
    resp = authed_client.get(reverse("labs:synthetic:list"))
    assert resp.status_code == 200
    assert b"Demo A" in resp.content


@pytest.mark.django_db
def test_list_hides_inaccessible_row(authed_client):
    SyntheticOpportunity.objects.create(
        opportunity_id=999, label="Out of reach", gdrive_folder_id="folder-z", enabled=True
    )
    resp = authed_client.get(reverse("labs:synthetic:list"))
    assert resp.status_code == 200
    assert b"Out of reach" not in resp.content


@pytest.mark.django_db
@override_settings(**LABS_SETTINGS)
def test_create_redirects_without_context_opp(authed_client_no_context):
    # authed_client_no_context has multiple opportunities (no auto-select) and NO labs_context selection
    resp = authed_client_no_context.get(reverse("labs:synthetic:new"))
    assert resp.status_code == 302
    assert resp["Location"].endswith(reverse("labs:synthetic:list"))


@pytest.mark.django_db
@override_settings(**LABS_SETTINGS)
def test_create_redirects_when_context_opp_not_accessible(client, user):
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "tok",
        "organization_data": {"opportunities": [{"id": 42, "name": "Demo A"}]},
    }
    session["labs_context"] = {"opportunity_id": 99}
    session.save()

    resp = client.get(reverse("labs:synthetic:new"))
    assert resp.status_code == 302
    assert resp["Location"].endswith(reverse("labs:synthetic:list"))


@pytest.mark.django_db
@override_settings(**LABS_SETTINGS)
def test_create_round_trip_uses_context_opp(authed_client_with_context):
    # opportunity_id is NOT submitted by the client; view derives from labs_context
    resp = authed_client_with_context.post(
        reverse("labs:synthetic:new"),
        {
            "label": "New Demo",
            "gdrive_folder_id": "folder-x",
            "enabled": "on",
            "notes": "",
        },
    )
    assert resp.status_code == 302
    row = SyntheticOpportunity.objects.get(opportunity_id=42)
    assert row.label == "New Demo"
    assert row.gdrive_folder_id == "folder-x"


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


@pytest.mark.django_db
@override_settings(**LABS_SETTINGS)
def test_edit_404_when_opp_inaccessible(authed_client_with_context):
    row = SyntheticOpportunity.objects.create(opportunity_id=999, gdrive_folder_id="f", enabled=True)
    resp = authed_client_with_context.get(reverse("labs:synthetic:edit", args=[row.pk]))
    assert resp.status_code == 404


@pytest.mark.django_db
@override_settings(**LABS_SETTINGS)
def test_delete_404_when_opp_inaccessible(authed_client_with_context):
    row = SyntheticOpportunity.objects.create(opportunity_id=999, gdrive_folder_id="f", enabled=True)
    resp = authed_client_with_context.post(reverse("labs:synthetic:delete", args=[row.pk]))
    assert resp.status_code == 404


@pytest.mark.django_db
@override_settings(**LABS_SETTINGS)
def test_reload_404_when_opp_inaccessible(authed_client_with_context):
    row = SyntheticOpportunity.objects.create(opportunity_id=999, gdrive_folder_id="f", enabled=True)
    resp = authed_client_with_context.post(reverse("labs:synthetic:reload", args=[row.pk]))
    assert resp.status_code == 404


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_edit_cannot_change_opportunity_id(authed_client_with_context):
    row = SyntheticOpportunity.objects.create(opportunity_id=42, gdrive_folder_id="f", enabled=True)
    resp = authed_client_with_context.post(
        reverse("labs:synthetic:edit", args=[row.pk]),
        {
            "opportunity_id": 999,  # attempt to change identity
            "label": "Tampered",
            "gdrive_folder_id": "f",
            "enabled": "on",
            "notes": "",
        },
    )
    assert resp.status_code == 302
    row.refresh_from_db()
    assert row.opportunity_id == 42  # unchanged
    assert row.label == "Tampered"  # other fields still editable


# ── UI polish regressions ────────────────────────────────────────────
@pytest.mark.django_db
def test_list_shows_opportunity_name(authed_client):
    """The list renders the Connect opp name alongside the integer id so users
    can identify rows at a glance."""
    SyntheticOpportunity.objects.create(
        opportunity_id=42, label="Baobab demo", gdrive_folder_id="folder-a", enabled=True
    )
    resp = authed_client.get(reverse("labs:synthetic:list"))
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Demo A" in content  # opp name from fixture seed
    assert "id 42" in content  # id shown as subtitle


@pytest.mark.django_db
def test_list_empty_state_renders_cta(authed_client):
    """With no rows, the list shows the empty-state block and primary CTA."""
    resp = authed_client.get(reverse("labs:synthetic:list"))
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "No synthetic opportunities yet" in content
    assert reverse("labs:synthetic:new") in content


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_create_context_panel_shows_opp_name_and_org(authed_client_with_context):
    """The create form's context panel surfaces the opp name and org from
    labs_oauth so the user confirms they're registering the right opp."""
    resp = authed_client_with_context.get(reverse("labs:synthetic:new"))
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Registering synthetic version of" in content
    assert "Demo A" in content
    assert "id" in content and "42" in content
