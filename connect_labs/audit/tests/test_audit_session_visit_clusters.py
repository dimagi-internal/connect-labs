from connect_labs.audit.data_access import AuditDataAccess
from connect_labs.audit.models import AuditSessionRecord


def test_to_summary_dict_includes_visit_clusters():
    record = AuditSessionRecord(
        {
            "id": 5456,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": 1973,
            "username": "testuser",
            "data": {
                "title": "Aisha Ismail",
                "tag": "muac",
                "status": "in_progress",
                "overall_result": None,
                "opportunity_id": 1973,
                "opportunity_name": "ISODAF",
                "description": "",
                "visit_ids": [111, 112],
                "visit_results": {},
                "image_count": 4,
                "flw_username": "flw1",
                "visit_clusters": [{"group_id": "g1", "visit_ids": [111, 112], "image_count": 4}],
            },
        }
    )
    summary = record.to_summary_dict()
    assert summary["visit_clusters"] == [{"group_id": "g1", "visit_ids": [111, 112], "image_count": 4}]


def test_to_summary_dict_defaults_visit_clusters_to_empty_list_when_absent():
    """Sessions created before this feature existed have no visit_clusters key."""
    record = AuditSessionRecord(
        {
            "id": 1,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": 1973,
            "username": "testuser",
            "data": {
                "title": "t",
                "tag": "muac",
                "status": "in_progress",
                "overall_result": None,
                "opportunity_id": 1973,
                "opportunity_name": "",
                "description": "",
                "visit_ids": [],
                "visit_results": {},
                "image_count": 0,
                "flw_username": "flw1",
            },
        }
    )
    assert record.to_summary_dict()["visit_clusters"] == []


def test_create_audit_session_stores_visit_clusters(monkeypatch):
    data_access = AuditDataAccess(access_token="test-token", opportunity_id=1973)
    captured = {}

    def fake_create_record(**kwargs):
        captured.update(kwargs)

        class FakeRecord:
            id = 42
            experiment = "audit"
            type = "AuditSession"
            data = kwargs.get("data", {})
            username = kwargs.get("username")
            opportunity_id = kwargs.get("opportunity_id")
            organization_id = None
            program_id = None
            labs_record_id = None

        return FakeRecord()

    monkeypatch.setattr(data_access.labs_api, "create_record", fake_create_record)
    monkeypatch.setattr(data_access, "get_opportunity_details", lambda opp_id: {"name": "ISODAF"})

    clusters = [{"group_id": "g1", "visit_ids": [111, 112], "image_count": 4}]
    data_access.create_audit_session(
        username="nm1",
        visit_ids=[111, 112],
        title="Aisha Ismail",
        tag="muac",
        opportunity_id=1973,
        visit_images={"111": [{"blob_id": "a"}], "112": [{"blob_id": "b"}]},
        visit_clusters=clusters,
    )

    assert captured["data"]["visit_clusters"] == clusters


def test_create_audit_session_defaults_visit_clusters_to_empty_list(monkeypatch):
    data_access = AuditDataAccess(access_token="test-token", opportunity_id=1973)
    captured = {}

    def fake_create_record(**kwargs):
        captured.update(kwargs)

        class FakeRecord:
            id = 42
            experiment = "audit"
            type = "AuditSession"
            data = kwargs.get("data", {})
            username = kwargs.get("username")
            opportunity_id = kwargs.get("opportunity_id")
            organization_id = None
            program_id = None
            labs_record_id = None

        return FakeRecord()

    monkeypatch.setattr(data_access.labs_api, "create_record", fake_create_record)
    monkeypatch.setattr(data_access, "get_opportunity_details", lambda opp_id: {"name": "ISODAF"})

    data_access.create_audit_session(
        username="nm1",
        visit_ids=[111],
        title="Aisha Ismail",
        tag="muac",
        opportunity_id=1973,
        visit_images={"111": [{"blob_id": "a"}]},
    )

    assert captured["data"]["visit_clusters"] == []
