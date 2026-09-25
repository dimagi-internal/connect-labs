"""Labs Admin -> Semantic Registries: list, view, and a delete that must name the id."""

import pytest
from django.test import override_settings
from django.urls import reverse

from connect_labs.labs.tests.test_settings import LABS_SETTINGS
from connect_labs.users.models import User
from connect_labs.workflow.data_access import SemanticRegistryRecord


@pytest.fixture
def dimagi_user(db):
    return User.objects.create_user(username="staff", email="staff@dimagi.com", password="pw")


@pytest.fixture
def external_user(db):
    return User.objects.create_user(username="ext", email="partner@external.com", password="pw")


def _record(rid, name, *, opportunity_id=523, public=False):
    return SemanticRegistryRecord(
        {
            "id": rid,
            "experiment": "semantic",
            "type": "semantic_registry",
            "opportunity_id": opportunity_id,
            "public": public,
            "data": {
                "name": name,
                "version": 2,
                "is_shared": public,
                "indicators": {
                    "series": ["KMC"],
                    "measures": [
                        {"name": "mortality", "title": "Mortality", "meta": {"indicator": "mortality", "unit": "%"}},
                        {"name": "mortality_numerator", "type": "count"},
                    ],
                },
                "properties": {"properties": [{"name": "died", "sql": "death_visits > 0"}]},
                "deployment": {"llo_map": {"523": "NAMA"}},
            },
        }
    )


class _FakeAccess:
    """Stands in for SemanticRegistryDataAccess, recording the scope it was opened in."""

    def __init__(self, records, scope):
        self.records = {r.id: r for r in records}
        self.scope = scope
        self.deleted = []
        self.opportunity_id = scope.get("opportunity_id")
        self.program_id = scope.get("program_id")
        self.organization_id = scope.get("organization_id")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_registries(self, include_shared=True):
        return list(self.records.values())

    def get_registry(self, registry_id, **scope):
        return self.records.get(registry_id)

    def delete_registry(self, registry_id):
        self.deleted.append(registry_id)


@pytest.fixture
def fake_access(monkeypatch):
    opened = []
    records = [_record(21931, "SCRATCH #2004 - delete me"), _record(19784, "KMC indicators (live)", public=True)]

    def factory(request, scope):
        access = _FakeAccess(records, scope)
        opened.append(access)
        return access

    monkeypatch.setattr("connect_labs.labs.admin.registry_views._access", factory)
    return opened


@override_settings(**LABS_SETTINGS)
def test_the_list_shows_each_registry_and_where_it_lives(client, dimagi_user, fake_access):
    client.force_login(dimagi_user)
    resp = client.get(reverse("labs_admin:semantic_registries"), {"opportunity_id": "523"})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "SCRATCH #2004 - delete me" in body and "KMC indicators (live)" in body
    assert "opportunity 523" in body
    assert fake_access[0].scope == {"opportunity_id": 523}, "the named scope must be the one read"
    assert reverse("labs_admin:semantic_registry_detail", args=[21931]) + "?opportunity_id=523" in body


@override_settings(**LABS_SETTINGS)
def test_registries_are_admin_only(client, external_user, fake_access):
    client.force_login(external_user)
    assert client.get(reverse("labs_admin:semantic_registries")).status_code == 403
    resp = client.post(reverse("labs_admin:semantic_registry_delete", args=[21931]), {"confirm_id": "21931"})
    assert resp.status_code == 403
    assert not any(a.deleted for a in fake_access)


@override_settings(**LABS_SETTINGS)
def test_the_detail_shows_the_indicators_documents_and_delete_form(client, dimagi_user, fake_access):
    client.force_login(dimagi_user)
    resp = client.get(reverse("labs_admin:semantic_registry_detail", args=[21931]), {"opportunity_id": "523"})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "mortality" in body and "Mortality" in body
    assert "Indicators (Layer 3)" in body and "Deployment facts" in body
    assert 'name="confirm_id"' in body
    assert 'name="opportunity_id" value="523"' in body, "the delete must post the scope the record lives in"


@override_settings(**LABS_SETTINGS)
def test_an_unknown_registry_is_a_404(client, dimagi_user, fake_access):
    client.force_login(dimagi_user)
    resp = client.get(reverse("labs_admin:semantic_registry_detail", args=[1]), {"opportunity_id": "523"})
    assert resp.status_code == 404


@override_settings(**LABS_SETTINGS)
def test_a_delete_without_the_id_typed_back_deletes_nothing(client, dimagi_user, fake_access):
    """Deleting the registry a live report reads takes that report down."""
    client.force_login(dimagi_user)
    for confirm in ("", "yes", "19784"):
        resp = client.post(
            reverse("labs_admin:semantic_registry_delete", args=[21931]),
            {"opportunity_id": "523", "confirm_id": confirm},
        )
        assert resp.status_code == 302
    assert not any(a.deleted for a in fake_access)


@override_settings(**LABS_SETTINGS)
def test_a_confirmed_delete_removes_it_in_its_own_scope(client, dimagi_user, fake_access):
    client.force_login(dimagi_user)
    resp = client.post(
        reverse("labs_admin:semantic_registry_delete", args=[21931]),
        {"opportunity_id": "523", "confirm_id": "21931"},
    )
    assert resp.status_code == 302
    assert resp.url == reverse("labs_admin:semantic_registries") + "?opportunity_id=523"
    [access] = [a for a in fake_access if a.deleted]
    assert access.deleted == [21931]
    assert access.scope == {"opportunity_id": 523}


@override_settings(**LABS_SETTINGS)
def test_a_registry_not_in_the_scope_is_not_deleted(client, dimagi_user, fake_access):
    client.force_login(dimagi_user)
    resp = client.post(
        reverse("labs_admin:semantic_registry_delete", args=[4242]),
        {"opportunity_id": "523", "confirm_id": "4242"},
    )
    assert resp.status_code == 302
    assert not any(a.deleted for a in fake_access)
