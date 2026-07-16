import csv
import io
import time

import pytest
from django.test import Client


@pytest.fixture
def labs_client(db):
    from connect_labs.users.models import User

    user, _ = User.objects.update_or_create(username="testuser", defaults={"email": "testuser@example.com"})
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "test-token-abc", "expires_at": time.time() + 3600}
    session.save()
    return client


def _make_session():
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
                "visit_ids": [111, 112],
                "visit_clusters": [{"group_id": "g1", "visit_ids": [111, 112], "image_count": 2}],
                "visit_images": {
                    "111": [
                        {
                            "blob_id": "b1",
                            "name": "img1.jpg",
                            "entity_name": "Child A",
                            "visit_date": "Jun 22, 10:00",
                            "username": "flw1",
                        }
                    ],
                    "112": [
                        {
                            "blob_id": "b2",
                            "name": "img2.jpg",
                            "entity_name": "Child B",
                            "visit_date": "Jun 22, 10:05",
                            "username": "flw1",
                        }
                    ],
                },
            },
        }
    )


def test_export_csv_returns_one_row_per_image_in_the_group(labs_client, monkeypatch):
    from connect_labs.audit import views

    session = _make_session()

    class FakeDataAccess:
        def __init__(self, *a, **k):
            pass

        def get_audit_session(self, session_id, try_multiple_opportunities=False):
            return session

        def get_visits_batch(self, visit_ids, opportunity_id):
            return [
                {"id": 111, "user_id": "u-1", "user_visit_id": "uv-111"},
                {"id": 112, "user_id": "u-1", "user_visit_id": "uv-112"},
            ]

        def close(self):
            pass

    monkeypatch.setattr(views, "AuditDataAccess", FakeDataAccess)
    monkeypatch.setattr(
        views,
        "fetch_opportunity_metadata",
        lambda access_token, opportunity_id: {
            "cc_domain": "eha-clinics-reach",
            "raw": {"deliver_app": {"hq_server": {"url": "https://www.commcarehq.org"}}},
        },
    )

    response = labs_client.get("/audit/api/5456/visit-clusters/g1/export.csv")
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"

    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert rows[0] == ["Filename", "Visit Date", "GPS Location", "Beneficiary Name", "Connect Visit URL"]
    assert len(rows) == 3  # header + 2 images
    assert rows[1][0] == "img1.jpg"
    assert rows[1][3] == "Child A"


def test_export_csv_returns_404_for_unknown_group(labs_client, monkeypatch):
    from connect_labs.audit import views

    session = _make_session()

    class FakeDataAccess:
        def __init__(self, *a, **k):
            pass

        def get_audit_session(self, session_id, try_multiple_opportunities=False):
            return session

        def close(self):
            pass

    monkeypatch.setattr(views, "AuditDataAccess", FakeDataAccess)

    response = labs_client.get("/audit/api/5456/visit-clusters/does-not-exist/export.csv")
    assert response.status_code == 404
