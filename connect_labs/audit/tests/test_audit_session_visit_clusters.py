from connect_labs.audit.data_access import AuditCriteria, AuditDataAccess
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


def test_create_audit_session_persists_clustering_criteria_from_an_audit_criteria_object(monkeypatch):
    """Regression: run_audit_creation (tasks.py) always calls create_audit_session
    with an AuditCriteria OBJECT (criteria=audit_criteria), never a raw dict --
    so the dataclass-reconstruction branch of criteria_dict, not the
    isinstance(criteria, dict) branch, is what actually runs in production.
    That branch used to hand-list a fixed set of fields and silently drop
    enable_time_gap/time_gap_minutes/enable_distance/distance_meters, so
    AuditSessionRecord.to_summary_dict()'s visit_clustering_used always read
    as "disabled" even for sessions whose visit_clusters proves clustering
    was genuinely on. Going through create_audit_session for real (not
    hand-constructing session.data["criteria"]) is what catches that -- a
    test that builds the stored dict directly would pass either way."""
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

    audit_criteria = AuditCriteria.from_dict(
        {"enable_time_gap": True, "time_gap_minutes": 10, "enable_distance": True, "distance_meters": 15}
    )
    data_access.create_audit_session(
        username="nm1",
        visit_ids=[111, 112],
        title="Aisha Ismail",
        tag="muac",
        opportunity_id=1973,
        visit_images={"111": [{"blob_id": "a"}], "112": [{"blob_id": "b"}]},
        criteria=audit_criteria,
        visit_clusters=[{"group_id": "g1", "visit_ids": [111, 112], "image_count": 4}],
    )

    stored_criteria = captured["data"]["criteria"]
    assert stored_criteria["enable_time_gap"] is True
    assert stored_criteria["time_gap_minutes"] == 10
    assert stored_criteria["enable_distance"] is True
    assert stored_criteria["distance_meters"] == 15

    # And the full round-trip through to_summary_dict() the FLW breakdown UI reads.
    record = AuditSessionRecord(
        {
            "id": 42,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": 1973,
            "data": captured["data"],
        }
    )
    assert record.to_summary_dict()["visit_clustering_used"] == {
        "enable_time_gap": True,
        "time_gap_minutes": 10,
        "enable_distance": True,
        "distance_meters": 15,
    }
