from connect_labs.audit.data_access import AuditDataAccess
from connect_labs.audit.models import AuditSessionRecord


def test_to_summary_dict_includes_has_ai_reviewer():
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
                "has_ai_reviewer": True,
            },
        }
    )
    assert record.to_summary_dict()["has_ai_reviewer"] is True


def test_to_summary_dict_defaults_has_ai_reviewer_to_false_when_absent():
    """Sessions created before this field existed have no has_ai_reviewer key."""
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
    assert record.to_summary_dict()["has_ai_reviewer"] is False


def _create_session(monkeypatch, **kwargs):
    data_access = AuditDataAccess(access_token="test-token", opportunity_id=1973)
    captured = {}

    def fake_create_record(**call_kwargs):
        captured.update(call_kwargs)

        class FakeRecord:
            id = 42
            experiment = "audit"
            type = "AuditSession"
            data = call_kwargs.get("data", {})
            username = call_kwargs.get("username")
            opportunity_id = call_kwargs.get("opportunity_id")
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
        **kwargs,
    )
    return captured


def test_create_audit_session_stores_has_ai_reviewer_true(monkeypatch):
    captured = _create_session(monkeypatch, has_ai_reviewer=True)
    assert captured["data"]["has_ai_reviewer"] is True


def test_create_audit_session_defaults_has_ai_reviewer_to_false(monkeypatch):
    captured = _create_session(monkeypatch)
    assert captured["data"]["has_ai_reviewer"] is False
