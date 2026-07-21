from unittest.mock import MagicMock

from connect_labs.audit import tasks
from connect_labs.audit.models import AuditSessionRecord


def _fake_data_access(visit_ids, all_visit_images, meta_visits, created_sessions):
    """Builds a MagicMock standing in for AuditDataAccess, configured with just
    enough behavior for run_audit_creation's per-FLW-session path. Anything not
    explicitly set below (e.g. get_audit_creation_job_by_task_id used by the
    progress-tracking helper) auto-mocks harmlessly via MagicMock's defaults."""
    da = MagicMock()
    da.get_visit_ids_for_audit.return_value = visit_ids
    da.extract_images_for_visits.return_value = all_visit_images
    da.get_flw_names.return_value = {}
    da.pipeline.fetch_raw_visits.return_value = meta_visits

    def fake_create_audit_session(**kwargs):
        created_sessions.append(kwargs)
        return AuditSessionRecord(
            {
                "id": len(created_sessions),
                "experiment": "audit",
                "type": "AuditSession",
                "data": {"title": kwargs["title"], "tag": kwargs["tag"]},
                "opportunity_id": kwargs.get("opportunity_id"),
            }
        )

    da.create_audit_session.side_effect = fake_create_audit_session
    return da


def test_run_audit_creation_computes_and_stores_visit_clusters(monkeypatch):
    created_sessions = []
    fake_da = _fake_data_access(
        visit_ids=[111, 112],
        all_visit_images={
            "111": [{"blob_id": "a", "name": "a.jpg", "question_id": "form/muac", "username": "flw1"}],
            "112": [{"blob_id": "b", "name": "b.jpg", "question_id": "form/muac", "username": "flw1"}],
        },
        meta_visits=[
            {"id": "111", "visit_date": "2026-06-22T10:00:00Z", "location": None},
            {"id": "112", "visit_date": "2026-06-22T10:05:00Z", "location": None},
        ],
        created_sessions=created_sessions,
    )
    monkeypatch.setattr(tasks, "AuditDataAccess", MagicMock(return_value=fake_da))

    result = tasks.run_audit_creation.apply(
        kwargs={
            "access_token": "tok",
            "username": "nm1",
            "opportunities": [{"id": 1973, "name": "ISODAF"}],
            "criteria": {
                "audit_type": "date_range",
                "start_date": "2026-06-22",
                "end_date": "2026-06-28",
                "sample_percentage": 100,
                "granularity": "per_flw",
                "tag": "muac",
                "enable_time_gap": True,
                "time_gap_minutes": 10,
            },
        }
    ).result

    assert result["success"] is True
    assert len(created_sessions) == 1
    clusters = created_sessions[0]["visit_clusters"]
    assert clusters == [{"group_id": "g1", "visit_ids": [111, 112], "image_count": 2, "image_ids": ["a", "b"]}]


def test_run_audit_creation_skips_clustering_fetch_when_disabled(monkeypatch):
    created_sessions = []
    fake_da = _fake_data_access(
        visit_ids=[111],
        all_visit_images={"111": [{"blob_id": "a", "name": "a.jpg", "question_id": "form/muac", "username": "flw1"}]},
        meta_visits=[],
        created_sessions=created_sessions,
    )
    monkeypatch.setattr(tasks, "AuditDataAccess", MagicMock(return_value=fake_da))

    tasks.run_audit_creation.apply(
        kwargs={
            "access_token": "tok",
            "username": "nm1",
            "opportunities": [{"id": 1973, "name": "ISODAF"}],
            "criteria": {
                "audit_type": "date_range",
                "start_date": "2026-06-22",
                "end_date": "2026-06-28",
                "sample_percentage": 100,
                "granularity": "per_flw",
                "tag": "muac",
            },
        }
    ).result

    fake_da.pipeline.fetch_raw_visits.assert_not_called()
    assert created_sessions[0]["visit_clusters"] == []
