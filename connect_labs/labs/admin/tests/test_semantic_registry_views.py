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


def _audit(registry_id, when, action="update"):
    from connect_labs.audit_trail.models import AuditEvent

    AuditEvent.objects.create(
        action=action, resource_type="semantic_registry", resource_id=str(registry_id), occurred_at=when
    )


@override_settings(**LABS_SETTINGS)
def test_the_list_says_when_each_registry_was_last_edited(client, dimagi_user, fake_access):
    """Records written before writes stamped a time still have one: the audit trail."""
    import datetime as dt

    utc = dt.timezone.utc
    _audit(21931, dt.datetime(2026, 9, 24, 21, 40, tzinfo=utc), action="create")
    _audit(19784, dt.datetime(2026, 9, 1, 9, 0, tzinfo=utc))
    _audit(19784, dt.datetime(2026, 9, 24, 22, 10, tzinfo=utc))
    client.force_login(dimagi_user)
    body = client.get(reverse("labs_admin:semantic_registries"), {"opportunity_id": "523"}).content.decode()
    assert "2026-09-24 21:40" in body and "2026-09-24 22:10" in body, "the latest write per registry"
    assert "2026-09-01 09:00" not in body


def test_the_records_own_stamp_wins_when_it_is_later():
    """A write the audit trail missed (or one past its retention) still dates the record."""
    import datetime as dt

    from connect_labs.labs.admin.registry_views import _last_edited

    record = _record(21931, "SCRATCH #2004 - delete me")
    record.data["updated_at"] = "2026-09-25T15:30:00+00:00"
    older_audit = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.timezone.utc)
    assert _last_edited(record, older_audit) == dt.datetime(2026, 9, 25, 15, 30, tzinfo=dt.timezone.utc)
    assert _last_edited(_record(1, "never stamped"), None) is None
