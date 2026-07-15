"""Tests for ExperimentBulkAssessmentDataView's bulk_primary_flw_name field.

The bulk assessment page's header used to show the FLW's raw Connect username
(e.g. a connect_id) instead of their display name, because the primary-FLW
summary only threaded through `bulk_primary_username` — unlike the per-assessment
rows, which already resolve names via `flw_names.get(username, username)`.
"""
import time

import pytest
from django.test import Client


@pytest.fixture
def labs_client(db):
    """Django test client with a valid labs session and authenticated user."""
    from connect_labs.users.models import User

    user, _ = User.objects.update_or_create(
        username="testuser",
        defaults={"email": "testuser@example.com"},
    )
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "test-token-abc",
        "expires_at": time.time() + 3600,
        "user_profile": {"username": "testuser", "id": 42, "email": "testuser@example.com"},
    }
    session.save()
    return client


def _make_session(username):
    from connect_labs.audit.models import AuditSessionRecord

    return AuditSessionRecord(
        {
            "id": 5456,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": 1973,
            "labs_record_id": None,
            "data": {
                "opportunity_id": 1973,
                "visit_ids": [111],
                "related_fields": [],
                "visit_results": {},
                "visit_images": {
                    "111": [
                        {
                            "blob_id": "b1",
                            "name": "img1.jpg",
                            "question_id": "form/photo",
                            "username": username,
                            "visit_date": "2026-06-22T10:00:00Z",
                            "entity_name": "Child A",
                        }
                    ]
                },
            },
        }
    )


def test_bulk_primary_flw_name_resolves_via_flw_names(labs_client, monkeypatch):
    from connect_labs.audit import views

    username = "26a4b2fb1c4d2f260c5e"
    session = _make_session(username)

    class FakeDataAccess:
        def __init__(self, *a, **k):
            pass

        def get_audit_session(self, session_id, try_multiple_opportunities=False):
            return session

        def get_opportunity_details(self, opportunity_id):
            return {"name": "EHA-PRE-RCT Connect-CHC 2026"}

        def get_flw_names(self, opportunity_id):
            return {username: "Jane Doe"}

        def get_prior_audited_images(self, opportunity_id, exclude_session_id=None):
            return {}

        def close(self):
            pass

    monkeypatch.setattr(views, "AuditDataAccess", FakeDataAccess)

    response = labs_client.get(f"/audit/api/{session.id}/bulk-data/")
    data = response.json()

    assert data["bulk_primary_username"] == username
    assert data["bulk_primary_flw_name"] == "Jane Doe"


def test_assessment_entity_id_passes_through_when_already_stored(labs_client, monkeypatch):
    """New audit sessions store entity_id directly on each visit_images entry —
    it should reach the assessment unchanged, no backfill needed."""
    from connect_labs.audit import views

    username = "26a4b2fb1c4d2f260c5e"
    session = _make_session(username)
    session.data["visit_images"]["111"][0]["entity_id"] = "ALIYU-20240610"

    class FakeDataAccess:
        def __init__(self, *a, **k):
            pass

        def get_audit_session(self, session_id, try_multiple_opportunities=False):
            return session

        def get_opportunity_details(self, opportunity_id):
            return {"name": "EHA-PRE-RCT Connect-CHC 2026"}

        def get_flw_names(self, opportunity_id):
            return {}

        def get_prior_audited_images(self, opportunity_id, exclude_session_id=None):
            return {}

        def close(self):
            pass

    monkeypatch.setattr(views, "AuditDataAccess", FakeDataAccess)

    response = labs_client.get(f"/audit/api/{session.id}/bulk-data/")
    data = response.json()

    assert data["assessments"][0]["entity_id"] == "ALIYU-20240610"
    assert data["assessments"][0]["prior_audited"] is False


def test_assessment_entity_id_backfills_for_legacy_sessions(labs_client, monkeypatch):
    """Sessions created before entity_id was captured have none stored on
    visit_images. The view should backfill it via a bulk visit fetch, matching
    on visit_id even though the cache backend returns it as a string (CharField)
    while the assessment's visit_id is an int — a mismatch that used to make the
    backfill silently miss every time."""
    from connect_labs.audit import views

    username = "26a4b2fb1c4d2f260c5e"
    session = _make_session(username)  # no entity_id on the stored image

    class FakePipeline:
        def fetch_raw_visits(self, opportunity_id, skip_form_json, filter_visit_ids):
            assert 111 in filter_visit_ids
            return [{"id": "111", "entity_id": "ALIYU-20240610"}]  # string id, like RawVisitCache

    class FakeDataAccess:
        def __init__(self, *a, **k):
            self.pipeline = FakePipeline()

        def get_audit_session(self, session_id, try_multiple_opportunities=False):
            return session

        def get_opportunity_details(self, opportunity_id):
            return {"name": "EHA-PRE-RCT Connect-CHC 2026"}

        def get_flw_names(self, opportunity_id):
            return {}

        def get_prior_audited_images(self, opportunity_id, exclude_session_id=None):
            return {}

        def close(self):
            pass

    monkeypatch.setattr(views, "AuditDataAccess", FakeDataAccess)

    response = labs_client.get(f"/audit/api/{session.id}/bulk-data/")
    data = response.json()

    assert data["assessments"][0]["entity_id"] == "ALIYU-20240610"


def test_bulk_primary_flw_name_falls_back_to_username_when_unresolved(labs_client, monkeypatch):
    """If the FLW name lookup doesn't have an entry, fall back to the raw
    username — same behavior as the per-assessment flw_name field."""
    from connect_labs.audit import views

    username = "26a4b2fb1c4d2f260c5e"
    session = _make_session(username)

    class FakeDataAccess:
        def __init__(self, *a, **k):
            pass

        def get_audit_session(self, session_id, try_multiple_opportunities=False):
            return session

        def get_opportunity_details(self, opportunity_id):
            return {"name": "EHA-PRE-RCT Connect-CHC 2026"}

        def get_flw_names(self, opportunity_id):
            return {}

        def get_prior_audited_images(self, opportunity_id, exclude_session_id=None):
            return {}

        def close(self):
            pass

    monkeypatch.setattr(views, "AuditDataAccess", FakeDataAccess)

    response = labs_client.get(f"/audit/api/{session.id}/bulk-data/")
    data = response.json()

    assert data["bulk_primary_flw_name"] == username


def test_prior_audited_fields_present(labs_client, monkeypatch):
    from connect_labs.audit import views

    username = "26a4b2fb1c4d2f260c5e"
    session = _make_session(username)

    class FakeDataAccess:
        def __init__(self, *a, **k):
            pass

        def get_audit_session(self, session_id, try_multiple_opportunities=False):
            return session

        def get_opportunity_details(self, opportunity_id):
            return {"name": "Readers"}

        def get_flw_names(self, opportunity_id):
            return {}

        def get_prior_audited_images(self, opportunity_id, exclude_session_id=None):
            return {"111:b1": {"result": "fail", "session_id": 9, "session_title": "Old",
                               "completed_at": "2026-05-01T00:00:00Z"}}

        def close(self):
            pass

    monkeypatch.setattr(views, "AuditDataAccess", FakeDataAccess)

    response = labs_client.get(f"/audit/api/{session.id}/bulk-data/")
    a = response.json()["assessments"][0]
    assert a["prior_audited"] is True
    assert a["prior_result"] == "fail"
    assert a["prior_session_date"] == "2026-05-01"
